"""
Demand nobody has ordered yet.

A sack plant making standard sizes to stock has nothing to plan
against until an order lands, and by then the polymer needed
ordering three weeks ago. A forecast is how the plant says "we expect
to ship forty tonnes of 60 x 100 in October" before anybody has asked
for it.

**A forecast is consumed by the orders that arrive against it.** This
is the whole difficulty and the reason a naive forecast makes things
worse rather than better. Forty tonnes forecast for October and
twenty-five tonnes ordered is not sixty-five tonnes of demand; it is
forty, of which twenty-five is now real. Adding the two is the
classic way a forecast doubles a plant's stock and then gets switched
off.

**Consumption is within the period and no further.** An order for the
third of November does not eat October's forecast, however much
October has left. Plants that let it end up with a forecast that
never expires and a November that looks empty. Where a plant really
does want a window — orders pulled in a few days early consuming the
previous period — that is a policy with a number on it, and it is not
guessed here.

**What is left over is still demand.** The unconsumed part of a
period's forecast is planned for, because that is the point: the
material has to be there for the orders that have not come in yet.
Once the period is past, what was never ordered is never ordered, and
a forecast behind the plan's own date contributes nothing.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import AuditModel
from apps.inventory.models import Item, Warehouse

ZERO = Decimal("0")


class Forecast(AuditModel):
    """
    What the plant expects to ship in a period, before anybody orders
    it.

    A period rather than a date, because nobody forecasts a Tuesday.
    Periods for one item on one shelf may not overlap: two forecasts
    covering the same week would each be consumed by the same orders
    and between them plan for twice the material.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="forecasts")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="forecasts"
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="Expected shipments in the period, in the item's stocking "
                  "unit.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["item", "warehouse", "starts_on"]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0), name="forecast_quantity_positive"
            ),
            models.CheckConstraint(
                check=Q(ends_on__gte=models.F("starts_on")),
                name="forecast_ends_after_it_starts",
            ),
        ]
        indexes = [models.Index(fields=["item", "warehouse", "starts_on"])]

    def __str__(self):
        return (
            f"{self.quantity} {self.item.uom} {self.item.sku} "
            f"{self.starts_on}–{self.ends_on}"
        )

    def clean(self):
        self._check_no_overlap()

    def save(self, *args, **kwargs):
        # On save as well, because a forecast is as likely to be
        # loaded from a spreadsheet as typed, and Django does not call
        # full_clean for you.
        self._check_no_overlap()
        super().save(*args, **kwargs)

    def _check_no_overlap(self):
        clash = Forecast.objects.filter(
            item_id=self.item_id, warehouse_id=self.warehouse_id,
            starts_on__lte=self.ends_on, ends_on__gte=self.starts_on,
        ).exclude(pk=self.pk).first()
        if clash is not None:
            raise ValidationError(
                f"This overlaps {clash}. Two forecasts covering the same week "
                "would each be consumed by the same orders, and between them "
                "plan for twice the material."
            )

    def covers(self, on_date):
        return self.starts_on <= on_date <= self.ends_on

    def consumed(self):
        """
        Confirmed orders that fall inside this period.

        Counted whole rather than net of what has shipped: a forecast
        is about demand arriving, and an order that arrived and
        shipped consumed the forecast exactly as much as one still
        waiting.
        """
        from apps.sales.models import OrderStatus, SalesOrderLine

        lines = (
            SalesOrderLine.objects.filter(
                item_id=self.item_id, order__status=OrderStatus.CONFIRMED,
                charge__isnull=True,
            )
            .filter(warehouse__in=[self.warehouse, None])
            .select_related("order", "item", "uom")
        )
        return sum(
            (
                line.quantity_in_stock_units() for line in lines
                if self.covers(line.promised_date())
            ),
            ZERO,
        )

    def unconsumed(self):
        """What the forecast still expects over and above what came in."""
        return max(self.quantity - self.consumed(), ZERO)


def forecast_demand(item, warehouse, planned_on, horizon_end):
    """
    The unconsumed part of every forecast period inside the horizon.

    Dated at the start of its period, because a forecast for October
    means "we expect to ship this during October" and the material has
    to be there at the beginning to ship through the month. That is a
    deliberate bias towards being early, and it is the right way round
    for a figure nobody has ordered yet: too early costs a few days of
    stock and too late costs the order.

    A period already under way is dated at the plan's own date rather
    than at its start, which is in the past. A period entirely behind
    the plan contributes nothing: what was never ordered in September
    is never going to be.
    """
    from .models import DemandSource
    from .mrp import _demand

    rows = []
    forecasts = Forecast.objects.filter(
        item=item, warehouse=warehouse, is_active=True,
        ends_on__gte=planned_on, starts_on__lte=horizon_end,
    )
    for forecast in forecasts:
        left = forecast.unconsumed()
        if left <= 0:
            continue
        rows.append(_demand(
            max(forecast.starts_on, planned_on), left, DemandSource.FORECAST,
            forecast=forecast,
        ))
    return rows


def coverage(item, warehouse, planned_on=None, horizon_days=180):
    """
    Forecast against orders, period by period — the report that says
    whether a forecast is worth keeping.

    A plant whose forecasts are consistently double what arrives is
    carrying stock for orders that never come, and one whose forecasts
    are consistently short is the plant that keeps running out. Neither
    shows up anywhere else.
    """
    planned_on = planned_on or datetime.date.today()
    horizon_end = planned_on + datetime.timedelta(days=horizon_days)
    rows = []
    for forecast in Forecast.objects.filter(
        item=item, warehouse=warehouse, is_active=True,
        ends_on__gte=planned_on, starts_on__lte=horizon_end,
    ):
        consumed = forecast.consumed()
        rows.append({
            "forecast": forecast,
            "period": (forecast.starts_on, forecast.ends_on),
            "expected": forecast.quantity,
            "ordered": consumed,
            "unconsumed": max(forecast.quantity - consumed, ZERO),
            "over_ordered": max(consumed - forecast.quantity, ZERO),
            "accuracy_percent": (
                (consumed / forecast.quantity * Decimal("100")).quantize(
                    Decimal("0.01")
                )
                if forecast.quantity else None
            ),
        })
    return rows
