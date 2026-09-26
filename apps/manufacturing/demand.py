"""
What has been sold against what is being made.

A sack plant makes to order far more than it makes to stock: fifty
thousand printed sacks for a cement customer is not something anybody
runs speculatively. So the question a planner asks every morning is not
"what is on the board" but "what have we promised that nothing is
making yet" — and until a run can say which customer line it is for,
nothing can answer it.

The pointer sits on the work order. A make-to-order run is meaningless
without the order it is for; the order is perfectly meaningful without
the run, and it should not have to know that a manufacturing module
exists. Django gives `line.work_orders` back for nothing, so the
question can still be asked from the sales side without sales importing
anything.

Coverage counts the runs, not the stock. A line may be covered by
finished goods already on a shelf and that is the reservation system's
question, asked and answered elsewhere; this one is about whether
somebody has started making it.
"""

from decimal import Decimal


def _stock(line, quantity):
    """A sales line's quantity in the item's stocking unit."""
    if line.item_id is None:
        return Decimal("0")
    return line.item.to_stock_quantity(quantity, line.uom or line.item.uom)


def coverage(line):
    """
    What one customer line has ordered against what is being made for
    it.

    Cancelled runs do not cover anything — they were abandoned, and a
    line they used to cover is uncovered again. A closed run does
    count: it made what it made, and whether that was enough is the
    `made` figure's business rather than the status's.
    """
    ordered = _stock(line, line.quantity)
    runs = line.work_orders.exclude(status="cancelled").select_related("item", "uom")
    on_order = Decimal("0")
    made = Decimal("0")
    for run in runs:
        on_order += run.item.to_stock_quantity(run.quantity_ordered, run.uom)
        made += run.item.to_stock_quantity(run.quantity_produced(), run.uom)
    shipped = line.quantity_shipped_in_stock_units()
    return {
        "line": line,
        "item": line.item,
        "ordered": ordered,
        "on_work_orders": on_order,
        "made": made,
        "shipped": shipped,
        # What nobody has started. Measured against what is on work
        # orders rather than against what they have finished, or a
        # planner would raise a second run for something already on a
        # loom.
        #
        # What has shipped is NOT netted off. A shipment may have come
        # out of one of these runs or out of stock that was already
        # there, and nothing here can tell which; netting it would
        # double-count the first case and under-report what is left to
        # make. Erring the other way puts a line on the planner's list
        # that they glance at and dismiss, which is the cheaper mistake.
        # A line shipped in full is exact, and `uncovered()` drops it.
        "uncovered": max(ordered - on_order, Decimal("0")),
        "runs": list(runs),
    }


def uncovered(sales_order=None, item=None):
    """
    Every customer line with something nobody has started making.

    The planner's morning list. Four kinds of line are left out, each
    for its own reason: a charge line and a line for something this
    plant buys have no bill of materials and are not uncovered but
    purchased; a line on a cancelled order is not promised to anybody;
    and a line shipped in full has nothing left to make, which is the
    one case where netting the shipment off is exact.
    """
    from apps.sales.models import OrderStatus, SalesOrderLine

    from .bom import default_bom_for

    lines = SalesOrderLine.objects.filter(item__isnull=False).exclude(
        order__status=OrderStatus.CANCELLED
    ).select_related("item", "uom", "order")
    if sales_order is not None:
        lines = lines.filter(order=sales_order)
    if item is not None:
        lines = lines.filter(item=item)
    rows = []
    for line in lines:
        if default_bom_for(line.item) is None:
            continue
        row = coverage(line)
        if row["shipped"] >= row["ordered"]:
            continue
        if row["uncovered"] > 0:
            rows.append(row)
    return rows


def consumed_lots(work_order):
    """
    Which batches went into a run.

    The first half of a genealogy: a customer rejects a pallet of sacks
    for low GSM, and the question is which fabric went into them and
    which blend went into that. Only issues that named a lot appear —
    an untracked material has no batch to name and is not a silent
    empty answer.
    """
    rows = {}
    for issue in work_order.posted_issues():
        for line in issue.lines.select_related("item", "lot", "uom"):
            if line.lot_id is None:
                continue
            key = line.lot_id
            entry = rows.setdefault(key, [line.lot, line.item, Decimal("0")])
            entry[2] += line.stock_quantity() * issue.sign()
    return [tuple(entry) for entry in rows.values() if entry[2] > 0]


def runs_that_made(lot):
    """
    Which runs put this batch on a shelf.

    The other half. Walked from the production entries rather than from
    the stock movements, because a movement says a batch arrived and
    only the entry says which run it came off.

    By-products count. In this plant the by-product IS the interesting
    one: contaminated regrind ruins the next three runs that eat it, and
    a genealogy that could only walk back from main output would stop at
    exactly the batch somebody wants to trace.
    """
    from .orders import ProductionByproduct, ProductionEntry

    entries = list(ProductionEntry.objects.filter(
        lot=lot, posted=True, voided_at__isnull=True
    ).select_related("work_order"))
    entries.extend(
        row.entry for row in ProductionByproduct.objects.filter(
            lot=lot, entry__posted=True, entry__voided_at__isnull=True
        ).select_related("entry", "entry__work_order")
    )
    seen = set()
    runs = []
    for entry in sorted(entries, key=lambda row: (row.entry_date, row.pk)):
        if entry.work_order_id in seen:
            continue
        seen.add(entry.work_order_id)
        runs.append(entry.work_order)
    return runs


def genealogy(lot, depth=4):
    """
    What a batch was made from, and what that was made from.

    Depth-limited rather than walked to exhaustion: the regrind loop
    means a fabric lot can lead back to a tape lot that leads back to a
    regrind lot that came off the fabric, and a walk with no limit does
    not come back. Four levels reaches polymer from a sack in this
    plant, which is as far as anybody asks.
    """
    seen = set()

    def walk(batch, level):
        if level > depth or batch.pk in seen:
            return []
        seen.add(batch.pk)
        rows = []
        for run in runs_that_made(batch):
            for came_from, item, quantity in consumed_lots(run):
                rows.append({
                    "level": level,
                    "lot": batch,
                    "made_by": run,
                    "from_lot": came_from,
                    "from_item": item,
                    "quantity": quantity,
                })
                rows.extend(walk(came_from, level + 1))
        return rows

    return walk(lot, 1)
