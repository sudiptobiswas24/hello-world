"""
Months shipped before this system kept the deliveries, brought in from
the old one so the seasonal forecast has a past to read. A plant going
live in October would otherwise be proposed nothing until the next
October, when a year of its own deliveries had gone through here.

One figure an item, a warehouse and a month, in the item's stock unit,
net of returns. Only for months the system did not ship in itself: a
month with posted deliveries counts them already, and a history figure
on top would count the month twice. A figure is replaced, not added
to, so the file can be run again to correct a month.
"""

import calendar
import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel


class ShipmentHistory(AuditModel):
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="shipment_history")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipment_history")
    month = models.DateField(help_text="The first day of the month.")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="Shipped less returned that month, in the item's stock unit.")
    note = models.CharField(max_length=128, blank=True,
                            help_text="Where the figure came from: the old system's register, a ledger.")

    class Meta:
        ordering = ["item", "warehouse", "month"]
        verbose_name_plural = "shipment history"
        constraints = [
            models.UniqueConstraint(fields=["item", "warehouse", "month"], name="one_history_figure_a_month"),
            models.CheckConstraint(check=Q(quantity__gte=0), name="shipment_history_not_negative"),
        ]

    def __str__(self):
        return f"{self.item.sku} {self.month:%Y-%m} {self.quantity}"

    def save(self, *args, **kwargs):
        if self.month.day != 1:
            raise ValidationError({"month": "Give the first day of the month."})
        if self.month >= timezone.localdate().replace(day=1):
            raise ValidationError({"month": "History is a month that has ended."})
        if self.quantity < 0:
            raise ValidationError({"quantity": "Shipped less returned is nothing or more."})
        if shipped_in(self.item, self.warehouse, self.month):
            raise ValidationError({"month": f"{self.item.sku} shipped from {self.warehouse} in "
                                            f"{self.month:%B %Y}; the system counts that month itself."})
        super().save(*args, **kwargs)


def month_end(first):
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def shipped_in(item, warehouse, month):
    """Whether the system itself shipped the item from there that month: the same deliveries the forecast counts."""
    from apps.sales.models import DeliveryLine

    return DeliveryLine.objects.filter(
        order_line__item=item, warehouse=warehouse, delivery__posted=True,
        delivery__sales_order__is_job_work=False,
        delivery__delivery_date__gte=month, delivery__delivery_date__lte=month_end(month),
    ).exists()


def parse_month(value):
    """'2025-04' or '2025-04-01' (or 04-2025, 04/2025) as the month's first day; None if it is neither."""
    for shape in ("%Y-%m", "%Y-%m-%d", "%m-%Y", "%m/%Y"):
        try:
            return datetime.datetime.strptime(value, shape).date().replace(day=1)
        except ValueError:
            continue
    return None
