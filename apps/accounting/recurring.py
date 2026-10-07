"""
Recurring journals: the entry the books take every month whether or not
anyone remembers — the rent accrual, the insurance premium amortised,
the salary provision — written once as a schedule and taken from it on
its day.

Each run is an ordinary journal entry that knows the schedule it came
from, a draft unless the schedule posts it; nothing here reaches the
ledger by any door but `JournalEntry.post()`, so a closed period refuses
it like anything else and the schedule does not advance. The schedule
moves on by one interval a run, anchored to the day it started on, and a
schedule that fell behind catches up one entry a period, because each
period happened.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, serialised, to_date
from apps.core.recurrence import RecurrenceInterval, add_interval

ZERO = Decimal("0")


class RecurringJournal(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    memo = models.CharField(max_length=255, help_text="What each entry is for; written on every run.")
    interval = models.CharField(max_length=16, choices=RecurrenceInterval.choices,
                                default=RecurrenceInterval.MONTHLY)
    interval_count = models.PositiveSmallIntegerField(default=1, help_text="2 with monthly: every other month.")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Blank runs until stopped.")
    next_run_date = models.DateField(null=True, blank=True)
    auto_post = models.BooleanField(
        default=False, help_text="Post each entry as it is made; otherwise it is left a draft to check.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.memo}"

    def clean(self):
        self._check_terms()

    def _check_terms(self):
        if self.end_date and self.start_date and to_date(self.end_date) < to_date(self.start_date):
            raise ValidationError({"end_date": ["A schedule cannot end before it starts."]})
        if not self.interval_count:
            raise ValidationError({"interval_count": ["At least one interval passes between runs."]})

    def save(self, *args, **kwargs):
        self._check_terms()
        if self.next_run_date is None:
            self.next_run_date = to_date(self.start_date)
        super().save(*args, **kwargs)

    def has_finished(self):
        return bool(self.end_date and self.next_run_date and self.next_run_date > to_date(self.end_date))

    def is_due(self, on_date=None):
        day = to_date(on_date) or timezone.localdate()
        return bool(self.is_active and self.next_run_date and self.next_run_date <= day and not self.has_finished())

    @serialised("next_run_date", "is_active")
    def generate_one(self, on_date=None):
        """Take the next entry from the schedule and advance it; the entry, a draft unless the schedule posts."""
        from .models import JournalEntry, JournalLine

        if not self.is_active:
            raise ValidationError("This schedule is stopped.")
        lines = list(self.lines.select_related("account", "party", "cost_centre"))
        if not lines:
            raise ValidationError("This schedule has no lines to post.")
        debit = sum((line.debit for line in lines), ZERO)
        credit = sum((line.credit for line in lines), ZERO)
        if debit != credit:
            raise ValidationError(f"The schedule's lines do not balance: debits {debit} and credits {credit}.")
        if self.has_finished():
            raise ValidationError("This schedule has reached its end date.")
        entry = JournalEntry.objects.create(
            date=to_date(on_date) or self.next_run_date, reference=self.code, memo=self.memo, recurring_journal=self)
        for line in lines:
            JournalLine.objects.create(
                entry=entry, account=line.account, party=line.party, cost_centre=line.cost_centre,
                debit=line.debit, credit=line.credit, description=line.description)
        if self.auto_post:
            entry.post()
        self.next_run_date = add_interval(
            self.next_run_date, self.interval, self.interval_count, anchor_day=to_date(self.start_date).day)
        self.save(update_fields=["next_run_date", "updated_at"])
        return entry


class RecurringJournalLine(AuditModel):
    schedule = models.ForeignKey(RecurringJournal, related_name="lines", on_delete=models.CASCADE)
    account = models.ForeignKey("accounting.Account", related_name="+", on_delete=models.PROTECT)
    party = models.ForeignKey("core.Party", null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    cost_centre = models.ForeignKey("accounting.CostCentre", null=True, blank=True, related_name="+",
                                    on_delete=models.PROTECT)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(check=~(Q(debit__gt=0) & Q(credit__gt=0)),
                                   name="recurring_line_not_both_debit_and_credit"),
            models.CheckConstraint(check=Q(debit__gte=0) & Q(credit__gte=0),
                                   name="recurring_line_amounts_non_negative"),
        ]

    def __str__(self):
        return f"{self.account} D{self.debit}/C{self.credit}"

    def clean(self):
        self._check_sides()

    def save(self, *args, **kwargs):
        self._check_sides()
        super().save(*args, **kwargs)

    def _check_sides(self):
        if self.debit and self.credit:
            raise ValidationError("A line has either a debit or a credit, not both.")
        if not self.debit and not self.credit:
            raise ValidationError("A line has either a debit or a credit.")


def generate_due_journals(as_of=None):
    """
    Take every entry now due across the active schedules, one per period
    behind rather than a single lump: each period genuinely happened.

    Returns (made, refused): the entries taken, and [(code, why)] for each
    schedule that could not run — unbalanced, without lines, dated into a
    closed month. One schedule's fault stops that schedule, not the run,
    and is handed back rather than swallowed.
    """
    as_of = to_date(as_of) or timezone.localdate()
    made, refused = [], []
    for schedule in RecurringJournal.objects.filter(is_active=True).prefetch_related("lines"):
        while schedule.is_due(as_of):
            try:
                made.append(schedule.generate_one())
            except ValidationError as why:
                refused.append((schedule.code, " ".join(why.messages)))
                break
    return made, refused
