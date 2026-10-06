"""
A forecast proposed from what has shipped, by season.

A sack plant's year has a shape: cement and fertiliser bags ship hard
after the monsoon and slacken through it. A flat average plans the same
polymer for July as for November, and runs short in one and heavy in
the other. So the proposal is the plain one a planner can check by
hand:

    forecast for a month = last year's monthly average
                           x that calendar month's seasonal index

where a month's index is what it shipped, across every full year of
history, over what an average month of those years shipped. November
at 1.5 ships half as much again as an average month.

**What it reads.** Shipments, not orders: posted deliveries net of
customer returns, by calendar month, for the item from the warehouse.
Job work is left out: those goods are the customer's, made from what
they sent, and planning leaves them out for the same reason.

**What it refuses to pretend.**
- Under 12 full months of history there is no year to average; nothing
  is proposed.
- With 12 to 23 there is no second year to see a season in; the month
  is last year's average, flat, and it says so.
- Trend is off unless asked for. Last year's growth, carried forward,
  turns one good year into a stock problem. It is always reported.
- The backtest forecasts last year from the years before it and says how
  far out that was. It needs 24 months before the year it scores, so 36
  in all; with fewer it says so, rather than score the method on the
  year it was fitted to.

**Proposed, then accepted.** Proposing writes nothing. Accepting works it
out again rather than trusting figures handed back, and writes ordinary
`Forecast` rows that the planner consumes like any other. A month
already forecast, or proposed at nothing, is reported as skipped with
the reason.
"""

import calendar
import datetime
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

ZERO = Decimal("0")
THOUSANDTH = Decimal("0.001")
MIN_MONTHS = 12
SEASONAL_MONTHS = 24
BACKTEST_MONTHS = 36


def _month_start(day):
    return day.replace(day=1)


def _add_months(first, count):
    month = first.month - 1 + count
    return datetime.date(first.year + month // 12, month % 12 + 1, 1)


def _month_end(first):
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def monthly_shipments(item, warehouse, first, last):
    """{first of month: shipped less returned, in stock units}, every month from first to last."""
    from apps.sales.models import DeliveryLine

    months = OrderedDict()
    month = first
    while month <= last:
        months[month] = ZERO
        month = _add_months(month, 1)
    lines = DeliveryLine.objects.filter(
        order_line__item=item, warehouse=warehouse, delivery__posted=True,
        delivery__sales_order__is_job_work=False,
        delivery__delivery_date__gte=first, delivery__delivery_date__lte=_month_end(last),
    ).select_related("delivery", "order_line__uom", "order_line__item")
    for line in lines:
        quantity = item.to_stock_quantity(line.quantity_shipped, line.order_line.uom)
        sign = -1 if line.delivery.is_return() else 1
        months[_month_start(line.delivery.delivery_date)] += sign * quantity
    # The months before this system shipped anything, brought in from the
    # old one; a month cannot hold both (history.py refuses it).
    from .history import ShipmentHistory

    for row in ShipmentHistory.objects.filter(item=item, warehouse=warehouse, month__gte=first, month__lte=last):
        months[row.month] += row.quantity
    return months


def _history_start(item, warehouse):
    from apps.sales.models import DeliveryLine

    # The same shipments monthly_shipments counts: a job-work one years
    # ago would otherwise start the history early with empty months.
    first = DeliveryLine.objects.filter(
        order_line__item=item, warehouse=warehouse, delivery__posted=True,
        delivery__sales_order__is_job_work=False,
    ).order_by("delivery__delivery_date").values_list("delivery__delivery_date", flat=True).first()
    from .history import ShipmentHistory

    earliest = ShipmentHistory.objects.filter(item=item, warehouse=warehouse).order_by("month") \
        .values_list("month", flat=True).first()
    starts = [month for month in (_month_start(first) if first else None, earliest) if month]
    return min(starts) if starts else None


def _fit(history):
    """Level, indices and growth from full years of history, newest last."""
    values = list(history.items())
    years = len(values) // 12
    blocks = [values[len(values) - 12 * (n + 1):len(values) - 12 * n] for n in range(years)]
    blocks.reverse()
    last_year = blocks[-1]
    level = sum((quantity for _, quantity in last_year), ZERO) / 12
    growth = None
    if years >= 2:
        before = sum((quantity for _, quantity in blocks[-2]), ZERO)
        after = sum((quantity for _, quantity in last_year), ZERO)
        growth = after / before if before else None
    indices = None
    if years >= 2:
        shipped = {month: ZERO for month in range(1, 13)}
        for block in blocks:
            for first, quantity in block:
                shipped[first.month] += quantity
        average = sum((sum((q for _, q in block), ZERO) / 12 for block in blocks), ZERO)
        if average > 0:
            indices = {month: shipped[month] / average for month in range(1, 13)}
    return level, indices, growth, years


def _forecast(level, indices, growth, origin, month, with_trend):
    quantity = level * (indices[month.month] if indices else Decimal("1"))
    if with_trend and growth:
        ahead = (month.year - origin.year) * 12 + month.month - origin.month
        quantity *= Decimal(str(float(growth) ** (ahead / 12)))
    return max(quantity, ZERO).quantize(THOUSANDTH, ROUND_HALF_UP)


def _backtest(history):
    """Forecast the last year from the years before it: mean absolute
    percentage error and bias, or why not."""
    values = list(history.items())
    if len(values) < BACKTEST_MONTHS:
        return {"months": 0, "note": f"A backtest needs {BACKTEST_MONTHS} months of history: "
                                     f"{SEASONAL_MONTHS} to fit, 12 to score. There are "
                                     f"{len(values)}."}
    fitted = OrderedDict(values[:-12])
    level, indices, _, _ = _fit(fitted)
    origin = values[-13][0]
    errors, forecast_total, actual_total = [], ZERO, ZERO
    for first, actual in values[-12:]:
        predicted = _forecast(level, indices, None, origin, first, False)
        forecast_total += predicted
        actual_total += actual
        if actual > 0:
            errors.append(abs(predicted - actual) / actual)
    return {
        "months": 12,
        "mape_percent": (sum(errors, ZERO) / len(errors) * 100).quantize(Decimal("0.01"))
        if errors else None,
        "bias_percent": ((forecast_total - actual_total) / actual_total * 100).quantize(
            Decimal("0.01")) if actual_total else None,
    }


def propose(item, warehouse, as_of=None, months_ahead=6, with_trend=False):
    """What history says the coming months will ship. Writes nothing."""
    as_of = as_of or timezone.localdate()
    if not 1 <= months_ahead <= 24:
        raise ValidationError("Propose between 1 and 24 months ahead.")
    last = _add_months(_month_start(as_of), -1)
    start = _history_start(item, warehouse)
    if start is None or start > last:
        raise ValidationError(f"{item.sku} has never shipped from {warehouse}; there is no "
                              "history to forecast from.")
    history = monthly_shipments(item, warehouse, start, last)
    usable = len(history) // 12 * 12
    if usable < MIN_MONTHS:
        raise ValidationError(
            f"{item.sku} has {len(history)} full months of history from {warehouse}; a "
            f"forecast needs at least {MIN_MONTHS}, a year to average."
        )
    history = OrderedDict(list(history.items())[-usable:])
    level, indices, growth, years = _fit(history)
    notes = []
    if indices is None:
        notes.append(
            f"Only {years} full year of history: no second year to see a season in, so "
            "every month is last year's average."
        )
    if with_trend and growth is None:
        notes.append("No trend applied: it needs two full years to compare.")
    first = _month_start(as_of)
    rows = []
    for ahead in range(months_ahead):
        month = _add_months(first, ahead)
        rows.append({"starts_on": month, "ends_on": _month_end(month),
                     "quantity": _forecast(level, indices, growth, last, month, with_trend)})
    return {
        "item": item, "warehouse": warehouse,
        "method": "seasonal" if indices else "level",
        "history_months": len(history),
        "history_from": next(iter(history)),
        "level": level.quantize(THOUSANDTH, ROUND_HALF_UP),
        "indices": {month: index.quantize(Decimal("0.0001"), ROUND_HALF_UP)
                    for month, index in indices.items()} if indices else None,
        "year_on_year_growth": growth.quantize(Decimal("0.0001"), ROUND_HALF_UP)
        if growth is not None else None,
        "trend_applied": bool(with_trend and growth),
        "backtest": _backtest(history),
        "rows": rows,
        "notes": notes,
    }


@transaction.atomic
def accept(item, warehouse, as_of=None, months_ahead=6, with_trend=False):
    """Write the proposal as forecasts. Returns (created, skipped with reasons)."""
    from .forecast import Forecast

    proposal = propose(item, warehouse, as_of, months_ahead, with_trend)
    created, skipped = [], []
    for row in proposal["rows"]:
        if row["quantity"] <= 0:
            skipped.append((row, "history says nothing ships that month"))
            continue
        clash = Forecast.objects.filter(
            item=item, warehouse=warehouse, starts_on__lte=row["ends_on"],
            ends_on__gte=row["starts_on"]).first()
        if clash is not None:
            skipped.append((row, f"already forecast: {clash}"))
            continue
        created.append(Forecast.objects.create(
            item=item, warehouse=warehouse, starts_on=row["starts_on"],
            ends_on=row["ends_on"], quantity=row["quantity"],
            notes=(f"Statistical, {proposal['method']}, from {proposal['history_months']} "
                   "months" + (", with trend" if proposal["trend_applied"] else "")),
        ))
    return created, skipped
