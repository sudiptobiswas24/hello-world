"""
Reading the stock ledger back.

Every other module here can be asked what it did: accounting has
statements, purchasing an aging and a vendor scorecard, sales a
statement, assets a register. Inventory posted to the ledger for the
whole of this project and could answer nothing about itself.

The report that matters most is `reconcile_to_ledger`. Stock is a
subledger: what the shelves are worth has to equal what the inventory
accounts say, and every test in this module has only ever asserted that
one item at a time. Nothing proved it in aggregate — which is exactly
where the difference hides, because a single item is easy to check by
hand and a thousand of them are not.

Everything here derives from movements and journal lines. Nothing is
stored, for the reason on-hand quantity is not stored: a saved total
drifts the moment anything is corrected, and corrections are how this
codebase fixes everything.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import JournalLine, round_money
from apps.core.models import to_date

from .models import Item, MovementType, StockMovement, Warehouse
from .valuation import inventory_account_for


def _stocked_items(item=None):
    items = Item.objects.filter(track_inventory=True, item_type="goods")
    if item is not None:
        items = items.filter(pk=item.pk)
    return items.order_by("sku")


def _warehouses(warehouse=None):
    if warehouse is not None:
        return [warehouse]
    return list(Warehouse.objects.order_by("code"))


def stock_valuation(as_of=None, warehouse=None, item=None, include_empty=False):
    """
    What is on every shelf and what it is worth.

    Rows are per item and warehouse, because that is the grain the
    valuation is actually computed at — a company-wide unit cost for an
    item held in two warehouses at different costs would be a number
    that describes nothing.
    """
    as_of = to_date(as_of)
    rows = []
    for stocked in _stocked_items(item).select_related("uom"):
        for shelf in _warehouses(warehouse):
            quantity, value = stocked.valuation_at(shelf, as_of=as_of)
            if not include_empty and not quantity and not value:
                continue
            rows.append({
                "item": stocked,
                "warehouse": shelf,
                "quantity": quantity,
                "value": round_money(value),
                "unit_cost": (
                    (value / quantity).quantize(Decimal("0.0001"))
                    if quantity else Decimal("0")
                ),
                "method": stocked.costing_method,
            })
    return {
        "as_of": as_of,
        "rows": rows,
        "total_value": round_money(sum((row["value"] for row in rows), Decimal("0"))),
    }


def reconcile_to_ledger(as_of=None):
    """
    Does the stock agree with the accounts?

    Grouped by inventory account, because that is the only grain at
    which the question has an answer: items can be pointed at different
    accounts, and a single company-wide total would net a shortfall on
    one against a surplus on another and call it balanced.

    `balanced` is the whole report. A stock system that cannot prove it
    agrees with the general ledger is a stock system somebody will stop
    believing the first time an auditor asks.
    """
    as_of = to_date(as_of)
    by_account = defaultdict(lambda: {"value": Decimal("0"), "items": []})
    for stocked in _stocked_items().select_related("uom"):
        try:
            account = inventory_account_for(stocked)
        except Exception:
            # An item with nowhere to be valued cannot be reconciled and
            # must not be silently dropped: it is reported separately.
            by_account[None]["items"].append(stocked)
            continue
        value = Decimal("0")
        for shelf in _warehouses():
            value += stocked.valuation_at(shelf, as_of=as_of)[1]
        if value:
            by_account[account]["items"].append(stocked)
        by_account[account]["value"] += value

    ledger = _account_balances(
        [account for account in by_account if account is not None], as_of
    )

    rows = []
    unassigned = by_account.pop(None, None)
    for account, holding in sorted(
        by_account.items(), key=lambda pair: pair[0].code
    ):
        stock_value = round_money(holding["value"])
        booked = round_money(ledger.get(account.pk, Decimal("0")))
        rows.append({
            "account": account,
            "stock_value": stock_value,
            "ledger_balance": booked,
            "difference": round_money(stock_value - booked),
            "items": len(holding["items"]),
        })

    total_stock = round_money(sum((row["stock_value"] for row in rows), Decimal("0")))
    total_ledger = round_money(sum((row["ledger_balance"] for row in rows), Decimal("0")))
    return {
        "as_of": as_of,
        "rows": rows,
        "total_stock_value": total_stock,
        "total_ledger_balance": total_ledger,
        "difference": round_money(total_stock - total_ledger),
        "balanced": all(not row["difference"] for row in rows),
        "unvalued_items": unassigned["items"] if unassigned else [],
    }


def _account_balances(accounts, as_of=None):
    """Posted debits less credits per account, up to a date."""
    if not accounts:
        return {}
    lines = JournalLine.objects.filter(entry__posted=True, account__in=accounts)
    if as_of:
        lines = lines.filter(entry__date__lte=as_of)
    rows = lines.values("account").annotate(debit=Sum("debit"), credit=Sum("credit"))
    return {
        row["account"]: (row["debit"] or Decimal("0")) - (row["credit"] or Decimal("0"))
        for row in rows
    }


def stock_ledger(item, warehouse=None, start=None, end=None, lot=None):
    """
    Every movement of one item, with the running quantity beside it.

    The running balance is recomputed from the opening position rather
    than read off the movements, so a row always agrees with the sum of
    everything above it. A ledger whose running total is stored is a
    ledger that can disagree with its own lines.
    """
    start, end = to_date(start), to_date(end)
    movements = item.movements.select_related("warehouse", "lot", "bin")
    if warehouse is not None:
        movements = movements.filter(warehouse=warehouse)
    if lot is not None:
        movements = movements.filter(lot=lot)

    opening = Decimal("0")
    if start is not None:
        earlier = movements.filter(occurred_at__date__lt=start)
        opening = earlier.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        movements = movements.filter(occurred_at__date__gte=start)
    if end is not None:
        movements = movements.filter(occurred_at__date__lte=end)

    running = opening
    rows = []
    for movement in movements.order_by("occurred_at", "id"):
        running += movement.quantity
        rows.append({
            "movement": movement,
            "date": movement.occurred_at,
            "type": movement.movement_type,
            "warehouse": movement.warehouse,
            "lot": movement.lot,
            "bin": movement.bin,
            "quantity": movement.quantity,
            "balance": running,
            "reference": movement.reference,
            "notes": movement.notes,
        })
    return {
        "item": item,
        "opening": opening,
        "rows": rows,
        "closing": running,
    }


# Aging assumes goods physically leave in the order they arrived,
# whatever method values them. That is how a warehouse actually works,
# and the alternative — refusing to age average-costed stock — would
# leave most of it unaged for a reason that is about accounting rather
# than about shelves.
DEFAULT_BUCKETS = (30, 60, 90, 180)


def stock_aging(as_of=None, warehouse=None, item=None, buckets=DEFAULT_BUCKETS):
    """
    How long what is on the shelf has been sitting there.

    Built by walking the movements and consuming the oldest receipts
    first, so what remains is dated by the arrival that actually left it
    there.
    """
    as_of = to_date(as_of) or datetime.date.today()
    edges = list(buckets)
    labels = [f"0-{edges[0]}"]
    for index in range(1, len(edges)):
        labels.append(f"{edges[index - 1] + 1}-{edges[index]}")
    labels.append(f"{edges[-1]}+")

    rows = []
    for stocked in _stocked_items(item).select_related("uom"):
        for shelf in _warehouses(warehouse):
            remaining = _surviving_receipts(stocked, shelf, as_of)
            if not remaining:
                continue
            counts = {label: Decimal("0") for label in labels}
            oldest = None
            for arrived, quantity in remaining:
                age = (as_of - arrived).days
                oldest = age if oldest is None else max(oldest, age)
                counts[_bucket_for(age, edges, labels)] += quantity
            rows.append({
                "item": stocked,
                "warehouse": shelf,
                "quantity": sum(counts.values()),
                "oldest_days": oldest,
                "buckets": counts,
            })
    return {"as_of": as_of, "labels": labels, "rows": rows}


def _bucket_for(age, edges, labels):
    for index, edge in enumerate(edges):
        if age <= edge:
            return labels[index]
    return labels[-1]


def _surviving_receipts(item, warehouse, as_of):
    """
    Which arrivals are still on the shelf, oldest first.

    Value-only movements are skipped: landed cost changes what stock is
    worth and not when it got here, and treating it as an arrival would
    date the goods to the day the freight invoice turned up.
    """
    layers = []
    movements = item.movements.filter(warehouse=warehouse)
    if as_of is not None:
        movements = movements.filter(occurred_at__date__lte=as_of)
    for movement in movements.order_by("occurred_at", "id"):
        if movement.quantity > 0:
            layers.append([movement.occurred_at.date(), movement.quantity])
        elif movement.quantity < 0:
            leaving = -movement.quantity
            while leaving > 0 and layers:
                drawn = min(layers[0][1], leaving)
                layers[0][1] -= drawn
                leaving -= drawn
                if layers[0][1] <= 0:
                    layers.pop(0)
    return [(arrived, quantity) for arrived, quantity in layers if quantity > 0]


def slow_moving(since, warehouse=None, item=None):
    """
    Stock that is here and has not moved out since a date.

    Reported rather than acted on. Whether slow stock should be
    discounted, returned or written off is a decision, and a system that
    made it would be deciding for somebody.
    """
    since = to_date(since)
    outbound = set(
        StockMovement.objects.filter(
            quantity__lt=0, occurred_at__date__gte=since
        ).values_list("item_id", "warehouse_id")
    )
    rows = []
    for stocked in _stocked_items(item).select_related("uom"):
        for shelf in _warehouses(warehouse):
            quantity, value = stocked.valuation_at(shelf)
            if quantity <= 0:
                continue
            if (stocked.pk, shelf.pk) in outbound:
                continue
            last_out = StockMovement.objects.filter(
                item=stocked, warehouse=shelf, quantity__lt=0
            ).order_by("-occurred_at").first()
            rows.append({
                "item": stocked,
                "warehouse": shelf,
                "quantity": quantity,
                "value": round_money(value),
                "last_issued": last_out.occurred_at if last_out else None,
            })
    rows.sort(key=lambda row: row["value"], reverse=True)
    return {
        "since": since,
        "rows": rows,
        "total_value": round_money(sum((row["value"] for row in rows), Decimal("0"))),
    }


def negative_stock(as_of=None):
    """
    Shelves holding less than nothing.

    Legitimate only where a warehouse allows it, and a permanent one
    anywhere else means a shipment was recorded that the goods for never
    arrived. Either way it is an exception somebody should see rather
    than a number buried in a valuation.
    """
    as_of = to_date(as_of)
    rows = []
    for stocked in _stocked_items().select_related("uom"):
        for shelf in _warehouses():
            quantity, value = stocked.valuation_at(shelf, as_of=as_of)
            if quantity >= 0:
                continue
            rows.append({
                "item": stocked,
                "warehouse": shelf,
                "quantity": quantity,
                "value": round_money(value),
                "allowed": shelf.allow_negative_stock,
            })
    return {
        "as_of": as_of,
        "rows": rows,
        "unexpected": [row for row in rows if not row["allowed"]],
    }


def movement_summary(start, end, warehouse=None, item=None):
    """
    What happened over a window, by kind of movement.

    Answers the question a valuation cannot: stock is worth the same as
    last month, but did nothing happen, or did a great deal happen twice.
    """
    start, end = to_date(start), to_date(end)
    movements = StockMovement.objects.filter(
        occurred_at__date__gte=start, occurred_at__date__lte=end
    )
    if warehouse is not None:
        movements = movements.filter(warehouse=warehouse)
    if item is not None:
        movements = movements.filter(item=item)

    rows = defaultdict(lambda: {"in": Decimal("0"), "out": Decimal("0"), "count": 0})
    for movement in movements:
        bucket = rows[movement.movement_type]
        bucket["count"] += 1
        if movement.quantity > 0:
            bucket["in"] += movement.quantity
        else:
            bucket["out"] += -movement.quantity
    return {
        "start": start,
        "end": end,
        "rows": [
            {"type": kind, "label": dict(MovementType.choices).get(kind, kind), **totals}
            for kind, totals in sorted(rows.items())
        ],
    }
