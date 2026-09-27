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

**Shipped is read from the stock ledger.** A delivery records which
batches it took; a customer return records nothing but the stock it
put back. Counting deliveries alone would name a customer who sent the
whole batch back, and a recall that rings the wrong people wastes the
day it has. So what each customer holds is the net of the movements
the deliveries and their returns made against the batch.
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


def descendants(lot, depth=4, beyond=None):
    """
    Every batch made, directly or at a remove, from this one.

    Rows of {level, lot, used_by, quantity, made}: the batch, the run
    that used it, how much, and the batches that run made. The batch
    being traced is level 1.

    A batch at the depth limit that was itself used further is added
    to `beyond` when a list is given: a recall that stops at four
    levels without saying so reads as complete.
    """
    seen = set()

    def walk(batch, level):
        if batch.pk in seen:
            return []
        if level > depth:
            if beyond is not None and runs_that_used(batch):
                beyond.append(batch)
            return []
        seen.add(batch.pk)
        rows = []
        for run, quantity in runs_that_used(batch):
            made = output_lots(run)
            rows.append({
                "level": level, "lot": batch, "used_by": run,
                "quantity": quantity, "made": made,
            })
            for child in made:
                rows.extend(walk(child, level + 1))
        return rows

    return walk(lot, 1)


def held_by_customers(lots):
    """
    What each customer was shipped of these batches, net of returns.

    Rows of {customer, lot, quantity, deliveries}, only where something
    is still out there.
    """
    from apps.inventory.models import StockMovement
    from apps.sales.models import Delivery

    lots = list(lots)
    movements = StockMovement.objects.filter(lot__in=lots)
    references = set(movements.values_list("reference", flat=True))
    # A delivery has a number, and moves stock, only once it has posted.
    deliveries = {
        delivery.number: delivery
        for delivery in Delivery.objects.filter(
            number__in=references
        ).select_related("sales_order__customer")
    }
    held = defaultdict(lambda: {"quantity": ZERO, "deliveries": set()})
    for movement in movements.select_related("lot"):
        delivery = deliveries.get(movement.reference)
        if delivery is None:
            continue
        row = held[(delivery.sales_order.customer, movement.lot)]
        # A shipment takes stock out (negative); a return puts it back.
        row["quantity"] -= movement.quantity
        row["deliveries"].add(delivery.number)
    return [
        {"customer": customer, "lot": lot, "quantity": row["quantity"],
         "deliveries": sorted(row["deliveries"])}
        for (customer, lot), row in sorted(
            held.items(), key=lambda item: (item[0][0].code, item[0][1].code)
        )
        if row["quantity"] > 0
    ]


def recall(lot, depth=4):
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
    return {
        "descendants": rows,
        "customers": held_by_customers(affected.values()),
        "not_followed": beyond,
    }
