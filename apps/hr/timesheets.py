"""
Hours worked.

Payroll can pay by the hour and had no idea how many anybody did — the
hours had to be handed to `calculate()` from outside, which means they
came from a spreadsheet, which means nothing in this system could say
where a number on a payslip came from.

A timesheet is an approved claim about time, and it is the same shape as
a leave request: somebody states it, somebody else agrees to it, and
afterwards it is a fact that other things depend on. So it has the same
guards, for the same reasons — a submission cannot be edited once
approved, an approval has a way back, and nobody signs off their own.

What it deliberately does not try to be is a projects module. There is
no project, no task and no billing rate here; an entry names an account
to charge and a free-text reference, which is enough for cost analysis
and honest about where the line is.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, to_date

from .calendars import parse_working_days
from .models import Employee, LeaveRequest, LeaveStatus

# A day has twenty-four hours and nobody works all of them. The cap is
# not about labour law, which differs everywhere — it is about catching
# the typed 80 that was meant to be 8.
MAX_HOURS_PER_DAY = Decimal("24")


class TimesheetStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Timesheet(AuditModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="timesheets")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(
        max_length=16, choices=TimesheetStatus.choices, default=TimesheetStatus.DRAFT
    )
    note = models.CharField(max_length=255, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        editable=False,
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-period_start", "employee"]
        permissions = [("decide_timesheet", "Can approve or reject timesheets")]
        constraints = [
            models.CheckConstraint(
                check=Q(period_end__gte=models.F("period_start")),
                name="timesheet_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.employee} {self.period_start}..{self.period_end} [{self.status}]"

    def is_open(self):
        """States that hold a claim on the days they cover."""
        return self.status in (
            TimesheetStatus.DRAFT, TimesheetStatus.SUBMITTED, TimesheetStatus.APPROVED
        )

    def hours(self):
        total = self.entries.aggregate(total=Sum("hours"))["total"]
        return total or Decimal("0")

    def billable_hours(self):
        total = self.entries.filter(is_billable=True).aggregate(total=Sum("hours"))["total"]
        return total or Decimal("0")

    def clean(self):
        self.period_start = to_date(self.period_start)
        self.period_end = to_date(self.period_end)
        if self.period_end < self.period_start:
            raise ValidationError("A timesheet cannot end before it starts.")
        if self.employee_id:
            self._check_within_employment()
            self._check_no_overlap()

    def _check_within_employment(self):
        if not self.employee.is_employed_on(self.period_end):
            raise ValidationError(
                f"{self.employee} is not employed on {self.period_end}; time cannot be "
                "claimed outside employment."
            )

    def _check_no_overlap(self):
        """
        One timesheet per person per stretch of days.

        Two sheets covering the same week is the double-processing shape:
        both get approved, both feed payroll, and the hours are paid
        twice with nothing about either sheet saying so.
        """
        if not self.is_open():
            return
        clashes = Timesheet.objects.filter(
            employee=self.employee,
            status__in=(
                TimesheetStatus.DRAFT, TimesheetStatus.SUBMITTED, TimesheetStatus.APPROVED
            ),
            period_start__lte=self.period_end,
            period_end__gte=self.period_start,
        )
        if self.pk:
            clashes = clashes.exclude(pk=self.pk)
        clash = clashes.first()
        if clash is not None:
            raise ValidationError(
                f"{self.employee} already has a timesheet covering "
                f"{clash.period_start} to {clash.period_end}."
            )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = Timesheet.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status == TimesheetStatus.APPROVED:
                if (
                    previous.period_start != to_date(self.period_start)
                    or previous.period_end != to_date(self.period_end)
                ):
                    raise ValidationError(
                        "This timesheet has been approved; its period can no longer "
                        "change. Send it back first."
                    )
        self.clean()
        super().save(*args, **kwargs)

    # -- the decision, and its reverse ----------------------------------

    @transaction.atomic
    def submit(self):
        if self.status != TimesheetStatus.DRAFT:
            raise ValidationError("Only a draft timesheet can be submitted.")
        if not self.entries.exists():
            raise ValidationError("There is no time on this sheet to submit.")
        self.status = TimesheetStatus.SUBMITTED
        self.submitted_at = timezone.now()
        super().save(update_fields=["status", "submitted_at", "updated_at"])
        return self

    def check_approver(self, by):
        if by is None:
            raise ValidationError("Somebody has to decide this; say who.")
        if by.pk == self.employee_id:
            raise ValidationError(f"{self.employee} cannot approve their own hours.")

    @transaction.atomic
    def approve(self, by):
        if self.status != TimesheetStatus.SUBMITTED:
            raise ValidationError("Only a submitted timesheet can be approved.")
        self.check_approver(by)
        self.status = TimesheetStatus.APPROVED
        self.decided_by = by
        self.decided_at = timezone.now()
        super().save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        return self

    @transaction.atomic
    def reject(self, by, note=""):
        if self.status != TimesheetStatus.SUBMITTED:
            raise ValidationError("Only a submitted timesheet can be rejected.")
        self.check_approver(by)
        self.status = TimesheetStatus.REJECTED
        self.decided_by = by
        self.decided_at = timezone.now()
        if note:
            self.note = note
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "note", "updated_at",
        ])
        return self

    @transaction.atomic
    def send_back(self, note=""):
        """
        Put an approved or rejected sheet back in the author's hands.

        The mirror of approve(), written with it. Without it an approval
        given in error can only be worked around by raising a second
        sheet for the same week, which the overlap check correctly
        refuses — leaving no way forward at all.
        """
        if self.status not in (TimesheetStatus.APPROVED, TimesheetStatus.REJECTED):
            raise ValidationError("Only a decided timesheet can be sent back.")
        if self.status == TimesheetStatus.APPROVED and self.is_paid():
            raise ValidationError(
                "These hours have already been paid. Void the pay run before sending "
                "the timesheet back."
            )
        self.status = TimesheetStatus.DRAFT
        self.decided_by = None
        self.decided_at = None
        self.submitted_at = None
        if note:
            self.note = note
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "submitted_at", "note", "updated_at",
        ])
        return self

    def is_paid(self):
        """
        Whether a posted pay run has already covered this period.

        Hours that have been paid are not a draft any more, whatever the
        sheet says, and editing them would leave a payslip nobody can
        reproduce.
        """
        from .payroll import PayRun, PayRunStatus

        return PayRun.objects.filter(
            status=PayRunStatus.POSTED,
            voided_at__isnull=True,
            period_start__lte=self.period_end,
            period_end__gte=self.period_start,
            payslips__employee_id=self.employee_id,
        ).exists()


class TimesheetEntry(AuditModel):
    timesheet = models.ForeignKey(Timesheet, on_delete=models.CASCADE, related_name="entries")
    date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2)
    account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="What this time is charged against, for cost analysis. Not posted "
                  "from here — payroll posts the money, this records where it went.",
    )
    reference = models.CharField(
        max_length=64, blank=True,
        help_text="Job, ticket or customer this was for. Free text, because there "
                  "is no projects module here to point at.",
    )
    is_billable = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(check=Q(hours__gt=0), name="timesheet_hours_positive"),
            models.CheckConstraint(
                check=Q(hours__lte=MAX_HOURS_PER_DAY), name="timesheet_hours_within_a_day"
            ),
        ]
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"{self.date} {self.hours}h"

    def clean(self):
        self.date = to_date(self.date)
        sheet = self.timesheet
        if not (sheet.period_start <= self.date <= sheet.period_end):
            raise ValidationError(
                f"{self.date} is outside this timesheet's period "
                f"({sheet.period_start} to {sheet.period_end})."
            )
        if not sheet.employee.is_employed_on(self.date):
            raise ValidationError(
                f"{sheet.employee} was not employed on {self.date}."
            )
        self._check_day_total()
        self._check_not_on_leave()

    def _check_day_total(self):
        others = TimesheetEntry.objects.filter(timesheet=self.timesheet, date=self.date)
        if self.pk:
            others = others.exclude(pk=self.pk)
        already = others.aggregate(total=Sum("hours"))["total"] or Decimal("0")
        if already + self.hours > MAX_HOURS_PER_DAY:
            raise ValidationError(
                f"{self.date} would come to {already + self.hours} hours. A day has "
                f"{MAX_HOURS_PER_DAY}."
            )

    def _check_not_on_leave(self):
        """
        Time cannot be claimed for a day already taken as leave.

        Both are claims on the same day and both feed pay. Somebody on
        approved holiday who also files eight hours is paid twice for it,
        and the two records each look perfectly reasonable on their own.
        """
        clash = LeaveRequest.objects.filter(
            employee=self.timesheet.employee,
            status=LeaveStatus.APPROVED,
            start_date__lte=self.date,
            end_date__gte=self.date,
        ).first()
        if clash is not None:
            raise ValidationError(
                f"{self.timesheet.employee} is on approved {clash.get_leave_type_display().lower()} "
                f"leave on {self.date}; time cannot also be claimed for it."
            )

    def save(self, *args, **kwargs):
        if self.timesheet_id:
            status = Timesheet.objects.filter(pk=self.timesheet_id).values_list(
                "status", flat=True
            ).first()
            if status == TimesheetStatus.APPROVED:
                raise ValidationError(
                    "This timesheet has been approved; its hours can no longer change. "
                    "Send it back first."
                )
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.timesheet.status == TimesheetStatus.APPROVED:
            raise ValidationError(
                "This timesheet has been approved; its hours can no longer be removed."
            )
        super().delete(*args, **kwargs)


def approved_hours(employee, start, end):
    """
    Hours this person has had signed off within a range.

    Approved only. Payroll reads this, and paying for hours a manager has
    not agreed to is the whole reason the approval step exists.
    """
    start, end = to_date(start), to_date(end)
    total = TimesheetEntry.objects.filter(
        timesheet__employee=employee,
        timesheet__status=TimesheetStatus.APPROVED,
        date__gte=start, date__lte=end,
    ).aggregate(total=Sum("hours"))["total"]
    return total or Decimal("0")


def hours_by_account(start, end, employee=None, billable=None):
    """Where signed-off time went, for cost analysis."""
    rows = TimesheetEntry.objects.filter(
        timesheet__status=TimesheetStatus.APPROVED,
        date__gte=to_date(start), date__lte=to_date(end),
    )
    if employee is not None:
        rows = rows.filter(timesheet__employee=employee)
    if billable is not None:
        rows = rows.filter(is_billable=billable)
    grouped = rows.values("account", "account__code", "account__name").annotate(
        hours=Sum("hours")
    ).order_by("account__code")
    return [
        {
            "account_id": row["account"],
            "code": row["account__code"],
            "name": row["account__name"],
            "hours": row["hours"],
        }
        for row in grouped
    ]
