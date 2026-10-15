"""
Where a batch went: the forward half of a genealogy.

`demand.genealogy()` answers the complaint — a customer rejects a pallet
of sacks, and the question is which fabric, which tape and which blend
went into them. This answers the recall, which is the same question
the other way round and the one with a deadline on it: a lot of
masterbatch turns out to have been the wrong grade, and before anybody
can ring a customer the plant has to know which runs ate it, which
batches those runs made, which runs ate *those*, and who was shipped
any of it.

The walk mirrors the backward one: depth-limited, because regrind
loops back on itself, and by-products followed, because contaminated
regrind is exactly the batch a recall has to chase.

**Shipped is read from what the deliveries recorded.** A delivery
holds each batch it took and the movement that took it; a customer
return holds each batch it put back the same way. What a customer
holds is the net of the two. Counting deliveries alone would name a
customer who sent the whole batch back, and matching movements by the
reference printed on them would count any adjustment somebody typed
that number on.
"""

from collections import defaultdict
from decimal import Decimal

from .explain import output_lots

ZERO = Decimal("0")


def runs_that_used(lot):
    """
    Which runs drew this batch, and how much of it they kept.

    Issues net of returns to stock, the mirror of `consumed_lots`: a
    run that drew a bag and put it back unopened did not use it.
    """
    from .orders import MaterialIssueLine

    used = defaultdict(lambda: ZERO)
    runs = {}
    lines = MaterialIssueLine.objects.filter(
        lot=lot, issue__posted=True, issue__voided_at__isnull=True,
    ).select_related("issue", "issue__work_order", "item", "uom")
    for line in lines:
        order = line.issue.work_order
        runs[order.pk] = order
        used[order.pk] += line.stock_quantity() * line.issue.sign()
    return [(runs[pk], quantity) for pk, quantity in sorted(used.items()) if quantity > 0]


def made_into_by_runs(lot):
    """
    The lineage downward through production, for the release gate
    (`register_lot_sources`, O207): the batches every run that used this
    one put on a shelf, main output and by-products. The same two
    questions `descendants` asks, so a recall and a trace follow one
    lineage. At the run, as the genealogy is: every batch a run made is
    made from every batch it used.
    """
    return [child for run, _quantity in runs_that_used(lot) for child in output_lots(run)]


def held_inputs(lot):
    """
    The lineage upward through production, for the release gate: the
    batches the runs that made this one consumed that quality holds now
    (`demand.runs_that_made`, `demand.consumed_lots`, the genealogy's own).

    Only the held ones, as a subcontractor's assemblies answer: quality
    reads a batch whose sources all passed as passed, which is right for a
    split and wrong for a run. Fabric woven from passed tape is not fabric
    inspected, and under its own mandatory plan it would go unmeasured.
    """
    from apps.quality.models import ReleaseStatus
    from apps.quality.release import release_status

    from .demand import consumed_lots, runs_that_made

    found = {}
    for run in runs_that_made(lot):
        for came_from, _item, _quantity in consumed_lots(run):
            if came_from.pk not in found and release_status(came_from) == ReleaseStatus.HELD:
                found[came_from.pk] = came_from
    return list(found.values())


def descendants(lot, depth=None, beyond=None):
    """
    Every batch made, directly or at a remove, from this one.

    Rows of {level, lot, used_by, quantity, made}: the batch, the run
    that used it, how much, and the batches that run made. The batch
    being traced is level 1.

    A batch at the depth limit that was itself used further is added
    to `beyond` when a list is given: a recall that stops at four
    levels without saying so reads as complete.

    A batch split or joined into others is followed into them the same
    way, with the re-batch as what used it: the sacks are the same sacks
    under a new number.
    """
    from apps.quality.release import LINEAGE_DEPTH, walk

    from .rebatch import remade_into

    depth = LINEAGE_DEPTH if depth is None else depth
    uses = {}

    def used(batch):
        if batch.pk not in uses:
            uses[batch.pk] = ([(run, quantity, output_lots(run)) for run, quantity in runs_that_used(batch)]
                              + remade_into(batch))
        return uses[batch.pk]

    def made(batch):
        return [child for _used_by, _quantity, children in used(batch) for child in children]

    # The gate's one walk (apps.quality.release.walk): each batch once, so a
    # loop of any length ends, levels counted from 1 here.
    rows = []
    for batch, level in walk(lot, made, depth=depth):
        if level >= depth:
            if beyond is not None and used(batch):
                beyond.append(batch)
            continue
        rows.extend({"level": level + 1, "lot": batch, "used_by": used_by,
                     "quantity": quantity, "made": children}
                    for used_by, quantity, children in used(batch))
    return rows


def held_by_customers(lots):
    """
    What each customer was shipped of these batches, net of returns.

    Rows of {customer, lot, quantity, deliveries}, only where something
    is still out there.
    """
    from apps.sales.models import DeliveryAllocation

    held = defaultdict(lambda: {"quantity": ZERO, "deliveries": set()})
    allocations = DeliveryAllocation.objects.filter(lot__in=list(lots)).select_related(
        "lot", "line__delivery__sales_order__customer", "line__delivery__reverses",
    )
    for allocation in allocations:
        delivery = allocation.line.delivery
        row = held[(delivery.sales_order.customer, allocation.lot)]
        row["quantity"] += -allocation.quantity if delivery.is_return() else allocation.quantity
        row["deliveries"].add(delivery.number)
    return [
        {"customer": customer, "lot": lot, "quantity": row["quantity"],
         "deliveries": sorted(row["deliveries"])}
        for (customer, lot), row in sorted(
            held.items(), key=lambda item: (item[0][0].code, item[0][1].code)
        )
        if row["quantity"] > 0
    ]


def recall(lot, depth=None):
    """
    Everything a recall of this batch has to chase: the batches made
    from it, and the customers holding any of them — including this
    batch itself, if it was sold as it was.
    """
    beyond = []
    rows = descendants(lot, depth, beyond)
    affected = {lot.pk: lot}
    for row in rows:
        for child in row["made"]:
            affected[child.pk] = child
    from .bales import bales_holding

    return {
        "descendants": rows,
        "customers": held_by_customers(affected.values()),
        # The bale number is what the customer's warehouse can see.
        "bales": bales_holding(affected.values()),
        "not_followed": beyond,
    }
