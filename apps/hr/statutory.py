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

The law's figures are loaded by `python manage.py setup_statutory` from the
dated files in statutory/ at the repository's root, each row with its
source, and unconfirmed. A row is read by nothing that posts until the
Controller confirms it (confirm(), kept with who and when): the latest row
on or before the day is the law of that day, and while it waits the run is
refused in words naming the figure and who confirms it, never paid at the
row before it nor at a default. With no row at all, the code's own figure
for the three it held before they were data applies (FIGURE_DEFAULTS), the
kept terms say so, and go_live_check fails until a confirmed row covers
the day. The rest have no default: bonus, ESI and the minimum wage are
refused without a confirmed row.

The bonus ceiling is the higher of the dated floor (7,000) and the minimum
wage of the person's scheduled employment, zone and skill on the period's
last day (MinimumWage), and only those whose month's wages are within the
dated eligibility ceiling (21,000) are paid it: section 26 of the Code on
Wages 2019, as the Central Government's notifications of 25 August 2026
(S.O. 4711(E)) set the figures; whether the State's notification sets
others for a West Bengal factory is for the accountant to confirm.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, lock_rows, to_date


class FigureKind(models.TextChoices):
    EPF_WAGE_CEILING = "epf_ceiling", "EPF wage ceiling, a month's wages"
    EPS_WAGE_CEILING = "eps_ceiling", "EPS (pension) wage ceiling, a month's wages"
    OVERTIME_MULTIPLIER = "overtime_multiplier", "Overtime, times the ordinary rate of wages"
    ESI_EMPLOYEE_RATE = "esi_employee_rate", "ESI, the employee's share, per cent of wages"
    ESI_EMPLOYER_RATE = "esi_employer_rate", "ESI, the employer's share, per cent of wages"
    ESI_WAGE_CEILING = "esi_ceiling", "ESI coverage ceiling, a month's wages"
    BONUS_MINIMUM_PERCENT = "bonus_minimum_percent", "Bonus, the least per cent of wages"
    BONUS_MAXIMUM_PERCENT = "bonus_maximum_percent", "Bonus, the most per cent of wages"
    BONUS_FLOOR = "bonus_floor", "Bonus calculation ceiling, the floor below the minimum wage"
    BONUS_ELIGIBILITY = "bonus_eligibility", "Bonus eligibility, a month's wages at most"


# What the code used before these were data: EPS_CEILING and the PF component's 15,000, and the
# Factories Act's (and the OSH Code's) twice the ordinary rate. Defaults, not confirmed figures.
FIGURE_DEFAULTS = {
    FigureKind.EPF_WAGE_CEILING: Decimal("15000"),
    FigureKind.EPS_WAGE_CEILING: Decimal("15000"),
    FigureKind.OVERTIME_MULTIPLIER: Decimal("2"),
}
CEILINGS = (FigureKind.EPF_WAGE_CEILING, FigureKind.EPS_WAGE_CEILING)
# The figures each engine's calculation reads, one list per engine: the pay run reads them from here and
# go_live_check asks each of them (statutory_check), so a figure added to a calculation is asked too.
PF_FIGURES = (FigureKind.EPF_WAGE_CEILING, FigureKind.EPS_WAGE_CEILING)
ESI_FIGURES = (FigureKind.ESI_EMPLOYEE_RATE, FigureKind.ESI_EMPLOYER_RATE, FigureKind.ESI_WAGE_CEILING)
BONUS_FIGURES = (FigureKind.BONUS_FLOOR, FigureKind.BONUS_ELIGIBILITY, FigureKind.BONUS_MINIMUM_PERCENT,
                 FigureKind.BONUS_MAXIMUM_PERCENT)
OVERTIME_FIGURES = (FigureKind.OVERTIME_MULTIPLIER,)
ENGINE_FIGURES = {"PF": PF_FIGURES, "ESI": ESI_FIGURES, "Bonus": BONUS_FIGURES, "Overtime": OVERTIME_FIGURES}
assert {kind for kinds in ENGINE_FIGURES.values() for kind in kinds} == set(FigureKind), \
    "every statutory figure belongs to the engine that reads it (ENGINE_FIGURES)"


def read_figure(engine, kind, day, state=""):
    """figure_as_of for `engine`'s calculation, refused for a figure the engine does not list (ENGINE_FIGURES)."""
    if kind not in ENGINE_FIGURES[engine]:
        raise AssertionError(f"{engine} reads {kind}, which ENGINE_FIGURES does not list for it: go_live_check "
                             "would not ask it.")
    return figure_as_of(kind, day, state)


# A component capped by the bonus rule: the higher of the dated floor and the person's minimum wage, paid only
# while a month's wages are within the dated eligibility ceiling (PayComponent.ceiling_figure).
BONUS_CEILING = "bonus_ceiling"
BONUS_CEILING_LABEL = "Bonus: the higher of the dated floor and the minimum wage, up to the eligibility ceiling"
# Who confirms a figure, as the refusals name them.
CONFIRMER = "the Controller"


def refuse_unconfirmed(what, since, screen):
    raise ValidationError(
        f"{what} {f'from {since:%d %b %Y}' if since else 'in force from the start'} is not confirmed, so nothing that posts may use it yet: {CONFIRMER} "
        f"confirms it, or corrects it, under {screen}.")


class Confirmed(models.Model):
    """
    The Controller's word that a figure is the law's, with who and when.
    A changed figure is a new figure: its confirmation is cleared and it
    is confirmed again. FIGURES names what a confirmation vouches for.
    """

    FIGURES = ()
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)
    confirmed_on = models.DateField(null=True, blank=True, editable=False,
                                    help_text="When the Controller confirmed it. Until then nothing posts at it.")

    class Meta:
        abstract = True

    def figures(self):
        return {name: getattr(self, name) for name in self.FIGURES}

    def clear_if_changed(self, stored):
        if stored.figures() != self.figures():
            self.confirmed_by, self.confirmed_on = None, None

    def confirm(self, user, seen=None):
        """
        Confirm the figures as `seen` (as the reader read them; by default as
        this object holds them), refused if the row has changed since or is
        confirmed already (the TDS rate's rule, accounting.dated_rates).
        """
        from apps.accounting.dated_rates import _same

        read = seen if seen is not None else self.figures()
        with transaction.atomic():
            lock_rows(self, refresh=False)
            stored = type(self).objects.get(pk=self.pk)
            if stored.confirmed_on is not None:
                raise ValidationError(f"{stored} is already confirmed, on {stored.confirmed_on:%d %b %Y}.")
            moved = [name.removesuffix("_id").replace("_", " ") for name in self.FIGURES
                     if not _same(getattr(stored, name), read.get(name))]
            if moved:
                raise ValidationError(f"{stored} is not what was read: its {', '.join(moved)} differ. Read it "
                                      "again, and confirm what it says now.")
            type(self).objects.filter(pk=self.pk).update(confirmed_by=user, confirmed_on=timezone.localdate())
        self.confirmed_by, self.confirmed_on = user, timezone.localdate()


class StatutoryFigure(Confirmed, AuditModel):
    """One figure the law sets, from the day it applies; blank state is every state."""

    FIGURES = ("kind", "state", "effective_from", "value")

    kind = models.CharField(max_length=32, choices=FigureKind.choices)
    state = models.CharField(max_length=32, blank=True,
                             help_text="The state it applies in; blank applies in every state.")
    effective_from = models.DateField()
    value = models.DecimalField(max_digits=18, decimal_places=4)
    source = models.CharField(max_length=255, blank=True,
                              help_text="The notification, or who confirmed it: shown beside it on the registers.")

    class Meta:
        ordering = ["kind", "state", "-effective_from"]
        permissions = [("confirm_statutoryfigure", "Can confirm a statutory figure as the law's")]
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
            self.clear_if_changed(stored)
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        # FOR UPDATE: the row is deleted, and a row being inserted that names it must wait (O257).
        lock_rows(self, refresh=False, deleting=True)
        refuse_if_kept(self.kind, self.pk, f"{self}", "deleted")
        return super().delete(*args, **kwargs)


# The pension scheme's age: EPS is paid for the month a member turns 58 in and not after; the whole
# employer share then goes to the provident fund. The law's rule, kept here; the date of birth the run
# read is kept on the slip's PF lines (kept_terms["born"]). To confirm with the accountant.
PENSION_AGE = 58


def pension_stops(born, period_end):
    """Whether the month ending `period_end` is after the month the member born on `born` turns 58 in."""
    born, period_end = to_date(born), to_date(period_end)
    return (period_end.year, period_end.month) > (born.year + PENSION_AGE, born.month)


def figure_standing(kind, day, state=""):
    """The row of `kind` that is the law on `day`: the latest from on or before it, the state's over every state's."""
    return StatutoryFigure.objects.filter(kind=kind, effective_from__lte=to_date(day)).filter(
        Q(state="") | Q(state=state or "")).order_by("-effective_from", "-state").first()


def figure_as_of(kind, day, state=""):
    """
    (value, kept term) of `kind` on `day`, from the row standing then once
    the Controller has confirmed it; refused in words while it waits. With
    no row, the code's own figure where it had one (FIGURE_DEFAULTS).
    """
    day = to_date(day)
    row = figure_standing(kind, day, state)
    if row is None:
        if kind not in FIGURE_DEFAULTS:
            raise ValidationError(
                f"{FigureKind(kind).label} has no figure from {day:%d %b %Y} or before, so this cannot be worked "
                f"out: load it (python manage.py setup_statutory) and have {CONFIRMER} confirm it, under "
                "People > Statutory figures.")
        value = FIGURE_DEFAULTS[kind]
        return value, [None, None, f"{value.normalize():f}"]
    if row.confirmed_on is None:
        refuse_unconfirmed(f"{row.get_kind_display()} of {row.value.normalize():f}"
                           + (f" in {row.state}" if row.state else ""), row.effective_from,
                           "People > Statutory figures")
    return row.value, row.term()


class Skill(models.TextChoices):
    UNSKILLED = "unskilled", "Unskilled"
    SEMI_SKILLED = "semi_skilled", "Semi-skilled"
    SKILLED = "skilled", "Skilled"
    HIGHLY_SKILLED = "highly_skilled", "Highly skilled"


class MinimumWage(Confirmed, AuditModel):
    """
    A month's minimum rate of wages for one scheduled employment, zone and
    skill in one state, from the day it applies: the basic rate and the
    variable dearness allowance it carries from that day. A VDA revision
    (West Bengal's are from 1 January and 1 July) is a new row from its day.
    """

    FIGURES = ("state", "employment", "zone", "skill", "effective_from", "basic", "vda")
    state = models.CharField(max_length=32, help_text="The state that notifies it, as Employee.work_state names it.")
    employment = models.CharField(max_length=128, help_text="The scheduled employment, as the notification names it.")
    zone = models.CharField(max_length=32, blank=True, help_text="The zone or area; blank is the whole state.")
    skill = models.CharField(max_length=16, choices=Skill.choices)
    effective_from = models.DateField()
    basic = models.DecimalField(max_digits=18, decimal_places=2, help_text="A month's basic minimum rate.")
    vda = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"),
                              help_text="A month's variable dearness allowance from this day.")
    source = models.CharField(max_length=255, blank=True, help_text="The notification, or who confirmed it.")

    class Meta:
        ordering = ["state", "employment", "zone", "skill", "-effective_from"]
        permissions = [("confirm_minimumwage", "Can confirm a minimum wage as the law's")]
        constraints = [
            models.UniqueConstraint(fields=["state", "employment", "zone", "skill", "effective_from"],
                                    name="minimum_wage_once_a_day"),
            models.CheckConstraint(check=Q(basic__gt=0) & Q(vda__gte=0), name="minimum_wage_positive"),
        ]

    def __str__(self):
        where = f"{self.state}{' ' + self.zone if self.zone else ''}"
        return f"Minimum wage {self.employment}, {self.get_skill_display().lower()}, {where}: " \
               f"{self.monthly.normalize():f} from {self.effective_from}"

    @property
    def monthly(self):
        return self.basic + self.vda

    def term(self):
        return [self.pk, self.effective_from.isoformat(), f"{self.monthly.normalize():f}"]

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.effective_from = to_date(self.effective_from)
        if self.pk is not None:
            lock_rows(self, refresh=False)
            stored = MinimumWage.objects.get(pk=self.pk)
            if stored.figures() != self.figures():
                refuse_if_kept("minimum_wage", stored.pk, f"{stored}", "changed")
            self.clear_if_changed(stored)
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        lock_rows(self, refresh=False, deleting=True)
        refuse_if_kept("minimum_wage", self.pk, f"{self}", "deleted")
        return super().delete(*args, **kwargs)


def minimum_wage_on(employee, day):
    """(the month's minimum wage, kept term) of `employee`'s employment, zone and skill on `day`, confirmed."""
    day = to_date(day)
    details = getattr(employee, "pay_details", None)
    employment = getattr(details, "scheduled_employment", "") if details is not None else ""
    skill = getattr(details, "skill", "") if details is not None else ""
    zone = getattr(details, "wage_zone", "") if details is not None else ""
    if not (employee.work_state and employment and skill):
        raise ValidationError(
            f"{employee}'s bonus is worked out on the minimum wage of their scheduled employment, and their "
            "state, scheduled employment or skill is not set. Set them in their pay details and the state they "
            "work in.")
    row = MinimumWage.objects.filter(
        state=employee.work_state, employment=employment, skill=skill, zone__in=(zone, ""),
        effective_from__lte=day).order_by("-effective_from", "-zone").first()
    what = f"the minimum wage for {employment}, {Skill(skill).label.lower()}, in {employee.work_state}" + (
        f" {zone}" if zone else "")
    if row is None:
        raise ValidationError(f"There is no {what} from {day:%d %b %Y} or before. Load it (python manage.py "
                              f"setup_statutory) and have {CONFIRMER} confirm it, under People > Minimum wages.")
    if row.confirmed_on is None:
        refuse_unconfirmed(f"{what[0].upper()}{what[1:]} of {row.monthly.normalize():f}", row.effective_from,
                           "People > Minimum wages")
    return row.monthly, row.term()


def bonus_ceiling(employee, day):
    """(ceiling, kept terms) of the bonus rule on `day`: the higher of the dated floor and the minimum wage."""
    floor, floor_term = read_figure("Bonus", FigureKind.BONUS_FLOOR, day, employee.work_state)
    wage, wage_term = minimum_wage_on(employee, day)
    return max(floor, wage), {FigureKind.BONUS_FLOOR.value: floor_term, "minimum_wage": wage_term}


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
