"""
The figures the law sets for payroll, as dated data: the EPF and EPS wage
ceilings and the overtime multiplier. Professional tax and the Labour
Welfare Fund are banded or monthly, so they are dated slabs of their own
components (PayComponentSlab.state, .effective_from); the rule for which
row stands on a day is the same.

A pay run reads the figure that stood on the last day of its period and
keeps it on the line it worked out (PayslipLine.kept_terms), so the ECR
and the registers read what the month was paid at, never today's table.
A row a posted run kept is not edited or deleted: the run is voided first,
or a new row is dated from when the new figure applies (audit shape "a
setting read live where a fact should be kept").

Nothing is seeded. With no row, the code's own figure applies, the one it
used before these were data (FIGURE_DEFAULTS), and the kept terms say it
was the default. Every figure here is for the plant's accountant to
confirm: the EPF wage ceiling reported to rise to 25,000 from 17
September 2026 (S.O. 5109(E)) is entered by them, not by the code.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from apps.core.models import AuditModel, lock_rows, to_date


class FigureKind(models.TextChoices):
    EPF_WAGE_CEILING = "epf_ceiling", "EPF wage ceiling, a month's wages"
    EPS_WAGE_CEILING = "eps_ceiling", "EPS (pension) wage ceiling, a month's wages"
    OVERTIME_MULTIPLIER = "overtime_multiplier", "Overtime, times the ordinary rate of wages"


# What the code used before these were data: EPS_CEILING and the PF component's 15,000, and the
# Factories Act's (and the OSH Code's) twice the ordinary rate. Defaults, not confirmed figures.
FIGURE_DEFAULTS = {
    FigureKind.EPF_WAGE_CEILING: Decimal("15000"),
    FigureKind.EPS_WAGE_CEILING: Decimal("15000"),
    FigureKind.OVERTIME_MULTIPLIER: Decimal("2"),
}
CEILINGS = (FigureKind.EPF_WAGE_CEILING, FigureKind.EPS_WAGE_CEILING)


class StatutoryFigure(AuditModel):
    """One figure the law sets, from the day it applies; blank state is every state."""

    kind = models.CharField(max_length=32, choices=FigureKind.choices)
    state = models.CharField(max_length=32, blank=True,
                             help_text="The state it applies in; blank applies in every state.")
    effective_from = models.DateField()
    value = models.DecimalField(max_digits=18, decimal_places=4)
    source = models.CharField(max_length=255, blank=True,
                              help_text="The notification, or who confirmed it: shown beside it on the registers.")

    class Meta:
        ordering = ["kind", "state", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "state", "effective_from"], name="statutory_figure_once_a_day"),
            models.CheckConstraint(check=Q(value__gt=0), name="statutory_figure_positive"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.value.normalize():f} from {self.effective_from}" + (
            f" ({self.state})" if self.state else "")

    def term(self):
        return [self.pk, self.effective_from.isoformat(), f"{self.value.normalize():f}"]

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.effective_from = to_date(self.effective_from)
        if self.pk is not None:
            lock_rows(self, refresh=False)
            stored = StatutoryFigure.objects.get(pk=self.pk)
            if (stored.kind, stored.state, stored.effective_from, stored.value) != (
                    self.kind, self.state, self.effective_from, self.value):
                refuse_if_kept(stored.kind, stored.pk, f"{stored}", "changed")
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        lock_rows(self, refresh=False)
        refuse_if_kept(self.kind, self.pk, f"{self}", "deleted")
        return super().delete(*args, **kwargs)


def figure_as_of(kind, day, state=""):
    """(value, kept term) of `kind` on `day`: the latest row from on or before it, the state's over every state's."""
    day = to_date(day)
    rows = StatutoryFigure.objects.filter(kind=kind, effective_from__lte=day).filter(
        Q(state="") | Q(state=state or "")).order_by("-effective_from", "-state")
    row = rows.first()
    if row is None:
        value = FIGURE_DEFAULTS[kind]
        return value, [None, None, f"{value.normalize():f}"]
    return row.value, row.term()


def refuse_if_kept(key, pk, what, doing):
    """
    Refuse changing a figure (or slab) a posted run kept: the run paid at
    it, and its files read it. `key` is the name the line keeps it under.
    """
    from .payroll import PayRunStatus, PayslipLine

    kept = PayslipLine.objects.filter(payslip__run__status=PayRunStatus.POSTED, kept_terms__has_key=key)
    for line in kept.select_related("payslip__run").only("kept_terms", "payslip__run__number"):
        term = (line.kept_terms or {}).get(key)
        if term and term[0] == pk:
            raise ValidationError(
                f"{what} is what {line.payslip.run} was paid at; it is not {doing}. Void that run first, "
                "or give the new figure a row of its own from the day it applies.")
