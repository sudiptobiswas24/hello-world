"""
Payroll, and the ledger it has to reach.

HR modelled people and their days off and posted nothing, ever — which
for most companies in this size range leaves the single largest expense
line out of the accounts entirely.

What this does not do, said plainly rather than left to be discovered:
it does not calculate statutory tax. PAYE bands, national insurance
thresholds, state withholding and their dozen jurisdictions are a
product in themselves, they change every year, and a wrong one is worse
than none because it looks authoritative. Tax here is a pay component
like any other — a fixed amount or a percentage somebody supplies — and
the entry it posts is correct whatever produced the number.

The shape of the entry, which is the part that has to be right:

    Dr  Wages expense            gross earnings
    Dr  Employer cost expense    employer's own contributions
        Cr  Each deduction's liability account
        Cr  Employer contribution liabilities
        Cr  Net pay payable      what actually reaches people

Net pay payable is a control account, so it has to clear. Paying a run
settles it; until then the balance is what the company owes its staff,
and a payroll module that leaves a permanent balance there has lost
track of somebody's wages.
"""

import datetime
from collections import Counter
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from itertools import groupby

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.accounting.models import (
    JournalEntry,
    JournalLine,
    PaymentDirection,
    round_money,
)
from apps.core.models import (
    AuditModel,
    documents_it_answers_to,
    Company,
    correction_date,
    DocumentSequence,
    lock_rows,
    serialised,
    to_date,
)

from .calendars import working_days
from .models import Employee, LeaveRequest, LeaveStatus, hold_the_employee
from .statutory import CEILINGS, FigureKind, figure_as_of, pension_stops, refuse_if_kept


class ComponentKind(models.TextChoices):
    EARNING = "earning", "Earning"
    DEDUCTION = "deduction", "Employee deduction"
    EMPLOYER_COST = "employer_cost", "Employer cost"


class ComponentBasis(models.TextChoices):
    FIXED = "fixed", "Fixed amount"
    PERCENT_OF_GROSS = "percent", "Percentage of its base (taxable gross unless named)"
    PER_HOUR = "per_hour", "Rate per hour"
    PER_UNIT = "per_unit", "Rate per unit produced"
    PER_OVERTIME_HOUR = "overtime", "Rate per overtime hour on the attendance register"
    SLAB = "slab", "Amount from a slab of its base"
    OVERTIME_ORDINARY = "ot_ordinary", "Overtime hours at the multiple of the ordinary rate of wages"


class Rounding(models.TextChoices):
    PAISA = "paisa", "To the paisa"
    RUPEE = "rupee", "To the nearest rupee"
    RUPEE_UP = "rupee_up", "Up to the next rupee"


class Statutory(models.TextChoices):
    """Which statutory line a component is, for the monthly files (statutory_files.py)."""

    NONE = "", "Not a statutory line"
    PF = "pf", "Provident fund, the employee's share"
    PF_EMPLOYER = "pf_employer", "Provident fund, the employer's share (pension inside it unless named)"
    EPS = "eps", "Pension (EPS), the employer's share named on its own"
    ESI = "esi", "ESI, the employee's share"
    ESI_EMPLOYER = "esi_employer", "ESI, the employer's share"
    PT = "pt", "Professional tax"
    LWF = "lwf", "Labour welfare fund, the employee's share"
    LWF_EMPLOYER = "lwf_employer", "Labour welfare fund, the employer's share"


PF_KINDS = (Statutory.PF, Statutory.PF_EMPLOYER, Statutory.EPS)


# What piece work can be counted in, registered by the modules that record
# output (manufacturing registers metres and kilograms woven), so payroll
# imports none of them. code -> (label, counter); counter(employee, up_to)
# returns [(date, quantity)] for everything the person made up to and
# including that date that still stands.
PIECE_MEASURES = {}


def register_piece_measure(code, label, counter):
    PIECE_MEASURES[code] = (label, counter)


class PayComponent(AuditModel):
    """
    One line that can appear on a payslip.

    Earnings, deductions and employer costs are one model because they
    differ only in which way the entry faces. Three models would mean
    three calculations, three posting paths, and the third would drift.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=ComponentKind.choices)
    basis = models.CharField(
        max_length=16, choices=ComponentBasis.choices, default=ComponentBasis.FIXED
    )
    expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Where an earning or an employer cost is charged. A department's "
                  "cost centre overrides it for that department's staff.",
    )
    recoverable_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="What a former employee owes of this component that their final pay may not take "
                  "back: a receivable (O28, the owner's decision of 9 October).",
    )
    liability_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="What a deduction or employer contribution is owed into — tax "
                  "payable, pension payable.",
    )
    measure = models.CharField(
        max_length=32, blank=True,
        help_text="For piece work, what is counted: a measure the production "
                  "side records against a person, such as metres woven.",
    )
    is_taxable = models.BooleanField(
        default=True,
        help_text="Counts towards the gross that percentage components are worked "
                  "out from. An expense reimbursement is pay that is not.",
    )
    reduces_for_unpaid_leave = models.BooleanField(
        default=False,
        help_text="Prorate this down for unpaid days in the period. True for salary; "
                  "false for a fixed allowance that is paid regardless.",
    )
    sequence = models.PositiveIntegerField(
        default=100,
        help_text="Order on the payslip, and the order components are worked out in "
                  "— a percentage can only be taken of what has been computed "
                  "before it.",
    )
    is_active = models.BooleanField(default=True)
    is_ordinary_wages = models.BooleanField(
        default=False,
        help_text="Part of the ordinary rate of wages overtime is paid at: basic and dearness "
                  "allowance, not bonus, overtime or reimbursement. Fixed, hourly and piece earnings.",
    )
    ceiling_figure = models.CharField(
        max_length=32, blank=True, choices=[("", "Its own ceiling, if any")] + [(k.value, k.label) for k in CEILINGS],
        help_text="Cap the base at this dated figure, as it stood on the last day of the run's period, "
                  "instead of a ceiling of its own.",
    )

    # Statutory contributions are not a percentage of everything paid.
    # Provident fund is 12% of basic and the allowances paid to all, on
    # wages capped at 15,000; ESI applies only to those earning up to
    # 21,000, decided once for each six-month contribution period, and
    # rounds up to the rupee; professional tax is a slab, with a
    # different figure in February. Taken of the whole taxable gross, PF
    # on a loom operator with overtime came out 876 a month too high.
    # The rules are set here per component; the rates stay data.
    base_components = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="+",
        help_text="What a percentage or slab is taken of: these components' amounts on "
                  "the slip. Empty means the taxable gross so far.",
    )
    base_ceiling = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="The base is capped here: 15,000 for provident fund.",
    )
    coverage_components = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="+",
        help_text="What decides whether it applies at all. Empty means the base. ESI "
                  "leaves overtime out of deciding, and charges on it all the same.",
    )
    coverage_ceiling = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Applies only while what decides coverage is at or under this: "
                  "21,000 for ESI.",
    )
    coverage_period_months = models.PositiveSmallIntegerField(
        default=1,
        help_text="Coverage is decided by the first slip in each block of this many "
                  "months from April, and holds for the block: 6 for ESI.",
    )
    rounding = models.CharField(max_length=8, choices=Rounding.choices,
                                default=Rounding.PAISA)
    statutory = models.CharField(
        max_length=16, choices=Statutory.choices, blank=True, default="",
        help_text="Which statutory line this is, so the monthly PF and ESI files know "
                  "which of the slip's lines to read.",
    )
    remit_by_day = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Day of the following month what is owed must be paid over: 15 for "
                  "PF and ESI, 7 for TDS.",
    )

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def round(self, amount):
        if self.rounding == Rounding.RUPEE:
            return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if self.rounding == Rounding.RUPEE_UP:
            return amount.quantize(Decimal("1"), rounding=ROUND_CEILING)
        return round_money(amount)

    def clean(self):
        if self.coverage_period_months not in (1, 2, 3, 4, 6, 12):
            raise ValidationError(
                f"{self.code}: a coverage period divides the year: 1, 2, 3, 4, 6 or 12 months."
            )
        if self.remit_by_day is not None and not 1 <= self.remit_by_day <= 28:
            raise ValidationError(f"{self.code}: pay it over by a day from 1 to 28.")
        if self.ceiling_figure and self.base_ceiling is not None:
            raise ValidationError(f"{self.code}: its base is capped at a ceiling of its own or at the dated "
                                  f"{self.get_ceiling_figure_display()}, not both.")
        if self.ceiling_figure and self.basis not in (ComponentBasis.PERCENT_OF_GROSS, ComponentBasis.SLAB):
            raise ValidationError(f"{self.code}: only what is taken of a base has a ceiling.")
        if self.is_ordinary_wages and (self.kind != ComponentKind.EARNING or self.basis not in (
                ComponentBasis.FIXED, ComponentBasis.PER_HOUR, ComponentBasis.PER_UNIT)):
            raise ValidationError(f"{self.code}: the ordinary rate of wages is made of fixed, hourly and piece "
                                  "earnings.")
        if self.basis in (ComponentBasis.PER_OVERTIME_HOUR, ComponentBasis.OVERTIME_ORDINARY) \
                and self.kind != ComponentKind.EARNING:
            raise ValidationError(f"{self.code}: overtime is something earned.")
        if self.basis == ComponentBasis.PER_UNIT:
            if self.kind != ComponentKind.EARNING:
                raise ValidationError(f"{self.code}: piece work is something earned.")
            if self.measure not in PIECE_MEASURES:
                raise ValidationError(
                    f"{self.code} is paid per unit and counts "
                    f"{self.measure or 'nothing'}; what can be counted is "
                    f"{', '.join(sorted(PIECE_MEASURES)) or 'nothing yet'}."
                )
        elif self.measure:
            raise ValidationError(
                f"{self.code} is not paid per unit, so it counts nothing; "
                f"{self.measure} would be read by nobody."
            )
        if self.kind == ComponentKind.EARNING and self.expense_account_id is None:
            raise ValidationError(f"{self.code} is an earning and needs an expense account.")
        if self.kind == ComponentKind.DEDUCTION and self.liability_account_id is None:
            raise ValidationError(
                f"{self.code} is a deduction; say what account the money is owed into."
            )
        if self.kind == ComponentKind.EMPLOYER_COST and (
            self.expense_account_id is None or self.liability_account_id is None
        ):
            raise ValidationError(
                f"{self.code} is an employer cost, so it is both an expense and "
                "something owed; it needs both accounts."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def slab_on(self, base, day, state="", who=None):
        """
        (amount, kept term) for `base` on `day`, from the schedule that stood
        then: the slabs of `state` (or of every state), of the latest date
        on or before `day`; one for that month wins over every month's.
        Slabs for one state only serve a person whose state is not set;
        for several, the person's state decides, and it must be said.
        """
        slabs = list(self.slabs.all())
        states = sorted({slab.state for slab in slabs if slab.state})
        mine = [slab for slab in slabs if state and slab.state == state] or [
            slab for slab in slabs if not slab.state]
        if not mine and states:
            if state or len(states) > 1:
                raise ValidationError(
                    f"{self.code} has slabs for {', '.join(states)} and {who or 'this person'} works in "
                    f"{state or 'no state said'}. Set the state they work in, or slabs for it.")
            mine = slabs
        day = to_date(day)
        stood = sorted({slab.effective_from for slab in mine
                        if slab.effective_from is None or slab.effective_from <= day},
                       key=lambda when: (when is not None, when))
        if not stood:
            return Decimal("0"), None
        schedule = [slab for slab in mine if slab.effective_from == stood[-1]]
        rows = [slab for slab in schedule if slab.above < base and (slab.up_to is None or base <= slab.up_to)]
        dated = [slab for slab in rows if slab.month == day.month]
        general = [slab for slab in rows if slab.month is None]
        chosen = (dated or general or [None])[0]
        if chosen is None:
            return Decimal("0"), None
        return chosen.amount, [chosen.pk, stood[-1].isoformat() if stood[-1] else None, chosen.state]

    def slab_for(self, base, month):
        """The slab amount for `base` in `month` of every state's undated schedule (the slabs as they were)."""
        return self.slab_on(base, datetime.date(2000, month, 1))[0]


# The legal limit on what one wage payment may lose to deductions, believed to be
# half the wages; the plant's accountant to confirm (the owner's decision on O28).
DEDUCTION_CAP = Decimal("0.5")


class PayComponentSlab(AuditModel):
    """
    One band of a slab component: over `above` and up to `up_to`, this
    amount; in `month` only, when it is set. Professional tax is the
    usual case — Maharashtra's 200 a month is 300 in February.
    """

    component = models.ForeignKey(PayComponent, on_delete=models.CASCADE, related_name="slabs")
    above = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    up_to = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    month = models.PositiveSmallIntegerField(null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    state = models.CharField(
        max_length=32, blank=True,
        help_text="The state whose schedule this band is of; blank is every state's.")
    effective_from = models.DateField(
        null=True, blank=True,
        help_text="The day the schedule this band is of applies from; blank is from the start. A pay run "
                  "reads the schedule that stood on the last day of its period.")

    class Meta:
        ordering = ["component", "state", "effective_from", "month", "above"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0), name="slab_amount_not_negative"),
            models.CheckConstraint(check=Q(above__gte=0), name="slab_above_not_negative"),
            models.CheckConstraint(check=Q(up_to__isnull=True) | Q(up_to__gt=F("above")),
                                   name="slab_up_to_above_its_floor"),
            models.CheckConstraint(check=Q(month__isnull=True) | Q(month__gte=1, month__lte=12),
                                   name="slab_month_is_a_month"),
        ]

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.effective_from = to_date(self.effective_from)
        if self.pk is not None:
            lock_rows(self, refresh=False)
            stored = PayComponentSlab.objects.get(pk=self.pk)
            if [getattr(stored, f) for f in ("component_id", "above", "up_to", "month", "amount", "state",
                                             "effective_from")] != [
                    getattr(self, f) for f in ("component_id", "above", "up_to", "month", "amount", "state",
                                               "effective_from")]:
                refuse_if_kept("slab", stored.pk, str(stored), "changed")
        # Two bands over one wage: slab_for took the first, and tax was
        # under-deducted with nothing said. Bands of one component for one
        # month (or for every month) may touch, not overlap.
        others = PayComponentSlab.objects.filter(
            component_id=self.component_id, month=self.month, state=self.state,
            effective_from=self.effective_from).exclude(pk=self.pk)
        for other in others:
            starts_below_its_top = other.up_to is None or self.above < other.up_to
            its_start_below_my_top = self.up_to is None or other.above < self.up_to
            if starts_below_its_top and its_start_below_my_top:
                raise ValidationError(
                    f"Over {self.above} up to {self.up_to or 'anything'} overlaps the band over "
                    f"{other.above} up to {other.up_to or 'anything'}; a wage must fall in one band."
                )
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        lock_rows(self, refresh=False)
        refuse_if_kept("slab", self.pk, str(self), "deleted")
        return super().delete(*args, **kwargs)

    def __str__(self):
        top = self.up_to if self.up_to is not None else "and over"
        dated = f" from {self.effective_from}" if self.effective_from else ""
        return f"{self.component.code}{' ' + self.state if self.state else ''}{dated} {self.above}-{top}: {self.amount}"


class EmployeeCompensation(AuditModel):
    """
    What one person is paid under one component, and from when.

    Dated rather than overwritten, so last month's payslip can still be
    explained after this month's rise. A rate that is simply edited makes
    every historical payslip unreproducible.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="compensation")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="The period amount for a fixed component, the percentage for a "
                  "percentage one, the hourly rate for an hourly one.",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee", "component", "-effective_from"]
        constraints = [
            models.CheckConstraint(
                check=Q(effective_to__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="compensation_dates_in_order",
            ),
            # A deduction is held positive and subtracted; a negative one
            # would be an earning nobody agreed to, and a negative salary
            # a debt. Neither has a meaning, so neither is storable.
            models.CheckConstraint(
                check=Q(amount__gt=0), name="compensation_amount_positive"
            ),
        ]

    def __str__(self):
        return f"{self.employee} {self.component.code} {self.amount} from {self.effective_from}"

    def covers(self, on_date):
        on_date = to_date(on_date)
        if on_date < self.effective_from:
            return False
        return self.effective_to is None or on_date <= self.effective_to

    def clean(self):
        self.effective_from = to_date(self.effective_from)
        self.effective_to = to_date(self.effective_to)
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("effective_to cannot precede effective_from.")
        if self.component.basis == ComponentBasis.OVERTIME_ORDINARY and self.amount != 1:
            raise ValidationError(
                f"{self.component.code} is paid at a multiple of {self.employee}'s ordinary rate of wages, worked "
                "out from their pay; it is not a rate typed for them. Give it as 1: they are paid overtime.")
        overlapping = EmployeeCompensation.objects.filter(
            employee=self.employee, component=self.component,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)
        )
        if self.effective_to is not None:
            overlapping = overlapping.filter(effective_from__lte=self.effective_to)
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)
        clash = overlapping.first()
        if clash is not None:
            # Two rates in force at once means whichever the query happens
            # to return first decides somebody's pay.
            raise ValidationError(
                f"{self.employee} already has {self.component.code} at {clash.amount} "
                f"from {clash.effective_from}; close that off before opening another."
            )

    def paid_to(self):
        """
        The last day a posted pay run paid on this rate, or None.

        Asked of the row as stored: what somebody has since typed into
        this object is not what was paid on.
        """
        stored = EmployeeCompensation.objects.filter(pk=self.pk).first() if self.pk else None
        if stored is None:
            return None
        runs = PayRun.objects.filter(
            status=PayRunStatus.POSTED, period_end__gte=stored.effective_from,
            payslips__employee_id=stored.employee_id,
            payslips__lines__component_id=stored.component_id,
        )
        if stored.effective_to is not None:
            runs = runs.filter(period_start__lte=stored.effective_to)
        last = runs.order_by("-period_end").values_list("period_end", flat=True).first()
        if last is not None and stored.effective_to is not None:
            # A rate that ended inside a run's period was paid on to its own
            # last day, and the next rate from the day after.
            last = min(last, stored.effective_to)
        return last, stored

    def locked_before_it(self):
        """The person (or the two, moved), as save() holds them first (hold_the_employee, O20)."""
        return documents_it_answers_to(self, "employee")

    @transaction.atomic
    def save(self, *args, **kwargs):
        # The person first: what posted runs paid on this rate is read next, and a run posting
        # at that moment paid 13,500 on a rate that then read 14,000 (O20).
        hold_the_employee(*self.locked_before_it())
        # Dated, not overwritten - the docstring's promise, kept here: a
        # rate a posted run paid on keeps its figure and its start, and
        # is only closed off, not before the last day it was paid on.
        paid = self.paid_to()
        if paid is not None and paid[0] is not None:
            last, stored = paid
            if (self.amount != stored.amount or self.component_id != stored.component_id
                    or self.employee_id != stored.employee_id
                    or to_date(self.effective_from) != stored.effective_from):
                raise ValidationError(
                    f"{stored} has been paid on, to {last}. Close it off and give the "
                    "new rate its own row from when it starts.")
            if self.effective_to is not None and to_date(self.effective_to) < last:
                raise ValidationError(
                    f"{stored} was paid on it to {last}; it cannot end before that.")
        self.clean()
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        hold_the_employee(*self.locked_before_it())
        paid = self.paid_to()
        if paid is not None and paid[0] is not None:
            raise ValidationError(f"{paid[1]} has been paid on, to {paid[0]}; it stays.")
        return super().delete(*args, **kwargs)


class PayRunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CALCULATED = "calculated", "Calculated"
    POSTED = "posted", "Posted"
    VOIDED = "voided", "Voided"


class PayRun(AuditModel):
    """
    One payroll period for a set of people.

    Calculating and posting are separate because a payroll is checked
    before it is committed. A calculated run can be recalculated as many
    times as somebody likes; a posted one is immutable like every other
    posted document here, and is corrected by voiding it.
    """

    number = models.CharField(max_length=32, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    pay_date = models.DateField()
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16, choices=PayRunStatus.choices, default=PayRunStatus.DRAFT
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-period_start", "-id"]
        permissions = [("post_payrun", "Can post a pay run to the ledger")]
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="pay_run_number_unique"
            ),
            models.CheckConstraint(
                check=Q(period_end__gte=models.F("period_start")),
                name="pay_period_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return self.number or self.name or f"Draft pay run {self.pk}"

    @property
    def posted(self):
        return self.status == PayRunStatus.POSTED

    def is_voided(self):
        return self.voided_at is not None

    def gross(self):
        return sum((slip.gross() for slip in self.payslips.all()), Decimal("0"))

    def net(self):
        return sum((slip.net() for slip in self.payslips.all()), Decimal("0"))

    def employer_cost(self):
        return sum((slip.employer_cost() for slip in self.payslips.all()), Decimal("0"))

    def total_cost(self):
        """What the company spends, which is not what anybody receives."""
        return self.gross() + self.employer_cost()

    # -- calculation -----------------------------------------------------

    @serialised("status")
    def calculate(self, employees=None, hours=None):
        """
        Work out every payslip in this run, replacing whatever was there.

        Recalculating is deliberately destructive: a half-updated payroll
        where some slips reflect the new salary and some the old is worse
        than one that is simply wrong, because nothing about it says
        which is which.
        """
        if self.status in (PayRunStatus.POSTED, PayRunStatus.VOIDED):
            raise ValidationError("A posted pay run cannot be recalculated. Void it first.")
        self.period_start = to_date(self.period_start)
        self.period_end = to_date(self.period_end)
        self.pay_date = to_date(self.pay_date)

        hours = hours or {}
        people = employees if employees is not None else self._eligible_employees()
        # Employed on any day of the period. Asked of its first and last days
        # only, somebody hired on 8 June and gone on the 19th was on no run
        # at all, and ten days' wages were never paid.
        people = [
            person for person in people
            if to_date(person.hire_date) <= self.period_end
            and (person.termination_date is None or to_date(person.termination_date) >= self.period_start)
        ]
        people += self._leavers_owing(people, among=employees)
        if not people:
            raise ValidationError("Nobody is employed during this period.")

        Payslip.objects.filter(run=self).delete()
        for person in people:
            slip = Payslip.objects.create(run=self, employee=person)
            slip.calculate(hours=hours.get(person))
        self.status = PayRunStatus.CALCULATED
        super().save(update_fields=[
            "period_start", "period_end", "pay_date", "status", "updated_at",
        ])
        return list(self.payslips.all())

    def _leavers_owing(self, people, among=None):
        """
        People who left before this period and still owe piece work back (O31):
        a run posted before their leaving date was set had no final slip to
        take it from, and no run after included them. On this run their slip
        is final; it has no wages, so what is owed is a receivable (O28).
        """
        on = {person.pk for person in people}
        found = []
        candidates = Employee.objects.all() if among is None else Employee.objects.filter(
            pk__in=[person.pk for person in among])
        for person in candidates.filter(
                termination_date__lt=self.period_start,
                compensation__component__basis=ComponentBasis.PER_UNIT).distinct():
            if person.pk in on:
                continue
            probe = Payslip(run=self, employee=person)
            components = {row.component for row in EmployeeCompensation.objects.filter(
                employee=person, component__basis=ComponentBasis.PER_UNIT).select_related("component")}
            if any(probe.piece_work(component)[0] < 0 for component in components):
                found.append(person)
        return found

    def _eligible_employees(self):
        """Everyone with pay in force at any point in the period."""
        return Employee.objects.filter(
            compensation__effective_from__lte=self.period_end,
        ).filter(
            Q(compensation__effective_to__isnull=True)
            | Q(compensation__effective_to__gte=self.period_start)
        ).distinct()

    # -- posting ---------------------------------------------------------

    @serialised("status")
    def post(self, memo=""):
        if self.status == PayRunStatus.POSTED:
            raise ValidationError("This pay run is already posted.")
        if self.status == PayRunStatus.VOIDED:
            raise ValidationError("A voided pay run cannot be posted again.")
        slips = list(self.payslips.prefetch_related("lines"))
        if not slips:
            raise ValidationError("Cannot post a pay run with no payslips.")
        # A former employee's slip owing something back has no lines and is
        # still the record of it (O28, O31).
        if not any(slip.lines.exists() or slip.recoverable for slip in slips):
            # Otherwise this posts an entry with no lines, which balances
            # trivially and records nothing, against a run that looks as
            # though payroll was done.
            raise ValidationError(
                "Nobody on this run is owed anything. Check the compensation in force "
                "before posting a payroll that pays nothing."
            )
        # What is posted is what these people are owed as it posts. Their
        # leave, register, hours, rates and output can have moved since the
        # run was calculated, and another run of theirs been posted; each
        # slip is worked out again with them held, so none of it moves
        # until the entry is made.
        lock_rows(*(slip.employee for slip in slips))
        for slip in slips:
            slip.check_current()
        # Somebody owed nothing was not paid, and a posted run keeps only the
        # slips it paid. An empty one held the period: somebody on June's run
        # with no pay set up was refused June's pay as paid already, and the
        # register and leave read their June as paid on.
        for slip in slips:
            if not slip.lines.exists() and not slip.recoverable:
                slip.delete()
        slips = [slip for slip in slips if slip.pk is not None]
        self._check_not_already_paid()

        if not self.number:
            self.number = DocumentSequence.next_for(
                "hr.payrun", self.pay_date, name="Pay Runs", prefix="PAY-"
            )
        label = memo or f"Payroll {self.number} for {self.period_start}..{self.period_end}"

        debits, credits = {}, {}

        def add(bucket, key, amount):
            # Debits are keyed (account, centre): wages read by the
            # department's cost centre in the analytic view. Credits are
            # what is owed, by account alone.
            account = key[0] if isinstance(key, tuple) else key
            if account is None or not amount:
                return
            bucket[key] = bucket.get(key, Decimal("0")) + amount

        net_total, owed_by, recovered = Decimal("0"), [], []
        for slip in slips:
            department = slip.employee.department
            centre = department.centre if department is not None else None
            for line in slip.lines.all():
                account = line.resolve_account()
                # Where it landed is a fact from here on, not something a
                # later reader recomputes: a department can be moved to a
                # different cost centre and this entry must not follow it.
                # And what it is owed into: the liabilities report reads this,
                # not the component, whose account can be changed later.
                # An advance recovered is a receivable cleared, not a sum owed over to anyone.
                owed_into = None if ComponentKind.EARNING in (line.kind, line.component.kind) or line.advance_id else (
                    account if line.kind == ComponentKind.DEDUCTION
                    else line.component.liability_account
                )
                if (line.posted_account_id != getattr(account, "pk", None)
                        or line.posted_liability_account_id != getattr(owed_into, "pk", None)):
                    line.posted_account = account
                    line.posted_liability_account = owed_into
                    super(PayslipLine, line).save(
                        update_fields=["posted_account", "posted_liability_account",
                                       "updated_at"]
                    )
                if line.kind == ComponentKind.EARNING:
                    add(debits, (account, centre), line.amount)
                elif line.kind == ComponentKind.DEDUCTION and line.advance_id:
                    # Against the person, as the payment that gave it was.
                    recovered.append((account, slip.employee.party, line.amount))
                elif line.kind == ComponentKind.DEDUCTION:
                    add(credits, account, line.amount)
                else:
                    add(debits, (account, centre), line.amount)
                    add(credits, line.component.liability_account, line.amount)
            net_total += slip.net()
            if slip.recoverable:
                # What the final pay could not take back: owed by the former
                # employee, out of the wages it was charged to (O28).
                component = slip.recoverable_component
                if component.recoverable_account_id is None:
                    raise ValidationError(
                        f"{slip.employee} leaves owing {slip.recoverable} of {component.code} that their final "
                        f"pay may not take back, and {component.code} has no account for what a former "
                        "employee owes. Set its recoverable account, then post.")
                probe = PayslipLine(payslip=slip, component=component, kind=ComponentKind.DEDUCTION)
                add(credits, probe.resolve_account(), slip.recoverable)
                owed_by.append((component.recoverable_account, slip.employee.party, slip.recoverable))

        add(credits, _net_pay_account(), net_total)

        entry = JournalEntry.objects.create(
            date=self.pay_date, reference=self.number, memo=label[:255]
        )
        for (account, centre), amount in debits.items():
            JournalLine.objects.create(
                entry=entry, account=account, debit=round_money(amount),
                description=label[:255], cost_centre=centre,
            )
        for account, amount in credits.items():
            JournalLine.objects.create(
                entry=entry, account=account, credit=round_money(amount),
                description=label[:255],
            )
        for account, party, amount in owed_by:
            JournalLine.objects.create(entry=entry, account=account, party=party, debit=amount,
                                       description=f"Owed back by a former employee: {label}"[:255])
        for account, party, amount in recovered:
            JournalLine.objects.create(entry=entry, account=account, party=party, credit=amount,
                                       description=f"Advance recovered: {label}"[:255])
        entry.post()

        self.journal_entry = entry
        self.status = PayRunStatus.POSTED
        self.posted_at = timezone.now()
        super().save(update_fields=[
            "number", "journal_entry", "status", "posted_at", "updated_at",
        ])
        return entry

    def _check_not_already_paid(self):
        """
        Nobody is paid twice for the same days.

        A second run over an overlapping period is the double-processing
        shape: it posts a second set of wages, a second set of
        liabilities, and nothing about either entry says the two are the
        same fortnight.
        """
        people = [slip.employee_id for slip in self.payslips.all()]
        clashing = PayRun.objects.filter(
            status=PayRunStatus.POSTED,
            voided_at__isnull=True,
            period_start__lte=self.period_end,
            period_end__gte=self.period_start,
            payslips__employee_id__in=people,
        ).exclude(pk=self.pk).distinct().first()
        if clashing is not None:
            raise ValidationError(
                f"{clashing} already paid some of these people for "
                f"{clashing.period_start}..{clashing.period_end}, which overlaps this "
                "period. Void it, or narrow this run."
            )

    @serialised("status", "voided_entry")
    def void(self, on_date=None, memo=""):
        """
        Reverse a posted run.

        Written with post(), because a payroll that can only go forwards
        means the first mistake is corrected by a hand-written journal
        that nothing ties back to the run it is fixing.
        """
        if self.status != PayRunStatus.POSTED:
            raise ValidationError("Only a posted pay run can be voided.")
        if self.is_voided():
            raise ValidationError("This pay run has already been voided.")
        on_date = correction_date(on_date, self.pay_date, f"{self} is not voided on", "it was paid")
        paid = next((slip for slip in self.payslips.all() if slip.is_paid()), None)
        if paid is not None:
            raise ValidationError(
                f"{paid.employee} has already been paid from this run. Reverse the "
                "payment before voiding the payroll that owed it."
            )
        # The month's dues paid over stand on what its runs deducted, as a slip's pay stands on
        # the slip. Voided under them, June's run left 7,200 paid to the EPFO against nothing
        # owed, and June gone from the report. Each account held as a remittance holds it.
        shares = {}
        for line in PayslipLine.objects.filter(payslip__run=self, posted_liability_account__isnull=False):
            shares[line.posted_liability_account] = shares.get(line.posted_liability_account, Decimal("0")) + line.amount
        lock_rows(*shares)
        month = _month_of(self.period_end)
        for account, share in sorted(shares.items(), key=lambda pair: pair[0].code):
            paid_over, left = remitted(account, month), _deducted(account, month) - share
            if paid_over > left:
                raise ValidationError(
                    f"{paid_over} of {account} for {month:%B %Y} has been paid over; voided, this run "
                    f"would leave {left} deducted for it. A payment that did not go through is voided first; while "
                    "it stands, so does this run.")
        self.voided_entry = self.journal_entry.create_reversal(
            entry_date=on_date,
            memo=memo or f"Void of payroll {self.number}",
        )
        self.status = PayRunStatus.VOIDED
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_entry", "status", "voided_at", "updated_at"])
        return self.voided_entry

    def unpaid_net(self):
        """
        What this run still owes its people.

        Asked of each slip rather than filtered in the database, because
        a slip whose payment was voided is owed again and a `payment
        IS NULL` filter cannot see that.
        """
        return sum(
            (slip.net() for slip in self.payslips.all() if not slip.is_paid()),
            Decimal("0"),
        )

    def delete(self, *args, **kwargs):
        # Posted, it is in the ledger and is reversed by voiding it; voided, it
        # is the record of what was paid and taken back. Deleted, its slips
        # went and its entries stayed, owed to nobody.
        stored = PayRun.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if stored in (PayRunStatus.POSTED, PayRunStatus.VOIDED):
            raise ValidationError(f"{self} has been posted: it is voided, not deleted.")
        return super().delete(*args, **kwargs)

    @transaction.atomic
    def save(self, *args, **kwargs):
        previous = PayRun.objects.filter(pk=self.pk).first() if self.pk else None
        # Edited only while it is being worked out. Voided, it is the record of what was paid
        # and taken back, as fixed as when it stood: a voided June run moved to May turned the
        # statutory report as of 10 July from June owing 1,000.00 into May owing it. post() and
        # void() write their own fields past this, through AuditModel.save().
        if previous is not None and previous.status not in (PayRunStatus.DRAFT, PayRunStatus.CALCULATED):
            raise ValidationError(
                f"Cannot modify a {previous.get_status_display().lower()} pay run. "
                + ("Void it and raise another." if previous.status == PayRunStatus.POSTED
                   else "It is the record of what was paid and taken back; raise another.")
            )
        moved = (previous is not None and previous.status == PayRunStatus.CALCULATED
                 and (to_date(self.period_start), to_date(self.period_end))
                 != (previous.period_start, previous.period_end))
        if moved:
            # Its slips were worked out for the days it covered; for any
            # others it is calculated again. Stretched to June and July after
            # June was calculated, it posted June's pay for both months, and
            # then refused July's run as paid already.
            self.status = PayRunStatus.DRAFT
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "status"}
        super().save(*args, **kwargs)
        if moved:
            self.payslips.all().delete()


def _net_pay_account():
    account = Company.get().net_pay_account
    if account is None:
        raise ValidationError(
            "The company has no net pay payable account configured; there is nowhere "
            "to record what payroll owes its staff."
        )
    return account


class Payslip(AuditModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    run = models.ForeignKey(PayRun, on_delete=models.CASCADE, related_name="payslips")
    payment = models.ForeignKey(
        "accounting.Payment", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payslips", editable=False,
        help_text="What settled the net pay. Until it is set, net pay payable holds "
                  "this slip's balance.",
    )
    hours = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Hours handed in for hourly pay when the run was calculated; empty when "
                  "they were read from approved timesheets. Posting works the slip out "
                  "again from these.",
    )
    note = models.CharField(max_length=255, blank=True)
    recoverable = models.DecimalField(
        max_digits=18, decimal_places=2, default=0, editable=False,
        help_text="Taken back beyond what a final pay may lose: owed by the former employee (O28).",
    )
    recoverable_component = models.ForeignKey(
        PayComponent, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
    )
    kept_paid_days = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Working days employed less unpaid leave and absences, as the slip was worked "
                  "out: what the ESI return files (O5).",
    )
    kept_ncp_days = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Unpaid leave and absences as the slip was worked out: the ECR's NCP days (O5).",
    )

    class Meta:
        ordering = ["employee"]
        permissions = [("view_own_payslip", "Can read one's own payslips (my-payslips)")]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "employee"], name="one_payslip_per_employee_per_run"
            ),
        ]

    def __str__(self):
        return f"{self.employee} {self.run}"

    # Totals are computed from the lines rather than stored, the same way
    # an invoice's are. The lines themselves are frozen when the run is
    # calculated, so a rise next month does not restate this month.
    def _total(self, kind, taxable_only=False):
        total = Decimal("0")
        for line in self.lines.all():
            if line.kind != kind:
                continue
            if taxable_only and not line.is_taxable:
                continue
            total += line.amount
        return round_money(total)

    def gross(self):
        return self._total(ComponentKind.EARNING)

    def taxable_gross(self):
        return self._total(ComponentKind.EARNING, taxable_only=True)

    def deductions(self):
        return self._total(ComponentKind.DEDUCTION)

    def employer_cost(self):
        return self._total(ComponentKind.EMPLOYER_COST)

    def net(self):
        return round_money(self.gross() - self.deductions())

    def is_paid(self):
        """
        Settled, and still settled.

        A voided payment credits net pay payable straight back, so the
        wages are owed again — but the slip went on pointing at it and
        reading as paid, so unpaid_net() said zero while the control
        account said four thousand. Whether somebody has been paid is a
        question about the payment, not about the pointer to it.
        """
        if self.payment_id is None or self.payment.is_voided():
            return False
        # Paid by a bank file whose transfer to this person came back (bank_file.py). Read off the
        # slip's transfers, which a list prefetches with the slips (SLIP_PAYMENT_READS): filtered in
        # the database it was a query a slip, 600 for a run of 600.
        return not any(row.is_returned() for row in self.transfers.all() if row.file.payment_id == self.payment_id)

    # -- working out what somebody earned --------------------------------

    def period_working_days(self):
        return Decimal(working_days(
            self.run.period_start, self.run.period_end,
            pattern=self.employee.working_days,
            region=self.employee.holiday_region,
        ))

    def days_employed(self, start=None, end=None):
        """
        Working days in this period the person was actually employed for,
        or in the part of it from `start` to `end`.

        A joiner halfway through the month is owed half a month, and
        paying them a full salary because the run happened to include
        them is the kind of error nobody reports.
        """
        start = max(start or self.run.period_start, self.employee.hire_date)
        end = end or self.run.period_end
        if self.employee.termination_date:
            end = min(end, self.employee.termination_date)
        if end < start:
            return Decimal("0")
        return Decimal(working_days(
            start, end,
            pattern=self.employee.working_days,
            region=self.employee.holiday_region,
        ))

    def unpaid_leave_days(self, start=None, end=None):
        """
        Approved days in this period, or in the part of it from `start` to
        `end`, under a policy that does not pay.

        This is the join between the two halves of the module: leave
        knows the days, payroll knows what a day costs, and neither is
        much use without the other.
        """
        start, end = start or self.run.period_start, end or self.run.period_end
        total = Decimal("0")
        for request in LeaveRequest.objects.filter(
            employee=self.employee,
            status=LeaveStatus.APPROVED,
            policy__is_paid=False,
            start_date__lte=end,
            end_date__gte=start,
        ).select_related("policy"):
            overlap_start = max(request.start_date, start)
            overlap_end = min(request.end_date, end)
            days = Decimal(working_days(
                overlap_start, overlap_end,
                pattern=self.employee.working_days,
                region=self.employee.holiday_region,
            ))
            # Half a day off is half a day unpaid, as it was half a day off the allowance.
            total += days / 2 if request.half_day else days
        return total

    def absent_days(self, start=None, end=None):
        """Days the attendance register shows the person away without leave, a half day as a half."""
        from .attendance import absent_days

        return absent_days(self.employee, start or self.run.period_start, end or self.run.period_end)

    def paid_days(self, start=None, end=None):
        """Working days employed in the period, or from `start` to `end`, less unpaid leave and absences."""
        earned = self.days_employed(start, end) - self.unpaid_leave_days(start, end) - self.absent_days(start, end)
        return max(earned, Decimal("0"))

    def paid_proportion(self):
        """
        The fraction of a full period's pay this person has earned.

        Days employed, less unpaid leave and absences, over the days the
        period holds. A day-rated worker is paid on the register alone,
        so an unmarked working day of theirs stops the run rather than
        passing as present.
        """
        from .attendance import unmarked_days

        full = self.period_working_days()
        if full <= 0:
            return Decimal("0")
        if self.employee.paid_by_attendance:
            missing = unmarked_days(self.employee, self.run.period_start, self.run.period_end)
            if missing:
                raise ValidationError(
                    f"{self.employee} is paid by attendance and {len(missing)} working day(s) in this "
                    f"period are not marked, the first {missing[0]}. Mark the register before the run."
                )
        earned = self.paid_days()
        if earned <= 0:
            return Decimal("0")
        return min(earned / full, Decimal("1"))

    @transaction.atomic
    def calculate(self, hours=None):
        """
        Build this slip's lines from the compensation in force.

        Hours handed in are kept with the slip, to the hundredth as a
        timesheet keeps them, and the slip is worked out from what is
        kept: posting works it out again from the same figure.
        """
        self.lines.all().delete()
        self.hours = None if hours is None else Decimal(hours).quantize(Decimal("0.01"))
        for line in self.work_out(self.hours):
            line.save()
        # Kept as worked out, with the lines: a working pattern or holiday
        # region edited after the run restated a posted month's days on its
        # ESI and ECR files (O5). The register's days move the lines too, so
        # check_current refuses a post they have moved under.
        away = self.unpaid_leave_days() + self.absent_days()
        self.kept_paid_days, self.kept_ncp_days = self.days_employed() - away, away
        super().save(update_fields=["hours", "kept_paid_days", "kept_ncp_days", "recoverable",
                                    "recoverable_component", "updated_at"])
        return list(self.lines.all())

    def kept_days(self):
        """(paid days, NCP days): as worked out, or for a slip from before they were kept, read now."""
        if self.kept_paid_days is not None and self.kept_ncp_days is not None:
            return self.kept_paid_days, self.kept_ncp_days
        away = self.unpaid_leave_days() + self.absent_days()
        return self.days_employed() - away, away

    def check_current(self):
        """
        Refuse when the slip is not what working it out now would give.

        Calculating and posting are apart so a payroll can be checked, and
        everything it read could move in between. Unpaid leave approved
        after June was calculated was posted unseen at 4,400.00 instead
        of 3,400.00; a rate edited from 5,000 to 5,100 was posted at 5,000
        and the row then held as paid on at 5,100. Two fortnights
        calculated before either posted each paid September's piece work
        from nothing, 1,250.00 for 850.00. Asked by post() with the
        person held, so nothing it reads moves until the entry is made.
        """
        kept = (self.recoverable, self.recoverable_component_id)
        now, then = self.work_out(self.hours), list(self.lines.select_related("component"))
        if Counter(map(_as_worked_out, now)) == Counter(map(_as_worked_out, then)) \
                and (self.recoverable, self.recoverable_component_id) == kept:
            return
        before, after = _by_component(then), _by_component(now)
        moved = [component for component in sorted(set(before) | set(after), key=lambda c: (c.sequence, c.code))
                 if before.get(component) != after.get(component)]
        what = (f"{moved[0].name} was worked out at {before.get(moved[0], Decimal('0.00'))} and comes to "
                f"{after.get(moved[0], Decimal('0.00'))} now" if moved else "its lines are not made up as they were")
        raise ValidationError(
            f"{self.employee}'s pay has changed since this run was calculated: {what}. "
            "Calculate the run again, and check it, before posting it."
        )

    def work_out(self, hours=None):
        """
        The lines this slip comes to from what is in force now, unsaved.

        Components are worked out in sequence order, so a percentage
        component can only ever be taken of what has already been
        computed. A percentage of a gross that later grows is a number
        that was right when it was calculated and wrong when it was
        posted.

        A rate changed inside the period is two rows of one component,
        and each is paid for the part of the period it was in force: a
        rise on the 16th of a 22-day June paid both 4,400 and 5,500 in
        full, 9,900.00 where 4,950.00 was owed. Each is a line of its own,
        at its own rate.
        """
        proportion = self.paid_proportion()
        # The dated figures each component was worked out at, kept on its lines (kept_terms), and the
        # overtime hours each rate row paid for.
        self._kept, self._hours = {}, {}
        rows = EmployeeCompensation.objects.filter(
            employee=self.employee,
            component__is_active=True,
            effective_from__lte=self.run.period_end,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=self.run.period_start)
        ).select_related("component").prefetch_related(
            # What each component is taken of, covered by and banded at,
            # read with the rows rather than asked once per component.
            "component__base_components", "component__coverage_components", "component__slabs",
        ).order_by("component__sequence", "component__code", "effective_from")

        running_taxable = Decimal("0")
        computed = {}
        lines = []
        for _component, group in groupby(rows, key=lambda row: row.component_id):
            group = list(group)
            component = group[0].component
            if component.basis == ComponentBasis.PER_UNIT:
                # Piece work is worked out once, at each day's own rate,
                # however many rows it has.
                amount, quantity, row, here, made_here = self.piece_work(component, split=True)
                owed = [(row, amount, quantity)]
                if self.is_final() and amount < here:
                    # A leaver's own last weeks are wages, and what is taken
                    # back for earlier runs is a deduction beside them: netted,
                    # the recovery came off their PF and ESI wages (O28).
                    owed = [(row, here, made_here), (row, amount - here, quantity - made_here)]
            else:
                # Each rate is taken of the gross before the component, not
                # of what its own earlier rate added to it.
                before = running_taxable
                # Rounded once, for the component, not once a rate: 1,000 up to the rupee,
                # closed on 9 June and opened again on the 10th, paid 319 + 682 = 1,001. Each
                # line is what the running total rounds to less what the lines before it took.
                owed, unrounded, taken = [], Decimal("0"), Decimal("0")
                for row in group:
                    unrounded += self._amount_for(row, proportion, before, hours, computed)
                    so_far = component.round(unrounded)
                    owed.append((row, so_far - taken, None))
                    taken = so_far
            marked, label = component.statutory or "", component.name
            if component.statutory in PF_KINDS:
                # The ECR files EPS wages at the pension ceiling of the month (O222), and none from the
                # month after the member turns 58: the date of birth read is kept with it.
                kept = self._terms(component.pk)
                kept["eps_ceiling"] = figure_as_of(
                    FigureKind.EPS_WAGE_CEILING, self.run.period_end, self.employee.work_state)[1]
                details = getattr(self.employee, "pay_details", None)
                born = details.date_of_birth if details is not None else None
                kept["born"] = born.isoformat() if born else None
                if component.statutory == Statutory.EPS:
                    if born is None:
                        raise ValidationError(
                            f"No date of birth on {self.employee}: {component.code} is the pension share, which "
                            "stops the month after 58, and this run cannot tell. Enter it in their pay details.")
                    if pension_stops(born, self.run.period_end):
                        # Past 58 the whole employer share is the provident fund's.
                        marked, label = Statutory.PF_EMPLOYER, f"{component.name}, to provident fund past 58"
            for row, amount, quantity in owed:
                amount, kind = component.round(amount), component.kind
                rate = row.amount
                if component.basis == ComponentBasis.OVERTIME_ORDINARY and amount > 0:
                    quantity = self._hours.get(row.pk)
                    rate = (amount / quantity).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                if amount < 0 and component.basis == ComponentBasis.PER_UNIT and self.is_final():
                    # More taken back than earned waits for the next run, and
                    # somebody leaving has none: a roll voided after it was paid
                    # comes off the rest of their final pay, taken back from the
                    # wages it was charged to.
                    amount, quantity, kind = -amount, -quantity, ComponentKind.DEDUCTION
                if amount <= 0:
                    continue
                if kind == component.kind:
                    computed[component.pk] = computed.get(component.pk, Decimal("0")) + amount
                lines.append(PayslipLine(
                    payslip=self,
                    component=component,
                    kind=kind,
                    description=label if kind == component.kind else f"{component.name}, taken back",
                    # Frozen: the rate, the basis and the account as they stood
                    # when this slip was worked out. All three can change, and
                    # a payslip that restates itself afterwards cannot be
                    # reconciled to the entry that paid it.
                    rate=rate,
                    basis=component.basis,
                    is_taxable=component.is_taxable,
                    amount=amount,
                    quantity=quantity,
                    base_ceiling=self._ceiling(component),
                    kept_statutory=marked,
                    kept_base=sorted(other.pk for other in component.base_components.all()),
                    kept_terms=self._kept.get(component.pk) or None,
                ))
                if kind == ComponentKind.EARNING and component.is_taxable:
                    running_taxable += amount
        self.recoverable, self.recoverable_component = Decimal("0"), None
        if self.is_final():
            lines = self._within_the_deduction_cap(lines)
        lines += self._advance_recoveries(lines)
        earned = sum((line.amount for line in lines if line.kind == ComponentKind.EARNING), Decimal("0.00"))
        deducted = sum((line.amount for line in lines if line.kind == ComponentKind.DEDUCTION), Decimal("0.00"))
        if deducted > earned:
            # Refused, not capped. Below nothing, the slip posted a debit to net
            # pay payable that no payment out can clear; capped, what was not
            # deducted would be dropped with nothing to carry it to a next run.
            # A pay below nothing is a debt, which a payslip has no meaning for.
            raise ValidationError(
                f"{self.employee} would be paid below nothing: {deducted} deducted against {earned} "
                f"earned for {self.run.period_start}..{self.run.period_end}. Change what is deducted "
                "from them for this period, or leave them off this run."
            )
        return lines

    def _span(self, row):
        """The first and last day of this period that `row`'s rate was in force on."""
        start = max(self.run.period_start, row.effective_from)
        end = self.run.period_end if row.effective_to is None else min(self.run.period_end, row.effective_to)
        return start, end

    def _part(self, amount, start, end):
        """
        `amount`, a whole period's worth, for the days from `start` to
        `end`: in proportion to the working days employed, the day basis
        joiners and leavers are paid on. Multiplied before it is divided.
        """
        employed = self.days_employed()
        return amount * self.days_employed(start, end) / employed if employed > 0 else Decimal("0")

    def piece_work(self, component, split=False):
        """
        (amount, quantity, rate row) owed for piece work on this run.

        Paid by difference: everything the person has made up to the end
        of the period, each day at the rate in force that day, less what
        posted runs have already paid them for it. So a roll voided after
        payday is taken back from the next run, and one entered late is
        paid in it, and nothing needs remembering which run paid which
        roll. More taken back than earned leaves nothing on this slip and
        the rest to the next; on the slip of somebody leaving, which has no
        next, the rest comes off their other pay (work_out()).

        Output on a day no rate covers was not piece work, and is not
        paid. Runs are worked in order: once a later period has paid
        piece work, an earlier one cannot be calculated behind it.
        """
        label, counter = PIECE_MEASURES.get(component.measure, (None, None))
        if counter is None:
            raise ValidationError(f"Nothing counts {component.measure} for {component.code}.")
        paid_lines = PayslipLine.objects.filter(
            component=component, payslip__employee=self.employee,
            payslip__run__status=PayRunStatus.POSTED,
        ).exclude(payslip__run=self.run)
        later = paid_lines.filter(payslip__run__period_end__gt=self.run.period_end).first()
        if later is not None:
            raise ValidationError(
                f"{later.payslip.run} has already paid {self.employee} {component.code} "
                f"for a later period. Piece work is paid in order; void that run first."
            )
        rates = list(EmployeeCompensation.objects.filter(
            employee=self.employee, component=component,
        ).order_by("effective_from"))
        earned, made, here, made_here = Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        for day, quantity in counter(self.employee, self.run.period_end):
            row = next((rate for rate in rates if rate.covers(day)), None)
            if row is not None:
                earned += quantity * row.amount
                made += quantity
                if day >= self.run.period_start:
                    here += quantity * row.amount
                    made_here += quantity
        # What a final run took back counts against what was paid.
        paid_amount, paid_quantity = Decimal("0"), Decimal("0")
        for kind, amount, quantity in paid_lines.values_list("kind", "amount", "quantity"):
            sign = -1 if kind == ComponentKind.DEDUCTION else 1
            paid_amount += sign * amount
            paid_quantity += sign * (quantity or Decimal("0"))
        # And what a final run left owed as a receivable was taken back too.
        paid_amount -= Payslip.objects.filter(
            employee=self.employee, run__status=PayRunStatus.POSTED, recoverable_component=component,
        ).exclude(run=self.run).aggregate(total=Sum("recoverable"))["total"] or Decimal("0")
        in_force = next((rate for rate in reversed(rates)
                         if rate.effective_from <= self.run.period_end), rates[-1])
        if split:
            return (round_money(earned) - paid_amount, made - paid_quantity, in_force,
                    round_money(here), made_here)
        return round_money(earned) - paid_amount, made - paid_quantity, in_force

    def _within_the_deduction_cap(self, lines):
        """
        A leaver's final pay takes back no more than the legal limit on what
        one wage payment may lose: deductions together at most
        DEDUCTION_CAP of the wages. What is taken back beyond it is owed by
        the former employee (`recoverable`), posted as a receivable (O28).
        """
        earned = sum((line.amount for line in lines if line.kind == ComponentKind.EARNING), Decimal("0"))
        taken = [line for line in lines
                 if line.kind == ComponentKind.DEDUCTION and line.component.kind == ComponentKind.EARNING]
        others = sum((line.amount for line in lines
                      if line.kind == ComponentKind.DEDUCTION and line not in taken), Decimal("0"))
        # Down, never up: a legal ceiling is not passed by a paisa (166.665 is 166.66).
        allowed = max((earned * DEDUCTION_CAP).quantize(Decimal("0.01"), rounding=ROUND_DOWN) - others,
                      Decimal("0"))
        kept = []
        for line in lines:
            if line in taken:
                take = min(line.amount, allowed)
                allowed -= take
                if take < line.amount:
                    self.recoverable += line.amount - take
                    self.recoverable_component = line.component
                line.amount = take
                if not take:
                    continue
            kept.append(line)
        return kept

    def _advance_recoveries(self, lines):
        """
        A deduction line for each advance or loan this slip recovers from
        (advances.py): the instalment, or what is left if less, and on a
        final slip all that is left; never past the deduction cap, the
        rest waiting for the next run, or on a final slip staying owed on
        the advance, a receivable already (O28).
        """
        from .advances import advances_to_recover

        advances = advances_to_recover(self)
        if not advances:
            return []
        earned = sum((line.amount for line in lines if line.kind == ComponentKind.EARNING), Decimal("0"))
        deducted = sum((line.amount for line in lines if line.kind == ComponentKind.DEDUCTION), Decimal("0"))
        room = max((earned * DEDUCTION_CAP).quantize(Decimal("0.01"), rounding=ROUND_DOWN) - deducted, Decimal("0"))
        found = []
        for advance in advances:
            left = advance.balance()
            take = min(left if self.is_final() else min(advance.instalment, left), room)
            if take <= 0:
                continue
            room -= take
            found.append(PayslipLine(
                payslip=self, component=advance.component, kind=ComponentKind.DEDUCTION,
                description=f"{advance.component.name}: {advance.get_kind_display().lower()} of {advance.amount}",
                rate=advance.instalment, basis=advance.component.basis, is_taxable=advance.component.is_taxable,
                amount=take, kept_statutory="", kept_base=[], advance=advance,
            ))
        return found

    def _terms(self, component_id):
        """What the dated figures `component_id` is worked out at on this slip: kept on its lines."""
        return self.__dict__.setdefault("_kept", {}).setdefault(component_id, {})

    def _ceiling(self, component):
        """The cap on `component`'s base this period: its own, or the dated figure on the period's last day (O222)."""
        if not component.ceiling_figure:
            return component.base_ceiling
        value, term = figure_as_of(component.ceiling_figure, self.run.period_end, self.employee.work_state)
        self._terms(component.pk)[component.ceiling_figure] = term
        return round_money(value)

    def _overtime_at_ordinary_rate(self, row, start, end):
        """
        Overtime at the multiple (dated data, default 2) of the ordinary
        rate of wages, for the hours the register shows from `start` to
        `end`. The ordinary rate is made of the components marked as
        ordinary wages in force on the day the overtime was worked: a
        period's fixed figure over the working days the person's pattern
        gives the period, an hourly rate times their hours a day, and
        piece work at the daily average of what was earned on the days
        worked in the month before (the Factories Act, s.59(2)), or in this
        period if none were. A day's rate over their hours a day is the
        hour's. Totalled first, divided last, rounded once by the
        component.

        The rate is the agreed one, not what the period earned: a joiner on
        the 16th, or a month cut by unpaid leave, is paid less salary for
        it, and an hour of theirs is still worth the agreed figure over the
        period's working days (13,500 over October's 27 is 500 a day for
        all of them; what a joiner on the 16th earned, 7,000, over the 27
        would be 259). It is the same divisor proration pays salary by, so a
        day-rated worker's day of overtime matches a day of their wage. A
        rate changed inside the period is read on the day the overtime was
        worked.
        """
        from .attendance import AttendanceDay

        worked = list(AttendanceDay.objects.filter(
            employee=self.employee, on__range=(start, end), overtime_hours__gt=0).values_list("on", "overtime_hours"))
        self.__dict__.setdefault("_hours", {})[row.pk] = sum((hours for _day, hours in worked), Decimal("0"))
        if not worked:
            return Decimal("0")
        multiplier, term = figure_as_of(FigureKind.OVERTIME_MULTIPLIER, self.run.period_end, self.employee.work_state)
        hours_a_day = self.employee.hours_a_day
        kept = self._terms(row.component_id)
        kept["overtime_multiplier"], kept["hours_a_day"] = term, f"{hours_a_day.normalize():f}"
        ordinary = list(EmployeeCompensation.objects.filter(
            employee=self.employee, component__is_ordinary_wages=True, component__is_active=True,
        ).select_related("component"))
        full, averages, wages = self.period_working_days(), {}, Decimal("0")
        for day, hours in worked:
            per_day = Decimal("0")
            for component_id in sorted({other.component_id for other in ordinary}):
                rates = [other for other in ordinary if other.component_id == component_id and other.covers(day)]
                if not rates:
                    continue
                rate = rates[0]
                if rate.component.basis == ComponentBasis.PER_UNIT:
                    if component_id not in averages:
                        averages[component_id] = self._piece_day_average(rate.component)
                    per_day += averages[component_id][0]
                elif rate.component.basis == ComponentBasis.PER_HOUR:
                    per_day += rate.amount * hours_a_day
                elif full > 0:
                    per_day += rate.amount / full
            wages += hours * per_day
        if averages:
            kept["piece_days_of"] = ",".join(sorted({month for _a, month in averages.values()}))
        return wages * multiplier / hours_a_day

    def _piece_day_average(self, component):
        """(a day's piece earnings on average, the month it was read from) for overtime's ordinary rate."""
        _label, counter = PIECE_MEASURES[component.measure]
        rates = list(EmployeeCompensation.objects.filter(employee=self.employee, component=component))
        this_month = self.run.period_start.replace(day=1)
        before = this_month - datetime.timedelta(days=1)
        for first, last in ((before.replace(day=1), before), (self.run.period_start, self.run.period_end)):
            by_day = {}
            for day, quantity in counter(self.employee, last):
                rate = next((r for r in rates if r.covers(day)), None) if day >= first else None
                if rate is not None:
                    by_day[day] = by_day.get(day, Decimal("0")) + quantity * rate.amount
            if by_day:
                return sum(by_day.values(), Decimal("0")) / len(by_day), f"{first:%Y-%m}"
        return Decimal("0"), "none"

    def is_final(self):
        """Whether the person leaves inside this run's period, so no run of theirs comes after it."""
        left = self.employee.termination_date
        return left is not None and to_date(left) <= self.run.period_end

    def worked_hours(self, start=None, end=None):
        """
        Hours signed off for this person within the period, or from
        `start` to `end` inside it.

        Read from approved timesheets rather than handed in from outside.
        Hours that arrive as an argument came from a spreadsheet, and
        nothing in this system could then say where a number on a payslip
        came from.
        """
        from .timesheets import approved_hours

        return approved_hours(self.employee, start or self.run.period_start, end or self.run.period_end)

    def _sum_of(self, component, named, computed, running_taxable):
        """What `named` components came to on this slip; the taxable gross if none."""
        if not named:
            return running_taxable
        late = [other.code for other in named if other.sequence >= component.sequence]
        if late:
            # Equal counts as not before: which of two at one sequence is
            # worked out first is an accident of their codes.
            raise ValidationError(
                f"{component.code} is taken of {', '.join(late)}, which do not come before "
                f"it on the slip (sequence {component.sequence} or later); give them a lower sequence."
            )
        return sum((computed.get(other.pk, Decimal("0")) for other in named), Decimal("0"))

    def _block_start(self, months):
        """The first day of the contribution period this run falls in, counted from April."""
        end = self.run.period_end
        since_april = (end.month - 4) % 12
        start_offset = since_april - since_april % months
        year = end.year if end.month >= 4 else end.year - 1
        month = 4 + start_offset
        return datetime.date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)

    def _is_covered(self, component, computed, running_taxable):
        """
        Whether a component with a coverage ceiling applies to this
        person in this run. Decided by the first posted slip in the
        contribution period, when there is one: ESI covers somebody for
        the whole period they were covered at the start of, and a rise in
        July does not end it until October.
        """
        if component.coverage_ceiling is None:
            return True
        deciding = list(component.coverage_components.all()) or list(
            component.base_components.all())
        start = self._block_start(component.coverage_period_months)
        first = Payslip.objects.filter(
            employee=self.employee, run__status=PayRunStatus.POSTED,
            run__period_end__gte=start, run__period_end__lt=self.run.period_end,
        ).exclude(pk=self.pk).order_by("run__period_end").first()
        if first is not None:
            lines = first.lines.all()
            if deciding:
                wanted = {other.pk for other in deciding}
                wages = sum((line.amount for line in lines if line.component_id in wanted),
                            Decimal("0"))
            else:
                wages = sum((line.amount for line in lines
                             if line.kind == ComponentKind.EARNING and line.is_taxable),
                            Decimal("0"))
        else:
            wages = self._sum_of(component, deciding, computed, running_taxable)
        return wages <= component.coverage_ceiling

    def _amount_for(self, row, proportion, running_taxable, hours, computed=None):
        """
        What `row` pays on this slip: the whole period's worth when its rate
        was in force for all of the period, and otherwise the part it was in
        force for. What is dated is read for those days: hours on a
        timesheet, overtime in the register. A salary is paid for the days
        paid under the rate, as a joiner's is; anything else is the period's
        figure at this rate in proportion to the working days employed
        under it.
        """
        component = row.component
        start, end = self._span(row)
        whole = (start, end) == (self.run.period_start, self.run.period_end)
        if component.basis in (ComponentBasis.PERCENT_OF_GROSS, ComponentBasis.SLAB):
            computed = computed or {}
            if not self._is_covered(component, computed, running_taxable):
                return Decimal("0")
            base = self._sum_of(component, list(component.base_components.all()),
                                computed, running_taxable)
            ceiling = self._ceiling(component)
            if ceiling is not None:
                base = min(base, ceiling)
            if component.basis == ComponentBasis.SLAB:
                amount, term = component.slab_on(base, self.run.period_end, self.employee.work_state, self.employee)
                if term is not None:
                    self._terms(component.pk)["slab"] = term
            else:
                amount = base * row.amount / Decimal("100")
            return amount if whole else self._part(amount, start, end)
        if component.basis == ComponentBasis.OVERTIME_ORDINARY:
            return self._overtime_at_ordinary_rate(row, start, end)
        if component.basis == ComponentBasis.PER_OVERTIME_HOUR:
            from .attendance import overtime_hours

            return overtime_hours(self.employee, start, end) * row.amount
        if component.basis == ComponentBasis.PER_HOUR:
            worked = Decimal(hours) if hours is not None else self.worked_hours()
            if not worked:
                raise ValidationError(
                    f"{component.code} is paid by the hour and {self.employee} has no "
                    "approved hours in this period. Approve their timesheet, or pass "
                    "the hours in explicitly."
                )
            if whole:
                return worked * row.amount
            if hours is not None:
                # Handed in for the period, with no days to them.
                return self._part(worked * row.amount, start, end)
            return self.worked_hours(start, end) * row.amount
        if component.reduces_for_unpaid_leave:
            if whole:
                return row.amount * proportion
            full = self.period_working_days()
            return row.amount * self.paid_days(start, end) / full if full > 0 else Decimal("0")
        if proportion <= 0:
            # Somebody who was employed for none of this period is owed
            # nothing, whatever the component says it pays regardless.
            return Decimal("0")
        return row.amount if whole else self._part(row.amount, start, end)

    @serialised("payment")
    def pay(self, payment):
        """
        Settle this slip's net against a payment, clearing the control
        account.

        Net pay payable exists to be emptied. A payroll that posts the
        liability and never clears it leaves a balance that grows by a
        month's wages every month and that nobody can explain.
        """
        # The payment too: another slip could be settling with it now.
        lock_rows(payment)
        if not self.run.posted:
            raise ValidationError("A pay run must be posted before it can be paid.")
        if self.is_paid():
            raise ValidationError(f"{self.employee} has already been paid for this run.")
        if payment.is_voided():
            raise ValidationError("That payment has been voided; it settles nothing.")
        # Each of these used to mark the slip paid while net pay payable
        # went on saying the wages were owed: the slip and the control
        # account have to be asked the same question.
        if not payment.posted:
            raise ValidationError("That payment has not been posted; nothing has left the bank.")
        if payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError("Wages are settled by money paid out, not money received.")
        if payment.counterpart_account_id != _net_pay_account().pk:
            raise ValidationError(
                f"That payment was booked against {payment.counterpart_account}, not net pay "
                "payable, so it does not clear what this slip owes."
            )
        if payment.party_id != self.employee.party_id:
            raise ValidationError(
                f"That payment was made to {payment.party}, not {self.employee}."
            )
        base = Company.get().base_currency_id
        if payment.currency_id not in (None, base):
            raise ValidationError("Net pay is owed in the base currency; pay it in that currency.")
        other = payment.payslips.exclude(pk=self.pk).first()
        if other is not None:
            raise ValidationError(f"That payment already settles {other}.")
        expected = self.net()
        if round_money(payment.amount) != expected:
            raise ValidationError(
                f"This payment is {payment.amount} and {self.employee}'s net pay is "
                f"{expected}. Pay the net, or split the payment."
            )
        self.payment = payment
        super().save(update_fields=["payment", "updated_at"])
        return self


# What Payslip.is_paid() reads, for a list that asks it of every slip to prefetch with them.
SLIP_PAYMENT_READS = ("payment__journal_entry__reversed_by", "transfers__file",
                      "transfers__returned_by__journal_entry__reversed_by")


def posted_slip_over(employee, first, last=None, **narrower):
    """
    The earliest payslip of `employee` in a posted run whose period touches
    `first`..`last` (with no `last`, any day from `first` on), or None.

    What a posted run paid on is not changed under it, and the register,
    leave, timesheets and somebody's start and leaving dates each ask this
    before they change; asked here once, so a rule about what counts as paid
    on is one rule. `narrower` filters the slips further: a timesheet asks
    only of slips that paid by the hour.
    """
    slips = Payslip.objects.filter(employee=employee, run__status=PayRunStatus.POSTED,
                                   run__period_end__gte=first, **narrower)
    if last is not None:
        slips = slips.filter(run__period_start__lte=last)
    return slips.select_related("run").order_by("run__period_start").first()


class PayslipLine(AuditModel):
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=16, choices=ComponentKind.choices, editable=False)
    basis = models.CharField(max_length=16, choices=ComponentBasis.choices, editable=False)
    description = models.CharField(max_length=255)
    rate = models.DecimalField(
        max_digits=18, decimal_places=4,
        help_text="What the component was set to when this slip was worked out. Kept "
                  "so the payslip can still be explained after the next rise.",
    )
    is_taxable = models.BooleanField(default=True, editable=False)
    base_ceiling = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, editable=False,
        help_text="The wage ceiling the component capped its base at when the slip was worked "
                  "out: the ECR files the PF wages as they stood for the month (O6).",
    )
    kept_statutory = models.CharField(
        max_length=16, blank=True, editable=False,
        help_text="Which statutory contribution the component was marked as when the slip was "
                  "worked out (R7): re-marked later, a filed month's ECR named nobody.",
    )
    kept_base = models.JSONField(
        null=True, blank=True, editable=False,
        help_text="The components it was taken of, as listed then (R7): the files read the base "
                  "as it stood for the month.",
    )
    kept_terms = models.JSONField(
        null=True, blank=True, editable=False,
        help_text="The dated figures the law set that the line was worked out at, each [row, from, value]: "
                  "the wage ceilings, the slab schedule, the overtime multiple. The files and registers read "
                  "these, never today's table.",
    )
    advance = models.ForeignKey(
        "hr.SalaryAdvance", null=True, blank=True, on_delete=models.PROTECT, related_name="recoveries",
        editable=False, help_text="The advance or loan this deduction recovers.",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, editable=False,
        help_text="On piece work, the units this line pays for: what was made to "
                  "the end of the period less what earlier runs paid.",
    )
    posted_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Where this line actually landed when the run posted, frozen so a "
                  "reversal gives it back to the same place.",
    )
    posted_liability_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="On a deduction or employer contribution, what it was owed into when "
                  "the run posted: what the liabilities report counts it against.",
    )

    class Meta:
        ordering = ["component__sequence", "component__code"]
        constraints = [
            # Which way a line faces is its kind, not its sign. A negative
            # earning would be a deduction charged to a wages account, and
            # it would post backwards.
            models.CheckConstraint(check=Q(amount__gt=0), name="payslip_line_amount_positive"),
        ]

    def __str__(self):
        return f"{self.description} {self.amount}"

    def resolve_account(self):
        """
        Which account this line charges.

        A department's cost centre beats the component's own account, so
        wages land against the team that incurred them rather than in one
        undivided payroll expense. Once posted, the frozen answer wins:
        moving a department to another cost centre must not restate the
        entries it has already produced.
        """
        if self.posted_account_id:
            return self.posted_account
        if self.advance_id:
            # Where the advance was given: its posted payment's account (review RP4), not the component's now.
            return self.advance.receivable_account
        if self.kind == ComponentKind.DEDUCTION and self.component.kind == ComponentKind.DEDUCTION:
            return self.component.liability_account
        # An earning, an employer cost, or an earning taken back (a final
        # run's piece work), which goes back to where the earning was charged.
        department = self.payslip.employee.department
        if department is not None and department.cost_centre_id:
            return department.cost_centre
        return self.component.expense_account


def _as_worked_out(line):
    """What a payslip line says about somebody's pay: two workings of a slip are compared on it."""
    terms = tuple(sorted((key, tuple(value) if isinstance(value, list) else value)
                         for key, value in (line.kept_terms or {}).items()))
    return (line.component_id, line.kind, line.basis, line.is_taxable, line.rate, line.amount, line.quantity,
            line.base_ceiling, line.kept_statutory, tuple(line.kept_base or ()), terms, line.advance_id)


def _by_component(lines):
    totals = {}
    for line in lines:
        totals[line.component] = totals.get(line.component, Decimal("0.00")) + line.amount
    return totals


def _month_of(day):
    day = to_date(day)
    return datetime.date(day.year, day.month, 1)


def _deducted(account, period):
    """What posted runs for `period` owe into `account`."""
    period = _month_of(period)
    following = (period + datetime.timedelta(days=32)).replace(day=1)
    return PayslipLine.objects.filter(
        posted_liability_account=account, payslip__run__status=PayRunStatus.POSTED,
        payslip__run__period_end__gte=period, payslip__run__period_end__lt=following,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")


class StatutoryRemittance(AuditModel):
    """
    Money paid over to the PF, ESI or tax authority, against one month's
    liability on one account.

    A payment against PF payable said that something was paid, never for
    which month, so nobody could say whether June was settled or what
    was due by the 15th. Recorded against the month, with the same
    questions asked of the payment as a payslip asks: posted, paid out,
    booked against this account, and not spent twice.
    """

    payment = models.ForeignKey(
        "accounting.Payment", on_delete=models.PROTECT, related_name="remittances"
    )
    liability_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="+"
    )
    period = models.DateField(help_text="The month the deductions were made for.")
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["-period", "liability_account", "id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="remittance_amount_positive"),
        ]

    def __str__(self):
        return f"{self.liability_account.code} {self.period:%b %Y} {self.amount}"

    def locked_before_it(self):
        """
        The liability account, then the payment, as save() takes them (lock_rows' order): an edit
        through the API holds them before the remittance (O37).
        """
        return [self.liability_account if self.liability_account_id else None,
                self.payment if self.payment_id else None]

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                "A remittance is not edited: it is the record of money paid over for its month. A "
                "payment that did not go through is voided, and the one that did is recorded."
            )
        self.period = _month_of(self.period)
        self.amount = round_money(Decimal(self.amount))
        payment = self.payment
        # What is left on the payment and owed on the account for the month
        # are both read below; another remittance could spend either.
        lock_rows(payment, self.liability_account)
        if not payment.posted:
            raise ValidationError("That payment has not been posted; nothing has left the bank.")
        if payment.is_voided():
            raise ValidationError("That payment has been voided; it pays nothing over.")
        if payment.direction != PaymentDirection.DISBURSEMENT:
            raise ValidationError("A remittance is money paid out, not money received.")
        if payment.counterpart_account_id != self.liability_account_id:
            raise ValidationError(
                f"That payment was booked against {payment.counterpart_account}, not "
                f"{self.liability_account}, so it does not clear this liability."
            )
        base = Company.get().base_currency_id
        if payment.currency_id not in (None, base):
            raise ValidationError("Statutory dues are paid in the base currency.")
        used = payment.remittances.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if used + self.amount > payment.amount:
            raise ValidationError(
                f"That payment is {payment.amount} and {used} of it is already accounted "
                f"for; {self.amount} more would pay over money that never left the bank."
            )
        owed = _deducted(self.liability_account, self.period) - remitted(
            self.liability_account, self.period)
        if self.amount > owed:
            raise ValidationError(
                f"{self.liability_account} owes {owed} for {self.period:%B %Y}; "
                f"{self.amount} is more than was deducted. Check the month."
            )
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        """
        Not while its payment stands: it is the record of what that money
        paid. Deleted, June's 7,200 to the EPFO stayed paid over in the
        ledger, the report said June was owed again, and the run it was
        deducted by could then be voided under it - the hole the void's own
        guard closes. A payment that bounced paid nothing over, and its
        remittance then counts for nothing and goes.
        """
        lock_rows(self.payment, self.liability_account)
        if not self.payment.is_voided():
            raise ValidationError(
                f"{self.payment.number} paid {self.amount} over against {self.liability_account} for "
                f"{self.period:%B %Y}, and this is the record of it: it stays while the payment stands. A "
                "payment that did not go through is voided, and its remittance can then go.")
        return super().delete(*args, **kwargs)


def remitted(account, period, as_of=None):
    """
    Paid over against `account` for `period`, by payments that still stand;
    or, `as_of` a day, by payments made by then and not voided by then. As
    of 10 July, June's PF paid over on the 15th read as paid already.
    """
    rows = StatutoryRemittance.objects.filter(
        liability_account=account, period=_month_of(period)
    ).select_related("payment__voided_entry", "payment__journal_entry")
    if as_of is None:
        return sum((row.amount for row in rows if not row.payment.is_voided()), Decimal("0"))
    return sum((row.amount for row in rows if _stood_on(row.payment, to_date(as_of))), Decimal("0"))


def _stood_on(payment, day):
    """Made by `day` and not yet voided on it: the payment as the ledger stood that day."""
    if to_date(payment.payment_date) > day:
        return False
    if payment.voided_entry_id:
        return to_date(payment.voided_entry.date) > day
    return not payment.journal_entry.reversed_by.filter(date__lte=day).exists()


def statutory_liabilities(as_of=None):
    """
    Per liability account and month: what payroll deducted and the
    company owes on top, what has been paid over, what is left, and by
    when it was due.

    Read from the posted lines, each against the account it was owed
    into when its run posted, and from remittances whose payments still
    stand: a voided run owes nothing and a bounced payment paid nothing.
    """
    as_of = to_date(as_of) or timezone.localdate()
    # A run voided since `as_of` was owed on it, as a payment returned since
    # had paid over: voided in August, June read as never deducted in July.
    lines = PayslipLine.objects.filter(
        posted_liability_account__isnull=False,
        payslip__run__status__in=(PayRunStatus.POSTED, PayRunStatus.VOIDED),
        payslip__run__period_end__lte=as_of,
    ).filter(
        Q(payslip__run__voided_entry__isnull=True) | Q(payslip__run__voided_entry__date__gt=as_of)
    ).select_related("posted_liability_account", "component", "payslip__run")
    rows, due_days = {}, {}
    for line in lines:
        key = (line.posted_liability_account, _month_of(line.payslip.run.period_end))
        rows[key] = rows.get(key, Decimal("0")) + line.amount
        day = line.component.remit_by_day
        if day is not None:
            due_days[key] = min(due_days.get(key, day), day)
    result = []
    for (account, period), deducted in sorted(rows.items(),
                                              key=lambda item: (item[0][1], item[0][0].code)):
        paid = remitted(account, period, as_of)
        following = (period + datetime.timedelta(days=32)).replace(day=1)
        due = following.replace(day=due_days[(account, period)]) \
            if (account, period) in due_days else None
        outstanding = deducted - paid
        result.append({
            "account": account, "period": period, "deducted": deducted, "remitted": paid,
            "outstanding": outstanding, "due_date": due,
            "overdue": bool(due and outstanding > 0 and as_of > due),
        })
    return result
