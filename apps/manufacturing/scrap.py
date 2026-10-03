"""
Where in a run the waste happened, and why.

A bag run is coat, print, cut and stitch on one order, and until now
the only figure it gave was a scrap total booked with the output at the
end. A thousand spoiled sacks said nothing about whether the printer
lost registration or the cutter wandered, and nothing about how much
cut fabric sat waiting for the stitchers.

**Scrap stays where it is costed.** A production entry's scrap is the
figure that is written off, and it remains the only record of how much
failed. What is added is its breakdown: lines saying why (a reason) and
at which step, never more than the entry's scrap. Scrap no line
explains is reported as unexplained, not hidden. A plant that wants
every sack explained switches `scrap_needs_reason` on and an entry
whose lines do not account for all of its scrap is refused.

**Steps before the last report what they passed on.** The last step's
output is what the run books, so it is never reported twice. Every
step takes in what it made good plus what it spoiled, and cannot take
more than the step before passed on:

    good(k) + scrap(k)  <=  good(k - 1)

checked when a step reports, when output is booked (the last step), and
when a report is withdrawn — a report the next step has already drawn
on cannot be. A step that has not reported bounds nothing: a plant that
counts at the printer but not at the coater is still told the truth
about the printer. Scrap given no step is the last step's.

Quantities are compared in the item's stocking unit: the run, the
report and the entry may each be written in another.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, to_date

ZERO = Decimal("0")


def _q(value):
    return format(Decimal(value).normalize(), "f")


class ScrapReason(AuditModel):
    """Why output failed: registration off, mis-cut, open seam, short weight."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProductionScrap(AuditModel):
    """Part of a production entry's scrap: how much, why, and at which step."""

    entry = models.ForeignKey("manufacturing.ProductionEntry", on_delete=models.CASCADE,
                              related_name="scrap_lines")
    reason = models.ForeignKey(ScrapReason, on_delete=models.PROTECT, related_name="lines")
    operation = models.ForeignKey(
        "manufacturing.WorkOrderOperation", null=True, blank=True,
        on_delete=models.PROTECT, related_name="scrap_lines",
        help_text="The step it failed at. Blank is the last step.",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4,
                                   help_text="In the entry's unit.")

    class Meta:
        ordering = ["entry", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0),
                                   name="production_scrap_positive"),
        ]

    def __str__(self):
        return f"{_q(self.quantity)} {self.reason.code}"

    def save(self, *args, **kwargs):
        if self.entry.posted:
            raise ValidationError(f"{self.entry} is posted; its scrap is as it was booked.")
        if self._state.adding and not self.reason.is_active:
            raise ValidationError(f"{self.reason} is no longer used.")
        if self.operation_id and self.operation.work_order_id != self.entry.work_order_id:
            raise ValidationError(f"{self.operation.name} is a step of "
                                  f"{self.operation.work_order}, not of "
                                  f"{self.entry.work_order}.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.posted:
            raise ValidationError(f"{self.entry} is posted; its scrap is as it was booked.")
        return super().delete(*args, **kwargs)

    def stock_quantity(self):
        return self.entry.work_order.item.to_stock_quantity(self.quantity, self.entry.uom)


class OperationReport(AuditModel):
    """Good output of one step of a run, counted before it moves on."""

    operation = models.ForeignKey("manufacturing.WorkOrderOperation",
                                  on_delete=models.PROTECT, related_name="reports")
    reported_on = models.DateField()
    quantity_good = models.DecimalField(max_digits=18, decimal_places=4,
                                        help_text="In the run's unit.")
    machine = models.ForeignKey("manufacturing.Machine", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    memo = models.CharField(max_length=255, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["operation", "reported_on", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity_good__gt=0),
                                   name="operation_report_positive"),
        ]

    def __str__(self):
        return f"{self.operation.name}: {_q(self.quantity_good)}"

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_voiding", False):
            raise ValidationError(f"{self} is a count as it was taken. Void it and "
                                  "report again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is a count as it was taken; void it.")

    def stock_quantity(self):
        order = self.operation.work_order
        return order.item.to_stock_quantity(self.quantity_good, order.uom)

    @transaction.atomic
    def void(self, reason):
        # What was counted is what is stored, not whatever this object has
        # been given since.
        self.refresh_from_db()
        if self.voided_at is not None:
            raise ValidationError(f"{self} is already void.")
        if not (reason or "").strip():
            raise ValidationError("Say why the count is withdrawn.")
        _lock(self.operation.work_order)
        after = _following(self.operation)
        good = _good(self.operation) - self.stock_quantity()
        if after is not None and _taken(after) > good:
            raise ValidationError(
                f"{after.name} has already taken {_q(_taken(after))} from "
                f"{self.operation.name}; without this count it passed on {_q(good)}."
            )
        self.voided_at, self.voided_reason = timezone.now(), reason.strip()
        self._voiding = True
        try:
            self.save(update_fields=["voided_at", "voided_reason", "updated_at"])
        finally:
            # Left set, the next save of this object would pass the guard too.
            self._voiding = False


def _lock(order):
    type(order).objects.select_for_update().filter(pk=order.pk).first()


def _steps(order):
    return list(order.operations.order_by("sequence", "id"))


def _last(order):
    return order.operations.order_by("-sequence", "-id").first()


def _previous(operation):
    steps = _steps(operation.work_order)
    index = [step.pk for step in steps].index(operation.pk)
    return steps[index - 1] if index > 0 else None


def _following(operation):
    steps = _steps(operation.work_order)
    index = [step.pk for step in steps].index(operation.pk)
    return steps[index + 1] if index + 1 < len(steps) else None


def _standing_entries(order):
    return order.entries.filter(posted=True, voided_at__isnull=True)


def _reported(operation):
    return operation.reports.filter(voided_at__isnull=True)


def _good(operation):
    """What a step passed on, in stock units. The last step's is what the run booked."""
    order = operation.work_order
    last = _last(order)
    if last is not None and operation.pk == last.pk:
        return sum((entry.stock_quantity() for entry in _standing_entries(order)), ZERO)
    return sum((report.stock_quantity() for report in _reported(operation)), ZERO)


def _scrap_at(operation, entries=None):
    """What a step spoiled, in stock units: lines naming it, and for the last
    step the lines naming no step and the scrap no line explains."""
    order = operation.work_order
    entries = list(_standing_entries(order)) if entries is None else entries
    last = _last(order)
    is_last = last is not None and operation.pk == last.pk
    total = ZERO
    for entry in entries:
        lines = list(entry.scrap_lines.all())
        total += sum((line.stock_quantity() for line in lines
                      if line.operation_id == operation.pk
                      or (is_last and line.operation_id is None)), ZERO)
        if is_last:
            explained = sum((line.stock_quantity() for line in lines), ZERO)
            total += entry.scrapped_stock_quantity() - explained
    return total


def _taken(operation, entries=None):
    return _good(operation) + _scrap_at(operation, entries)


def _has_reports(operation):
    last = _last(operation.work_order)
    if last is not None and operation.pk == last.pk:
        return _standing_entries(operation.work_order).exists()
    return _reported(operation).exists()


def _check_within(operation, taken):
    before = _previous(operation)
    if before is None or not _has_reports(before):
        return
    passed = _good(before)
    if taken > passed:
        raise ValidationError(
            f"{operation.name} would have taken {_q(taken)} and {before.name} has passed "
            f"on {_q(passed)}: {_q(taken - passed)} more than came to it."
        )


@transaction.atomic
def report(operation, quantity, on_date=None, machine=None, memo=""):
    """Count what a step has made good."""
    order = operation.work_order
    _lock(order)
    if not order.is_open():
        raise ValidationError(f"{order} is not running; nothing is passing its steps.")
    last = _last(order)
    if operation.pk == last.pk:
        raise ValidationError(
            f"{operation.name} is the last step: what it makes is the run's output. "
            "Book it as a production entry."
        )
    counted = OperationReport(operation=operation, reported_on=to_date(on_date or
                                                                        timezone.localdate()),
                              quantity_good=Decimal(str(quantity)), machine=machine,
                              memo=memo)
    if counted.quantity_good <= 0:
        raise ValidationError("A count of nothing is not a count.")
    if machine is not None and operation.work_centre_id:
        machine.check_in(operation.work_centre)
    _check_within(operation, _taken(operation) + counted.stock_quantity())
    following = _following(operation)
    passed = _good(operation) + counted.stock_quantity()
    if following is not None and _has_reports(following) and _taken(following) > passed:
        # The step after booked first. This count is the first moment the
        # two can be compared, and they disagree.
        raise ValidationError(
            f"{following.name} has already taken {_q(_taken(following))}; with this count "
            f"{operation.name} has passed on {_q(passed)}. Count what it actually made."
        )
    counted.save()
    return counted


def check_entry(entry):
    """As output is booked: its scrap lines, and the last step's intake."""
    from .orders import ManufacturingSettings

    lines = list(entry.scrap_lines.select_related("reason", "operation"))
    explained = sum((line.quantity for line in lines), ZERO)
    if explained > entry.quantity_scrapped:
        raise ValidationError(
            f"{_q(explained)} of scrap is explained and {_q(entry.quantity_scrapped)} "
            "was booked."
        )
    if ManufacturingSettings.get().scrap_needs_reason and explained < entry.quantity_scrapped:
        raise ValidationError(
            f"{_q(explained)} of {_q(entry.quantity_scrapped)} scrapped says why; every "
            "sack written off needs a reason here."
        )
    order = entry.work_order
    _lock(order)
    entries = list(_standing_entries(order)) + [entry]
    last = _last(order)
    # Scrap at an earlier step adds to what that step took in; it does not
    # change what it passed on, so only its own intake is checked.
    for step in {line.operation for line in lines if line.operation_id}:
        if step.pk != last.pk:
            _check_within(step, _taken(step, entries))
    if last is not None:
        _check_within(last, _good(last) + entry.stock_quantity() + _scrap_at(last, entries))


def flow(order):
    """Each step of a run: what it passed on, what it spoiled, what waits before it."""
    steps = _steps(order)
    rows, before = [], None
    for step in steps:
        good, scrap = _good(step), _scrap_at(step)
        reasons = defaultdict(lambda: ZERO)
        for entry in _standing_entries(order):
            for line in entry.scrap_lines.select_related("reason"):
                if line.operation_id == step.pk or (
                        line.operation_id is None and step is steps[-1]):
                    reasons[line.reason.code] += line.stock_quantity()
            if step is steps[-1]:
                unexplained = entry.scrapped_stock_quantity() - sum(
                    (line.stock_quantity() for line in entry.scrap_lines.all()), ZERO)
                if unexplained:
                    reasons["unexplained"] += unexplained
        reported = _has_reports(step)
        waiting = None
        if before is not None and before["reported"]:
            waiting = before["good"] - (good + scrap)
        rows.append({"sequence": step.sequence, "operation": step.name,
                     "work_centre": step.work_centre.code if step.work_centre_id else None,
                     "good": good, "scrap": scrap, "scrap_by_reason": dict(reasons),
                     "reported": reported, "waiting_before": waiting})
        before = rows[-1]
    return rows


def scrap_report(start, end):
    """Scrap booked between two dates, by item, step and reason, in stock units."""
    from .orders import ProductionEntry

    totals = defaultdict(lambda: ZERO)
    entries = ProductionEntry.objects.filter(
        posted=True, voided_at__isnull=True, quantity_scrapped__gt=0,
        entry_date__gte=to_date(start), entry_date__lte=to_date(end),
    ).select_related("work_order__item", "uom")
    for entry in entries:
        order = entry.work_order
        last = _last(order)
        last_name = last.name if last is not None else "-"
        explained = ZERO
        for line in entry.scrap_lines.select_related("reason", "operation"):
            quantity = line.stock_quantity()
            explained += quantity
            step = line.operation.name if line.operation_id else last_name
            totals[(order.item.sku, step, line.reason.code)] += quantity
        rest = entry.scrapped_stock_quantity() - explained
        if rest:
            totals[(order.item.sku, last_name, "unexplained")] += rest
    return [{"item": item, "operation": step, "reason": reason, "quantity": quantity}
            for (item, step, reason), quantity in sorted(totals.items())]
