"""
What to make and buy, how much, and when to start.

Everything here is netting: demand that is dated, supply that is
dated, and the difference between them walked forward a day at a time.
The arithmetic is not hard. Getting the inputs honest is, and that is
where this module spends its words.

**Net by date, not by bucket.** Weekly buckets are the textbook shape
and they are wrong in both directions at once: a receipt on Friday
covers a demand on Monday that it cannot reach, and a demand on Monday
raises no order when Tuesday's receipt would have covered it. Walking
the dates costs no more and answers the question that was asked.

**Everything is netted in the item's stocking unit.** A sales line in
bales, a bill of materials in kilogrammes and a shelf in kilogrammes
are three numbers that must not be added, and the plant's own habit of
quoting fabric in metres and buying it in kilos is how that goes
wrong. Every figure is converted on the way in, once, and the planned
order records which unit it is in.

**A shortage reported on the day it bites is useless**, so every
planned order carries a release date that is its own lead time earlier
— and when that date is already behind us, it stays behind us. Moving
it forward to today would make an impossible plan look feasible, which
is the single most expensive thing a planning system can do.

**Stock that is not on hand is not stock.** On-hand is read from the
ledger rather than from `available_at`, because available subtracts
reservations and the sales orders those reservations are for are
counted here as demand in their own right; netting both would order
the same polymer twice. Batches a mandatory inspection plan has not
passed, and batches that have expired, are taken out instead: they are
owned and valued and no run may draw on them.

**What is not modelled, said out loud.** A purchase order line names
no warehouse until it is received, so a confirmed order counts as
inbound to whichever warehouse is being planned. On one site that is
right. On several it under-reports shortages, and the fix is a
warehouse on the purchase line, not a heuristic here.
"""

import datetime
from collections import defaultdict, namedtuple
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import to_date
from apps.inventory.tracking import TrackingMode, lots_at
from apps.manufacturing.bom import default_bom_for
from apps.manufacturing.orders import WorkOrder, WorkOrderStatus
from apps.quality.release import plan_for, release_status
from apps.quality.models import ReleaseStatus

from .capacity import LoadBook, schedule_make
from .leadtime import (
    buy_lead_days,
    calendar_offset,
    make_lead_days,
    offset,
    plant_calendar,
)
from .levels import level_of, low_level_codes
from .models import (
    DemandSource,
    PlanningAction,
    RescheduleAction,
    SupplySource,
    PlannedDemand,
    PlannedOrder,
    PlannedOrderKind,
    PlanningRun,
    PlanningSettings,
)

ZERO = Decimal("0")
# The precision a planned quantity is stored at. Everything the plan
# says about a quantity — the order, its reasons, the rounding — is
# stated at this one precision so that the three reconcile.
QUANTITY = Decimal("0.0001")

Demand = namedtuple(
    "Demand", "date quantity source sales_order_line work_order parent"
)
Supply = namedtuple("Supply", "date quantity source document movable")
Pegged = namedtuple("Pegged", "supply wanted_on wanted_by used")
Shortage = namedtuple("Shortage", "date quantity rounded pegs")
Message = namedtuple("Message", "action supply wanted_on days quantity because")


def _supply(date, quantity, source=None, document=None, movable=False):
    """
    A quantity arriving on a date, and whether anything can be done
    about the date.

    `movable` is the whole reason supply carries its document at all.
    A confirmed purchase order dated a fortnight after the run that
    needs it can be pulled in by ringing the vendor; the reground trim
    a run throws off cannot be pulled in at all, because it comes off
    when the run comes off. Telling a planner to expedite a by-product
    is telling them to expedite something that does not exist yet.
    """
    return Supply(
        date=date, quantity=quantity, source=source, document=document,
        movable=movable,
    )


def _demand(date, quantity, source, sales_order_line=None, work_order=None, parent=None):
    return Demand(
        date=date, quantity=quantity, source=source,
        sales_order_line=sales_order_line, work_order=work_order, parent=parent,
    )


# -- what is on the shelf ------------------------------------------------


def unusable_on_hand(item, warehouse, on_date):
    """
    Stock that is here, is ours, and may not be drawn on.

    Held and expired batches both. A roll waiting to be re-wound is not
    a shortage cover, however likely it is to pass the second time, and
    a plan that counts it will report no shortage right up to the
    morning somebody tries to issue it.
    """
    if item.tracking == TrackingMode.NONE:
        return ZERO
    inspected = plan_for(item)
    mandatory = inspected is not None and inspected.is_mandatory
    total = ZERO
    for lot, quantity in lots_at(item, warehouse):
        if quantity <= 0:
            continue
        if lot.has_expired(on_date):
            total += quantity
        elif mandatory and release_status(lot) != ReleaseStatus.RELEASED:
            total += quantity
    return total


def opening_balance(item, warehouse, on_date):
    """What a run released today could actually draw on."""
    return (
        Decimal(item.on_hand_at(warehouse))
        - unusable_on_hand(item, warehouse, on_date)
    )


# -- what has been promised ----------------------------------------------


def sales_demand(item, warehouse, on_date):
    """
    Confirmed customer lines not yet shipped.

    Confirmed only. A quotation is not a promise, and a plan that
    treats every draft as one buys polymer against orders that were
    never placed — which is the failure mode that makes plants stop
    trusting the planning module and start keeping the real list in a
    spreadsheet.
    """
    from apps.sales.models import OrderStatus, SalesOrderLine

    rows = []
    lines = (
        SalesOrderLine.objects.filter(
            item=item, order__status=OrderStatus.CONFIRMED, charge__isnull=True
        )
        .filter(warehouse__in=[warehouse, None])
        .select_related("order", "item", "uom")
    )
    for line in lines:
        remaining = (
            line.quantity_in_stock_units() - line.quantity_shipped_in_stock_units()
        )
        if remaining <= 0:
            continue
        rows.append(_demand(
            line.promised_date(), remaining, DemandSource.SALES,
            sales_order_line=line,
        ))
    return rows


LIVE = (WorkOrderStatus.DRAFT, WorkOrderStatus.RELEASED)


def live_work_orders(warehouse):
    """
    Runs that are going to happen and have not finished happening.

    Draft as well as released, which is the decision worth stating. A
    draft run's requirements are still an opinion — release is what
    freezes them — and for a while this counted only released ones for
    exactly that reason. That was wrong, and wrong in the expensive
    direction: firming a plan produces draft runs, so a planner who
    firmed on Monday and re-planned on Tuesday was handed the whole of
    Monday's plan a second time. A run nobody has released is still a
    run somebody has decided on, and planning as though it did not
    exist orders its material twice.

    Cancelled and closed runs are out. A cancelled run will not happen;
    a closed one already did, and what it made is on the shelf.
    """
    return (
        WorkOrder.objects.filter(status__in=LIVE, warehouse=warehouse)
        .select_related("item", "uom", "bom")
    )


def work_order_demand(item, warehouse, on_date):
    """
    What live runs still have to draw, over and above what they have
    already drawn.

    A released run is read from its own frozen components, because that
    is what it will actually consume whatever the specification says
    now. A draft run has no components yet, so it is exploded from its
    bill of materials — an estimate, and the honest one: the
    alternative is to plan as though the run needed nothing.
    """
    rows = []
    for order in live_work_orders(warehouse):
        when = order.scheduled_start or on_date
        if order.status == WorkOrderStatus.RELEASED:
            for component in order.components.filter(item=item).select_related(
                "item", "uom"
            ):
                required = component.item.to_stock_quantity(
                    component.quantity_required, component.uom
                )
                remaining = required - component.quantity_issued()
                if remaining > 0:
                    rows.append(_demand(
                        when, remaining, DemandSource.WORK_ORDER,
                        work_order=order,
                    ))
            continue
        # No guard for a run with no bill of materials: `WorkOrder.bom`
        # is not nullable, so a skip here would be a branch nothing can
        # reach — and a silent one, which is how a plan comes to think
        # a run's material is free.
        scale = order.bom.scale_for(order.quantity_ordered, order.uom)
        for component in order.bom.components.filter(item=item).select_related(
            "item", "uom"
        ):
            required = component.item.to_stock_quantity(
                component.gross_quantity() * scale, component.uom
            )
            if required > 0:
                rows.append(_demand(
                    when, required, DemandSource.WORK_ORDER, work_order=order,
                ))
    return rows


# -- what is already coming ----------------------------------------------


def purchase_supply(item, warehouse, on_date):
    """Confirmed purchase lines not yet received."""
    from apps.purchasing.models import OrderStatus, PurchaseOrderLine

    rows = []
    lines = (
        PurchaseOrderLine.objects.filter(
            item=item, order__status=OrderStatus.CONFIRMED, charge__isnull=True
        )
        .select_related("order", "item", "uom")
    )
    for line in lines:
        remaining = line.item.to_stock_quantity(
            line.quantity - line.quantity_received(), line.uom or line.item.uom
        )
        if remaining <= 0:
            continue
        rows.append(_supply(
            line.expected_date or on_date, remaining,
            source=SupplySource.PURCHASE, document=line, movable=True,
        ))
    return rows


def work_order_supply(item, warehouse, on_date):
    """
    What live runs still have to deliver, output and by-product alike.

    The by-product is not a rounding detail here. An extrusion run
    throws off reground trim by the hundredweight and that regrind is
    an input to the next run; a plan that ignores it goes out and buys
    the plant's own scrap. It is an expectation rather than a fact —
    the run has not made it yet — and it is scaled off the bill of
    materials in exactly the proportion the finished output is short
    by.
    """
    rows = []
    for order in live_work_orders(warehouse):
        outstanding = order.quantity_ordered - order.quantity_produced()
        if outstanding <= 0:
            continue
        when = order.scheduled_end or on_date
        if order.item_id == item.pk:
            rows.append(_supply(
                when, order.item.to_stock_quantity(outstanding, order.uom),
                source=SupplySource.WORK_ORDER, document=order, movable=True,
            ))
        rows.extend(byproduct_supply(order.bom, outstanding, order.uom, item, when))
    return rows


def byproduct_supply(bom, quantity, uom, item, when):
    """What `quantity` off this bill of materials throws off of `item`."""
    if bom is None:
        return []
    rows = []
    scale = bom.scale_for(quantity, uom)
    for byproduct in bom.byproducts.filter(item=item).select_related("item", "uom"):
        expected = byproduct.item.to_stock_quantity(
            byproduct.quantity * scale, byproduct.uom
        )
        if expected > 0:
            # Never movable. Trim comes off when the run comes off.
            rows.append(_supply(
                when, expected, source=SupplySource.BYPRODUCT, document=bom,
            ))
    return rows


def requisition_supply(item, warehouse, on_date):
    """
    What has been asked for and not yet ordered.

    A requisition is not a commitment — no vendor has agreed to
    anything — and for that reason it very nearly did not count here.
    It has to, for the same reason a draft work order does: firming a
    planned buy raises one, and a plan that cannot see its own output
    proposes the same purchase every morning until somebody stops
    reading it.

    Counted per line and net of what has already been turned into a
    purchase order, because a requisition split across two vendors is
    part ordered and part not, and the ordered part is already counted
    as a confirmed purchase.
    """
    from apps.purchasing.models import PurchaseRequisitionLine, RequisitionStatus

    dead = (RequisitionStatus.REJECTED, RequisitionStatus.CANCELLED)
    rows = []
    lines = (
        PurchaseRequisitionLine.objects.filter(item=item)
        .exclude(requisition__status__in=dead)
        .select_related("requisition", "item", "uom")
    )
    for line in lines:
        remaining = line.item.to_stock_quantity(
            line.quantity - line.quantity_ordered(), line.uom or line.item.uom
        )
        if remaining <= 0:
            continue
        rows.append(_supply(
            line.requisition.needed_by or on_date, remaining,
            source=SupplySource.REQUISITION, document=line, movable=True,
        ))
    return rows


def safety_stock(item, warehouse):
    """
    The floor the plan keeps under this item, read from its reorder
    rule.

    The rule's order point is the floor and its target is ignored,
    which is a decision worth arguing with. Ordering up to a target is
    what you do when you cannot see the future: it buys cover for
    demand you have not been told about. Here the demand is in front of
    us and dated, and ordering to a target on top of it buys stock
    nothing has asked for and nothing will ask for. The floor is kept
    because a floor answers a different question — how much lateness
    and how much scrap the plant is willing to absorb without stopping.
    """
    from apps.purchasing.models import ReorderRule

    rule = ReorderRule.objects.filter(
        item=item, warehouse=warehouse, is_active=True
    ).first()
    return (rule.minimum if rule else ZERO), rule


def lot_size(quantity, rule):
    """
    Round up to a whole bag, pallet or minimum order, and to the
    precision the planned order is actually stored at.

    Stored precision matters more than it looks. A requirement carried
    at twenty decimal places and saved into four is exploded at one
    number and read back at another, and the components of the run a
    planner firms then disagree with the components the plan showed
    them. Rounded up rather than to nearest, because a requirement
    shaved by a ten-thousandth is a shortage.
    """
    if rule is not None and rule.multiple_of:
        multiples = (quantity / rule.multiple_of).to_integral_value(
            rounding=ROUND_CEILING
        )
        quantity = multiples * rule.multiple_of
    return quantity.quantize(QUANTITY, rounding=ROUND_CEILING)


# -- the netting itself --------------------------------------------------


def _apportion(rows, shortfall):
    """
    Split a day's shortage across the documents due that day, in
    proportion to what each of them asked for, plus whatever is left
    over for the safety floor to own.

    Pro rata rather than first-come-first-served, and the difference
    is not academic. Serving the list in order pegs the shortage to
    whichever line the query happened to return first — so a run that
    exists because a cement customer is short reads as belonging to a
    fertiliser customer whose stock was already on the shelf. Nothing
    in the order book makes one of two lines due the same morning
    senior to the other, so both are short, in proportion.

    Each share is capped at what that document asked for. A shortage
    driven past the whole day's demand can only have been driven there
    by the floor, so that excess is returned separately rather than
    inflating a customer's share — and it is returned as the floor's
    own figure, never as a catch-all for rounding, because a peg that
    says "to hold safety stock" where no safety stock is held is a
    sentence the system made up.

    Shares round down to the precision they are stored at. Rounded up
    they would sum past the order they explain, and a set of reasons
    claiming more than the order is worse than no reasons at all; what
    the rounding drops is reported on the order as rounding.
    """
    total = sum((row.quantity for row in rows), ZERO)
    floor_share = max(shortfall - total, ZERO).quantize(
        QUANTITY, rounding=ROUND_DOWN
    )
    if total <= 0:
        return [], floor_share
    covered = min(shortfall, total)
    pegs = []
    taken = ZERO
    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            share = covered - taken
        else:
            share = row.quantity * covered / total
        share = share.quantize(QUANTITY, rounding=ROUND_DOWN)
        if share <= 0:
            continue
        pegs.append((row, share))
        taken += share
    return pegs, floor_share


def net(opening, safety, demands, supplies, planned_on, rule=None):
    """
    Walk the dates and say where the balance goes under the floor.

    Events dated before the plan's own date are pulled forward to it: a
    receipt that was due last week and has not arrived is still
    expected, and a customer line that went past its date is still
    owed. Neither becomes less real for being late, and dropping either
    would make the plan quietly disagree with the order book.

    Supply lands before demand on a date it shares, because a delivery
    on the morning it is wanted does cover it, and a planner told
    otherwise raises an order for material sitting on the dock.
    """
    buckets = defaultdict(lambda: {"supply": ZERO, "demand": []})
    for row in supplies:
        buckets[max(row.date, planned_on)]["supply"] += row.quantity
    for row in demands:
        buckets[max(row.date, planned_on)]["demand"].append(row)
    if safety > 0:
        # With no demand at all, nothing would ever check the balance
        # against the floor, and an item sitting below its safety level
        # would be reported as fine because nobody had asked for it.
        buckets[planned_on]

    balance = opening
    shortages = []
    for when in sorted(buckets):
        bucket = buckets[when]
        balance += bucket["supply"]
        for row in bucket["demand"]:
            balance -= row.quantity
        if balance >= safety:
            continue
        shortfall = safety - balance
        pegs, floor_share = _apportion(bucket["demand"], shortfall)
        if floor_share > 0:
            # What is left over is the floor asking, not a document.
            pegs.append(
                (_demand(when, floor_share, DemandSource.SAFETY), floor_share)
            )
        quantity = lot_size(shortfall, rule)
        # Whatever rounding up added over and above the reasons. The
        # three figures are stated at one precision on purpose: the
        # pegs plus this must come to the order exactly, or a planner
        # reading the reasons is reading a different order.
        rounded = quantity - sum((taken for _row, taken in pegs), ZERO)
        shortages.append(Shortage(
            date=when, quantity=quantity, rounded=rounded, pegs=pegs,
        ))
        balance += quantity
    return shortages


# -- what is dated wrong -------------------------------------------------


def peg_supply(opening, safety, demands, supplies, planned_on):
    """
    Match what is coming to the demand it ends up covering.

    Earliest supply to earliest demand, which is what a store does:
    the polymer already on the shelf goes into the first run that
    wants it, and the order arriving next goes into the one after.
    Nothing here cares whether the dates line up — that is the point.
    A demand on the tenth served by an order arriving on the twentieth
    is exactly the case worth reporting, and pegging by date first and
    reading the mismatch afterwards is what finds it.

    What comes back is, for each supply row, the date of the first
    demand it serves and how much of it is spoken for. A row that
    serves nothing is covering nothing, which is its own kind of news.
    """
    queue = [[planned_on, opening, None]]
    for row in sorted(supplies, key=lambda r: (r.date, r.quantity)):
        queue.append([max(row.date, planned_on), row.quantity, row])

    wanted_on = {}
    used = defaultdict(lambda: ZERO)
    cursor = 0
    for demand in sorted(demands, key=lambda d: d.date):
        outstanding = demand.quantity
        when = max(demand.date, planned_on)
        while outstanding > 0 and cursor < len(queue):
            slot = queue[cursor]
            if slot[1] <= 0:
                cursor += 1
                continue
            take = min(slot[1], outstanding)
            slot[1] -= take
            outstanding -= take
            row = slot[2]
            if row is None:
                continue
            used[id(row)] += take
            wanted_on.setdefault(id(row), (when, demand))
        # Demand left over is a shortage, which `net` answers with a
        # planned order. It is not this function's business.

    spare = [
        (slot[2], slot[1]) for slot in queue[1:] if slot[1] > 0
    ]
    return [
        Pegged(
            supply=row,
            wanted_on=(wanted_on.get(id(row)) or (None, None))[0],
            wanted_by=(wanted_on.get(id(row)) or (None, None))[1],
            used=used[id(row)],
        )
        for row in supplies
    ], _uncovered(spare, safety)


def _uncovered(spare, safety):
    """
    How much of what is coming nothing is asking for.

    The safety floor comes off first and the latest arrivals are the
    ones left holding it. Stock kept deliberately is not stock nobody
    wants, and a plan that told a buyer to cancel the order that keeps
    the floor would spend the next month telling them to raise it
    again.
    """
    floor = safety
    surplus = []
    for row, quantity in sorted(
        spare, key=lambda pair: pair[0].date if pair[0] else None, reverse=True
    ):
        if row is None:
            continue
        held = min(floor, quantity)
        floor -= held
        if quantity - held > 0:
            surplus.append((row, quantity - held))
    return surplus


def reschedule(item, warehouse, opening, safety, demands, supplies, planned_on,
               tolerance):
    """
    Every order that exists, is movable, and is dated wrong.

    Only movable supply. A by-product comes off when its run comes
    off, and a planned order from this very run was dated by this very
    run, so neither has a date anybody can act on.
    """
    pegs, surplus = peg_supply(opening, safety, demands, supplies, planned_on)
    spare = {id(row): quantity for row, quantity in surplus}
    messages = []
    for peg in pegs:
        row = peg.supply
        if not row.movable:
            continue
        over = spare.get(id(row), ZERO)
        if over > 0:
            # Reported whether or not the rest of the order is spoken
            # for. A twenty-tonne order of which six tonnes is wanted
            # and fourteen is not has two separate things wrong with
            # it, and saying only that the six should move leaves the
            # fourteen on a lorry.
            messages.append(Message(
                action=RescheduleAction.CANCEL, supply=row, wanted_on=None,
                days=0, quantity=over,
                because=(
                    "nothing in the horizon wants it" if peg.used <= 0
                    else f"the other {_show(peg.used)} of it is spoken for"
                ),
            ))
        if peg.wanted_on is None:
            continue
        scheduled = max(row.date, planned_on)
        days = abs((scheduled - peg.wanted_on).days)
        if days <= tolerance:
            continue
        action = (
            RescheduleAction.EXPEDITE if peg.wanted_on < scheduled
            else RescheduleAction.DEFER
        )
        messages.append(Message(
            action=action, supply=row, wanted_on=peg.wanted_on, days=days,
            quantity=peg.used, because=_why(peg.wanted_by),
        ))
    return messages


def _why(demand):
    """The demand that sets the wanted date, named rather than dated."""
    if demand is None:
        return "nothing"
    if demand.source == DemandSource.SALES and demand.sales_order_line:
        line = demand.sales_order_line
        return f"{line.order.number or 'a draft order'} wants it by {demand.date}"
    if demand.source == DemandSource.WORK_ORDER and demand.work_order:
        return f"{demand.work_order} draws it on {demand.date}"
    if demand.source == DemandSource.PLANNED and demand.parent:
        return f"planned {demand.parent} draws it on {demand.date}"
    if demand.source == DemandSource.SAFETY:
        return f"the safety floor wants it held from {demand.date}"
    return f"demand on {demand.date}"


# -- the run -------------------------------------------------------------


def _show(quantity):
    """A quantity as the plan states it, not as the arithmetic left it."""
    return quantity.quantize(QUANTITY, rounding=ROUND_CEILING)


def _candidates(warehouse, planned_on, horizon_end):
    """
    Items something has asked for or something is bringing, before any
    explosion.

    Supply as well as demand, which it did not used to be. An item
    nobody wants and a vendor is shipping anyway never reached the
    planner at all — so the order nothing needs, which is the single
    most expensive thing an order can be, was the one case the plan
    could not see. A shortage announces itself through the item that
    is short; a surplus has nothing to announce it but the surplus.
    """
    from apps.manufacturing.orders import WorkOrderComponent
    from apps.purchasing.models import OrderStatus as PurchaseStatus
    from apps.purchasing.models import (
        PurchaseOrderLine,
        PurchaseRequisitionLine,
        ReorderRule,
        RequisitionStatus,
    )
    from apps.sales.models import OrderStatus, SalesOrderLine

    items = {}
    for line in SalesOrderLine.objects.filter(
        item__isnull=False, order__status=OrderStatus.CONFIRMED, charge__isnull=True
    ).filter(warehouse__in=[warehouse, None]).select_related("item", "order"):
        if line.promised_date() <= horizon_end:
            items[line.item_id] = line.item
    for component in WorkOrderComponent.objects.filter(
        work_order__status__in=LIVE, work_order__warehouse=warehouse,
    ).select_related("item"):
        items[component.item_id] = component.item
    for order in WorkOrder.objects.filter(
        status=WorkOrderStatus.DRAFT, warehouse=warehouse, bom__isnull=False
    ).select_related("bom"):
        for component in order.bom.components.select_related("item"):
            items[component.item_id] = component.item
    for rule in ReorderRule.objects.filter(
        warehouse=warehouse, is_active=True, minimum__gt=0
    ).select_related("item"):
        items[rule.item_id] = rule.item
    for line in PurchaseOrderLine.objects.filter(
        order__status=PurchaseStatus.CONFIRMED, charge__isnull=True,
        item__isnull=False,
    ).select_related("item"):
        items[line.item_id] = line.item
    dead = (RequisitionStatus.REJECTED, RequisitionStatus.CANCELLED)
    for line in PurchaseRequisitionLine.objects.exclude(
        requisition__status__in=dead
    ).select_related("item"):
        items[line.item_id] = line.item
    for order in WorkOrder.objects.filter(
        status__in=LIVE, warehouse=warehouse
    ).select_related("item"):
        items[order.item_id] = order.item
    return items


def _make_or_buy(item):
    """
    An item with a default bill of materials is made; everything else
    is bought.

    The same rule `explode()` already uses to decide what is a leaf, so
    the plan and the explosion cannot disagree about which items the
    plant produces. A separate make-or-buy flag on the item would be a
    second answer to a question already answered, and the two would
    drift.
    """
    bom = default_bom_for(item)
    return (PlannedOrderKind.MAKE, bom) if bom else (PlannedOrderKind.BUY, None)


@transaction.atomic
def plan(warehouse, planned_on=None, horizon_days=None, settings=None):
    """
    One pass of the planner. Returns the `PlanningRun` it wrote.

    Items are worked in low-level-code order so that nothing is netted
    before everything that could ask for it has asked. Where the bill
    of materials loops — tape consumes regrind and throws regrind off —
    the loop is cut, and the run records where, because a cut link
    means that item was netted against what is on the floor rather than
    against a run raised to produce it.
    """
    settings = settings or PlanningSettings.get()
    # Through `to_date`, because this is called from an API where the
    # date arrives as a string and from a shell where it arrives as a
    # datetime, and a string that survives to the arithmetic gives a
    # horizon nobody can read.
    planned_on = to_date(planned_on) or datetime.date.today()
    horizon_days = settings.horizon_days if horizon_days is None else horizon_days
    horizon_end = planned_on + datetime.timedelta(days=horizon_days)

    if warehouse.is_quarantine or warehouse.is_transit or warehouse.consignment_vendor_id:
        raise ValidationError(
            f"{warehouse} holds stock nobody may pick from, so there is nothing "
            "to plan against it."
        )

    calendar = plant_calendar(settings)
    # One book for the whole run, because the failure this fixes is two
    # orders being told the same hours are free. It has to remember
    # what the last answer spent.
    book = LoadBook(warehouse, planned_on, horizon_end)
    levels = low_level_codes()
    run = PlanningRun.objects.create(
        warehouse=warehouse, planned_on=planned_on, horizon_end=horizon_end,
        cut_links="\n".join(
            f"{cut.parent.sku} consumes {cut.item.sku}, which is already above "
            f"it in this explosion; that link was not planned through."
            for cut in levels.cuts
        ),
    )

    pending = defaultdict(list)          # item pk -> dependent demand
    extra_supply = defaultdict(list)     # item pk -> by-products already planned
    items = _candidates(warehouse, planned_on, horizon_end)
    done = set()
    deferred = []

    while True:
        # Shallowest first, with a stable tiebreak so that two runs
        # over the same data plan in the same order. A loop over a
        # moving set rather than a pass over a list, because exploding
        # a planned run adds items nothing had asked for yet.
        outstanding = [item for pk, item in items.items() if pk not in done]
        if not outstanding:
            break
        item = min(
            outstanding, key=lambda i: (level_of(levels, i), i.sku, i.pk)
        )
        done.add(item.pk)
        level = level_of(levels, item)

        demands = sales_demand(item, warehouse, planned_on)
        demands += work_order_demand(item, warehouse, planned_on)
        demands += pending.pop(item.pk, [])
        demands = [row for row in demands if row.date <= horizon_end]

        supplies = purchase_supply(item, warehouse, planned_on)
        supplies += requisition_supply(item, warehouse, planned_on)
        supplies += work_order_supply(item, warehouse, planned_on)
        supplies += extra_supply.pop(item.pk, [])

        safety, rule = safety_stock(item, warehouse)
        opening = opening_balance(item, warehouse, planned_on)
        # Before netting, because rescheduling reads the world as it
        # stands: an order this run is about to propose is not
        # something anybody can be asked to pull in.
        for message in reschedule(
            item, warehouse, opening, safety, demands, supplies, planned_on,
            settings.reschedule_tolerance_days,
        ):
            _write_action(run, item, warehouse, message, planned_on)
        shortages = net(opening, safety, demands, supplies, planned_on, rule)
        if not shortages:
            continue

        kind, bom = _make_or_buy(item)
        for shortage in shortages:
            order = _write(
                run, item, warehouse, kind, bom, shortage, level, settings,
                rule, calendar, book,
            )
            if kind != PlannedOrderKind.MAKE:
                continue
            for child_item, row in _components(bom, order):
                if child_item.pk in done:
                    deferred.append(
                        f"{child_item.sku}: {_show(row.quantity)} "
                        f"{child_item.uom} needed for planned {order} was not "
                        f"netted, because {child_item.sku} is planned before "
                        f"{item.sku}."
                    )
                    continue
                items.setdefault(child_item.pk, child_item)
                pending[child_item.pk].append(row)
            for by_item, rows in _byproducts(bom, order):
                if by_item.pk in done:
                    # The mirror of the case above, and reported for
                    # the same reason. It errs the safer way — the plan
                    # buys what the plant is about to produce rather
                    # than missing a requirement — but a planner told
                    # nothing cannot know to take it off.
                    coming = sum((row.quantity for row in rows), ZERO)
                    deferred.append(
                        f"{by_item.sku}: {_show(coming)} {by_item.uom} coming "
                        f"off planned {order} was not counted, because "
                        f"{by_item.sku} is planned before {item.sku}."
                    )
                    continue
                extra_supply[by_item.pk].extend(rows)
                items.setdefault(by_item.pk, by_item)

    if deferred:
        run.deferred_demand = "\n".join(deferred)
        run.save(update_fields=["deferred_demand", "updated_at"])
    return run


SUPPLY_FIELD = {
    SupplySource.PURCHASE: "purchase_order_line",
    SupplySource.REQUISITION: "requisition_line",
    SupplySource.WORK_ORDER: "work_order",
}


def _write_action(run, item, warehouse, message, planned_on):
    row = message.supply
    return PlanningAction.objects.create(
        run=run, item=item, warehouse=warehouse, action=message.action,
        source=row.source,
        quantity=message.quantity.quantize(QUANTITY, rounding=ROUND_CEILING),
        scheduled_on=max(row.date, planned_on), wanted_on=message.wanted_on,
        days=message.days, because=message.because[:255],
        **{SUPPLY_FIELD[row.source]: row.document},
    )


def _write(run, item, warehouse, kind, bom, shortage, level, settings, rule,
           calendar=None, book=None):
    bottleneck = None
    overloaded = False
    if kind == PlannedOrderKind.MAKE:
        vendor = None
        # Scheduled against the machines where there are machines to
        # schedule against. The routing's minutes over the machine's
        # open hours answers how long the run takes; only the book
        # answers when it can happen.
        placed = (
            schedule_make(
                book, bom, shortage.quantity, item.uom, shortage.date,
                run.planned_on, queue_days=settings.queue_days,
            )
            if book is not None else None
        )
        if placed is not None:
            release_on = placed["start"]
            bottleneck = placed["bottleneck"]
            overloaded = placed["overloaded"]
            if release_on >= shortage.date:
                # Never less than a day, however fast the machines
                # are. A run that starts and finishes on the same date
                # tells a planner nothing about when to release it,
                # and — because supply on a date covers demand on that
                # same date — it lets the run's own reground trim feed
                # its own hopper, which is material the plant has not
                # made yet.
                release_on = offset(shortage.date, 1, calendar)
            days = (shortage.date - release_on).days
        else:
            days = make_lead_days(
                item, bom, shortage.quantity, item.uom,
                queue_days=settings.queue_days,
                default=settings.default_make_lead_days,
            )
            release_on = offset(shortage.date, days, calendar)
    else:
        from apps.purchasing.pricing import preferred_vendor

        vendor = (rule.vendor if rule else None) or preferred_vendor(
            item, shortage.date
        )
        days = buy_lead_days(
            item, vendor, quantity=shortage.quantity, on_date=shortage.date,
            default=settings.default_buy_lead_days,
        )
        release_on = calendar_offset(shortage.date, days, calendar)
    order = PlannedOrder.objects.create(
        run=run, item=item, warehouse=warehouse, kind=kind,
        quantity=shortage.quantity, needed_by=shortage.date,
        release_on=release_on, lead_days=days, level=level,
        bom=bom, vendor=vendor, bottleneck=bottleneck,
        is_overloaded=overloaded,
        rounded_up_by=shortage.rounded,
    )
    for number, (row, taken) in enumerate(shortage.pegs, start=1):
        PlannedDemand.objects.create(
            planned_order=order, source=row.source, quantity=taken,
            needed_by=row.date, sales_order_line=row.sales_order_line,
            work_order=row.work_order, parent=row.parent, line_number=number,
        )
    return order


def _components(bom, order):
    """
    One level down from a planned run, wanted the day the run starts.

    One level, not an explosion: everything below this is planned in
    its own pass, against its own stock and its own firm supply, and
    exploding here would net the polymer against the sack's shelf
    instead of the polymer's.
    """
    scale = bom.scale_for(order.quantity, order.item.uom)
    rows = []
    for component in bom.components.select_related("item", "uom").all():
        required = component.item.to_stock_quantity(
            component.gross_quantity() * scale, component.uom
        )
        if required <= 0:
            continue
        rows.append((component.item, _demand(
            order.release_on, required, DemandSource.PLANNED, parent=order,
        )))
    return rows


def _byproducts(bom, order):
    """
    What a planned run throws off, available the day it finishes.

    By item, not by line. `byproduct_supply` answers for an item and a
    bill of materials may name that item on two lines; asking it once
    per line would count each of them for the sum of both.
    """
    by_item = {}
    for byproduct in bom.byproducts.select_related("item").all():
        by_item.setdefault(byproduct.item_id, byproduct.item)
    return [
        (item, byproduct_supply(
            bom, order.quantity, order.item.uom, item, order.needed_by
        ))
        for item in by_item.values()
    ]
