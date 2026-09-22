"""
When can we promise it, and when could we make it.

The question a sales rep is asked on the telephone and the one this
system could not answer. Every piece of the arithmetic already
existed — dated demand, dated supply, lead times, and since the
scheduler, machines that are actually busy — and nothing put them
together into "fifty thousand sacks by the twentieth: yes or no".

Two different questions, answered separately on purpose.

**Available to promise** is about stock and orders already placed. It
asks what is uncommitted: what is on the shelf and what is coming,
less what has already been promised to somebody else. It is the
answer a rep can give without ringing the plant.

**Capable to promise** is about the plant. When available-to-promise
says no, it asks what it would take to make the quantity — material
lead times, the routing, and the machines as they are actually
booked — and gives the earliest date a run could deliver it. That
answer is only as good as the plan, and it is worth having precisely
because the alternative is a rep guessing.

**A quotation reserves nothing.** Neither answer books stock, books a
machine or writes anything down. Two enquiries on the same morning
get the same date, and the second one is only wrong once the first is
confirmed. That is how every order book works and it is worth stating
rather than discovering: the cure is to confirm the order, which does
reserve stock, not to have the enquiry pretend it did.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from .capacity import LoadBook, schedule_make
from .leadtime import buy_lead_days, calendar_offset, plant_calendar

ZERO = Decimal("0")


def _ladder(item, warehouse, planned_on, horizon_end):
    """
    Dated supply and dated demand for one item, as the plan sees them.

    Reuses the plan's own collectors rather than asking the database
    its own way, so that a promise and a shortage can never disagree
    about what is on the shelf or what is already owed.
    """
    from .mrp import (
        opening_balance,
        purchase_supply,
        requisition_supply,
        sales_demand,
        work_order_demand,
        work_order_supply,
    )

    demands = sales_demand(item, warehouse, planned_on)
    demands += work_order_demand(item, warehouse, planned_on)
    demands = [row for row in demands if row.date <= horizon_end]
    supplies = purchase_supply(item, warehouse, planned_on)
    supplies += requisition_supply(item, warehouse, planned_on)
    supplies += work_order_supply(item, warehouse, planned_on)
    return (
        opening_balance(item, warehouse, planned_on), demands, supplies
    )


def available_to_promise(item, warehouse, planned_on=None, horizon_days=90):
    """
    What is uncommitted, period by period.

    A period runs from one arrival to the next, and what that arrival
    frees up is itself less **everything owed before the next arrival
    comes**. That is the whole subtlety, and getting it wrong is the
    commonest way an order book oversells: a thousand kilos on the
    shelf with eight hundred promised for a fortnight's time leaves
    two hundred to give away today, not a thousand. Reading the
    on-hand figure and the order book separately is how the same
    fabric gets sold twice.

    A period that comes out short borrows from the periods before it,
    because stock that arrived earlier can cover a shortage that comes
    later. The reverse is not true and is not allowed: a deficit that
    has already happened is not cured by an arrival after it, so the
    running total carries the deficit forward and only the figure
    reported to a rep is floored at nought.
    """
    planned_on = planned_on or datetime.date.today()
    horizon_end = planned_on + datetime.timedelta(days=horizon_days)
    opening, demands, supplies = _ladder(
        item, warehouse, planned_on, horizon_end
    )

    arriving = defaultdict(lambda: ZERO)
    arriving[planned_on] += opening
    for row in supplies:
        when = max(row.date, planned_on)
        if when <= horizon_end:
            arriving[when] += row.quantity
    owed = defaultdict(lambda: ZERO)
    for row in demands:
        owed[max(row.date, planned_on)] += row.quantity

    dates = sorted(arriving)
    windows = []
    for index, when in enumerate(dates):
        nxt = dates[index + 1] if index + 1 < len(dates) else None
        due = sum(
            (quantity for day, quantity in owed.items()
             if day >= when and (nxt is None or day < nxt)),
            ZERO,
        )
        windows.append({
            "date": when, "arriving": arriving[when], "owed": due,
            "uncommitted": arriving[when] - due,
        })

    # Backward pass: a short period is covered by what arrived before
    # it, which is the one direction stock can travel in time.
    for index in range(len(windows) - 1, 0, -1):
        short = windows[index]["uncommitted"]
        if short < 0:
            windows[index - 1]["uncommitted"] += short
            windows[index]["uncommitted"] = ZERO

    running = ZERO
    for window in windows:
        running += window["uncommitted"]
        window["promisable"] = max(running, ZERO)
    return windows


def when_can_we_promise(item, warehouse, quantity, planned_on=None,
                        horizon_days=90):
    """
    The earliest date `quantity` can be given away out of what already
    exists, or None if nothing in the horizon reaches it.

    None rather than a far-off date, because "not from stock" and "in
    March" are different answers and a rep who is handed the second
    when the first is true will promise March.
    """
    quantity = Decimal(quantity)
    for step in available_to_promise(item, warehouse, planned_on, horizon_days):
        if step["promisable"] >= quantity:
            return step["date"]
    return None


def capable_to_promise(item, warehouse, quantity, planned_on=None,
                       settings=None, horizon_days=None):
    """
    When the plant could have `quantity`, whether or not it exists
    today.

    Answers from stock where stock answers, and otherwise schedules a
    hypothetical run — the real routing against the real machines,
    with their real commitments on them — and adds the longest
    material lead time underneath it. Nothing is booked: see the
    module docstring on what a quotation does and does not reserve.
    """
    from .models import PlanningSettings
    from .mrp import _make_or_buy, safety_stock

    settings = settings or PlanningSettings.get()
    planned_on = planned_on or datetime.date.today()
    horizon_days = settings.horizon_days if horizon_days is None else horizon_days
    quantity = Decimal(quantity)

    from_stock = when_can_we_promise(
        item, warehouse, quantity, planned_on, horizon_days
    )
    if from_stock is not None:
        return {
            "item": item, "quantity": quantity, "date": from_stock,
            "source": "stock",
            "note": "out of what is on the shelf and already on order",
        }

    kind, bom = _make_or_buy(item)
    if kind == "buy":
        vendor = _vendor_for(item, warehouse, planned_on)
        days = buy_lead_days(
            item, vendor, quantity=quantity, on_date=planned_on,
            default=settings.default_buy_lead_days,
        )
        return {
            "item": item, "quantity": quantity,
            "date": planned_on + datetime.timedelta(days=days),
            "source": "purchase", "vendor": vendor, "lead_days": days,
            "note": f"nothing to spare, so {days} days to buy it",
        }

    return _soonest_run(
        item, warehouse, bom, quantity, planned_on, settings, horizon_days
    )


def _vendor_for(item, warehouse, on_date):
    from apps.purchasing.pricing import preferred_vendor

    from .mrp import safety_stock

    _floor, rule = safety_stock(item, warehouse)
    return (rule.vendor if rule else None) or preferred_vendor(item, on_date)


def _soonest_run(item, warehouse, bom, quantity, planned_on, settings,
                 horizon_days):
    """
    The earliest a run of this could finish, machines and materials
    both.

    Scheduled forwards by trying successive finish dates: the
    scheduler places work backwards from a date it is given, so the
    earliest feasible finish is found by asking it for earlier and
    earlier dates until the answer stops starting in the past. Crude,
    and it is a handful of passes on a real routing rather than a
    search worth optimising.
    """
    calendar = plant_calendar(settings)
    horizon_end = planned_on + datetime.timedelta(days=horizon_days)
    material = _material_ready(item, bom, quantity, warehouse, planned_on, settings)
    floor = max(material, planned_on)

    finish = floor
    while finish <= horizon_end:
        # A fresh book each pass: the previous pass booked hours it is
        # not entitled to keep, since nothing is being committed.
        book = LoadBook(warehouse, planned_on, horizon_end)
        placed = schedule_make(
            book, bom, quantity, item.uom, finish, planned_on,
            queue_days=settings.queue_days,
        )
        if placed is None:
            days = settings.default_make_lead_days
            if days is None:
                raise ValidationError(
                    f"{bom} has no routing and no default make lead time is "
                    f"set, so nothing can say when {item} could be ready."
                )
            return {
                "item": item, "quantity": quantity,
                "date": floor + datetime.timedelta(days=int(days)),
                "source": "run", "bottleneck": None,
                "note": "no routing, so the stated default make time",
            }
        if not placed["overloaded"] and placed["start"] >= floor:
            return {
                "item": item, "quantity": quantity, "date": finish,
                "source": "run", "bottleneck": placed["bottleneck"],
                "starts_on": placed["start"],
                "material_ready": material,
                "note": (
                    f"a run starting {placed['start']}, held longest by "
                    f"{placed['bottleneck']}"
                ),
            }
        finish += datetime.timedelta(days=1)

    return {
        "item": item, "quantity": quantity, "date": None, "source": "run",
        "note": (
            f"nothing inside {horizon_days} days: the machines cannot fit it "
            "and no earlier date works"
        ),
    }


def _material_ready(item, bom, quantity, warehouse, planned_on, settings):
    """
    The soonest every component could be to hand.

    One level down only, and from stock or purchase rather than from a
    run of its own. A full recursive promise would be more accurate
    and would also take as long as a planning run to answer a
    telephone call; this errs optimistic and says so, which is the
    right way round for a figure a rep will follow up by checking.
    """
    from .mrp import _make_or_buy

    ready = planned_on
    scale = bom.scale_for(quantity, item.uom)
    calendar = plant_calendar(settings)
    for component in bom.components.select_related("item", "uom").all():
        wanted = component.item.to_stock_quantity(
            component.gross_quantity() * scale, component.uom
        )
        from_stock = when_can_we_promise(
            component.item, warehouse, wanted, planned_on,
            settings.horizon_days,
        )
        if from_stock is not None:
            ready = max(ready, from_stock)
            continue
        kind, _bom = _make_or_buy(component.item)
        vendor = _vendor_for(component.item, warehouse, planned_on)
        days = buy_lead_days(
            component.item, vendor, quantity=wanted, on_date=planned_on,
            default=settings.default_buy_lead_days,
        )
        ready = max(ready, planned_on + datetime.timedelta(days=days))
    return ready
