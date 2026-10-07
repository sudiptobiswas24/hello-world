"""
The master schedule: what the plant commits to make, week by week,
whether or not anybody has ordered it yet.

Planning from demand makes things when they are wanted. A sack plant
cannot live on that alone: cement sacks peak before the monsoon, and
twelve looms cannot weave May's orders in May. The master schedule is
where a planner decides to build ahead — so many sacks in week 16 —
and has the plan treat that as settled.

**Committing is a draft run.** A committed entry becomes a draft work
order dated in its week, and a draft run is already everything the plan
needs: supply for the item, demand for its components, hours on the
machines. Nothing about the plan has to learn a second kind of supply.

**Rough-cut first.** Before committing, the week's machine time for
the quantity (at each machine's achieved speed) is set against what
the machines still have free that week. More than they have is refused
unless the planner commits it anyway and says why: an overloaded week
is sometimes the right call — overtime, a Sunday — and never an
accident.

**Withdrawn** while its run is still a draft; once released, the run is
on the floor and is cancelled or closed there.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel, lock_rows, serialised, to_date

ZERO = Decimal("0")
WEEK = datetime.timedelta(days=7)


def _q(value):
    return format(Decimal(value).normalize(), "f")


class MasterScheduleEntry(AuditModel):
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  related_name="+")
    week_of = models.DateField(help_text="The Monday of the week it is to be made in.")
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the item's stocking unit.")
    reason = models.CharField(max_length=255,
                              help_text="Why it is scheduled: building ahead of a peak, "
                                        "a customer's call-off expected, a stock target.")
    work_order = models.ForeignKey("manufacturing.WorkOrder", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+",
                                   editable=False)
    committed_at = models.DateTimeField(null=True, blank=True, editable=False)
    overload_accepted = models.CharField(
        max_length=255, blank=True, editable=False,
        help_text="Said when it was committed past what the machines had free.")
    withdrawn_at = models.DateTimeField(null=True, blank=True, editable=False)
    withdrawn_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["week_of", "item", "id"]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gt=0),
                                   name="master_schedule_quantity_positive"),
        ]

    def __str__(self):
        return f"{_q(self.quantity)} {self.item.sku} in the week of {self.week_of}"

    def week_end(self):
        return self.week_of + datetime.timedelta(days=6)

    def is_standing(self):
        return self.committed_at is not None and self.withdrawn_at is None

    def save(self, *args, **kwargs):
        if self.pk and MasterScheduleEntry.objects.filter(
                pk=self.pk, committed_at__isnull=False).exists() \
                and not getattr(self, "_writing", False):
            raise ValidationError(f"{self} is committed. Withdraw it and schedule again.")
        self.week_of = to_date(self.week_of)
        if self.week_of.weekday() != 0:
            raise ValidationError(f"{self.week_of} is not a Monday; a schedule week starts "
                                  "on one.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.committed_at is not None:
            raise ValidationError(f"{self} is committed; withdraw it.")
        return super().delete(*args, **kwargs)

    def _write(self, fields):
        self._writing = True
        try:
            self.save(update_fields=fields + ["updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._writing = False

    def bom(self):
        from apps.manufacturing.bom import default_bom_for

        bom = default_bom_for(self.item, self.week_of)
        if bom is None or bom.is_phantom:
            raise ValidationError(f"{self.item} has no recipe to make it by in the week of "
                                  f"{self.week_of}; only what the plant makes is scheduled.")
        return bom

    def rough_cut(self, on_date=None):
        """Per machine: minutes this needs in its week, and minutes free there."""
        from .capacity import LoadBook

        bom = self.bom()
        if bom.routing_id is None:
            return []
        start = max(self.week_of, to_date(on_date) or timezone.localdate())
        book = LoadBook(self.warehouse, start, self.week_end())
        quantity = bom.start_for(self.quantity)
        if self.item.uom_id != bom.uom_id:
            quantity = self.item.uom.convert_to(quantity, bom.uom)
        rows = {}
        for operation in bom.routing.operations.select_related("work_centre"):
            if operation.is_outside:
                continue
            centre = operation.work_centre
            row = rows.setdefault(centre.pk, {"work_centre": centre, "needed": ZERO,
                                              "free": ZERO})
            row["needed"] += operation.minutes_for(quantity, bom.uom, bom=bom)
        day = start
        while day <= self.week_end():
            for row in rows.values():
                row["free"] += book.free(row["work_centre"], day)
            day += datetime.timedelta(days=1)
        return list(rows.values())

    @serialised("committed_at", "withdrawn_at", "work_order")
    def commit(self, accept_overload="", on_date=None):
        from apps.manufacturing.orders import WorkOrder

        today = to_date(on_date) or timezone.localdate()
        if self.committed_at is not None:
            raise ValidationError(f"{self} is already committed.")
        if not (self.reason or "").strip():
            raise ValidationError("Say why it is scheduled.")
        if self.week_end() < today:
            raise ValidationError(f"The week of {self.week_of} has gone.")
        bom = self.bom()
        over = [row for row in self.rough_cut(today) if row["needed"] > row["free"]]
        if over and not (accept_overload or "").strip():
            raise ValidationError(" ".join(
                f"{row['work_centre'].code} needs {row['needed']:.0f} minutes that week and "
                f"has {row['free']:.0f} free." for row in over
            ) + " Commit it anyway only saying why.")
        self.work_order = WorkOrder.objects.create(
            item=self.item, bom=bom, quantity_ordered=self.quantity, uom=self.item.uom,
            warehouse=self.warehouse, routing=bom.routing,
            scheduled_start=max(self.week_of, today),
            scheduled_end=self.week_end(),
        )
        self.overload_accepted = (accept_overload or "").strip() if over else ""
        self.committed_at = timezone.now()
        self.reason = self.reason.strip()
        self._write(["work_order", "overload_accepted", "committed_at", "reason"])

    @serialised("committed_at", "withdrawn_at", "work_order")
    def withdraw(self, reason):
        from apps.manufacturing.orders import WorkOrderStatus

        if not self.is_standing():
            raise ValidationError(f"{self} is not a standing commitment.")
        if not (reason or "").strip():
            raise ValidationError("Say why it is withdrawn.")
        order = self.work_order
        # Held and read again: released on the floor since it was read, it is the floor's to cancel.
        lock_rows(order)
        if order.status != WorkOrderStatus.DRAFT:
            raise ValidationError(f"{order} has been released; cancel or close it on the "
                                  "floor.")
        order.cancel()
        self.withdrawn_at, self.withdrawn_reason = timezone.now(), reason.strip()
        self._write(["withdrawn_at", "withdrawn_reason"])


def schedule_view(item, warehouse, start, weeks=8, on_date=None):
    """
    Week by week: what is wanted, what is coming (and how much of that
    the master schedule committed), and the stock that leaves.
    """
    from .forecast import forecast_demand
    from .mrp import (
        opening_balance,
        purchase_supply,
        sales_demand,
        work_order_demand,
        work_order_supply,
    )

    start = to_date(start)
    start -= datetime.timedelta(days=start.weekday())
    end = start + WEEK * weeks - datetime.timedelta(days=1)
    today = to_date(on_date) or timezone.localdate()
    demands = (sales_demand(item, warehouse, today)
               + forecast_demand(item, warehouse, today, end)
               + work_order_demand(item, warehouse, today))
    supplies = purchase_supply(item, warehouse, today) + work_order_supply(item, warehouse,
                                                                           today)
    committed = {}
    for entry in MasterScheduleEntry.objects.filter(
            item=item, warehouse=warehouse, committed_at__isnull=False,
            withdrawn_at__isnull=True, week_of__gte=start, week_of__lte=end):
        committed[entry.week_of] = committed.get(entry.week_of, ZERO) + entry.quantity
    balance = opening_balance(item, warehouse, today)
    rows = []
    for index in range(weeks):
        week = start + WEEK * index
        last = week + datetime.timedelta(days=6)

        def within(row):
            when = max(row.date, today)
            return (week <= when <= last) or (index == 0 and when < week)

        wanted = sum((row.quantity for row in demands if within(row)), ZERO)
        coming = sum((row.quantity for row in supplies if within(row)), ZERO)
        balance += coming - wanted
        rows.append({"week_of": week, "wanted": wanted, "coming": coming,
                     "of_which_scheduled": committed.get(week, ZERO), "projected": balance})
    return rows
