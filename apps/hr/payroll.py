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
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
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
    Company,
    DocumentSequence,
    lock_rows,
    serialised,
    to_date,
)

from .calendars import working_days
from .models import Employee, LeaveRequest, LeaveStatus, reversal_day


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
        if self.basis == ComponentBasis.PER_OVERTIME_HOUR and self.kind != ComponentKind.EARNING:
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

    def slab_for(self, base, month):
        """The slab amount for `base` in `month`; one for that month wins."""
        rows = [slab for slab in self.slabs.all()
                if slab.above < base and (slab.up_to is None or base <= slab.up_to)]
        dated = [slab for slab in rows if slab.month == month]
        general = [slab for slab in rows if slab.month is None]
        chosen = (dated or general or [None])[0]
        return chosen.amount if chosen is not None else Decimal("0")


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

    class Meta:
        ordering = ["component", "month", "above"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0), name="slab_amount_not_negative"),
            models.CheckConstraint(check=Q(above__gte=0), name="slab_above_not_negative"),
            models.CheckConstraint(check=Q(up_to__isnull=True) | Q(up_to__gt=F("above")),
                                   name="slab_up_to_above_its_floor"),
            models.CheckConstraint(check=Q(month__isnull=True) | Q(month__gte=1, month__lte=12),
                                   name="slab_month_is_a_month"),
        ]

    def save(self, *args, **kwargs):
        # Two bands over one wage: slab_for took the first, and tax was
        # under-deducted with nothing said. Bands of one component for one
        # month (or for every month) may touch, not overlap.
        others = PayComponentSlab.objects.filter(component_id=self.component_id, month=self.month).exclude(pk=self.pk)
        for other in others:
            starts_below_its_top = other.up_to is None or self.above < other.up_to
            its_start_below_my_top = self.up_to is None or other.above < self.up_to
            if starts_below_its_top and its_start_below_my_top:
                raise ValidationError(
                    f"Over {self.above} up to {self.up_to or 'anything'} overlaps the band over "
                    f"{other.above} up to {other.up_to or 'anything'}; a wage must fall in one band."
                )
        super().save(*args, **kwargs)

    def __str__(self):
        top = self.up_to if self.up_to is not None else "and over"
        return f"{self.component.code} {self.above}-{top}: {self.amount}"


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

    def save(self, *args, **kwargs):
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

    def delete(self, *args, **kwargs):
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
        if not any(slip.lines.exists() for slip in slips):
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
            if not slip.lines.exists():
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

        net_total = Decimal("0")
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
                owed_into = None if ComponentKind.EARNING in (line.kind, line.component.kind) else (
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
                elif line.kind == ComponentKind.DEDUCTION:
                    add(credits, account, line.amount)
                else:
                    add(debits, (account, centre), line.amount)
                    add(credits, line.component.liability_account, line.amount)
            net_total += slip.net()

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
        on_date = reversal_day(on_date, self.pay_date, "This run was paid")
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
        if previous is not None and previous.status == PayRunStatus.POSTED:
            raise ValidationError(
                "Cannot modify a posted pay run. Void it and raise another."
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

    class Meta:
        ordering = ["employee"]
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
        return self.payment_id is not None and not self.payment.is_voided()

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
        super().save(update_fields=["hours", "updated_at"])
        return list(self.lines.all())

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
        now, then = self.work_out(self.hours), list(self.lines.select_related("component"))
        if Counter(map(_as_worked_out, now)) == Counter(map(_as_worked_out, then)):
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
                amount, quantity, row = self.piece_work(component)
                owed = [(row, amount, quantity)]
            else:
                # Each rate is taken of the gross before the component, not
                # of what its own earlier rate added to it.
                before = running_taxable
                owed = [(row, self._amount_for(row, proportion, before, hours, computed), None)
                        for row in group]
            for row, amount, quantity in owed:
                amount, kind = component.round(amount), component.kind
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
                    description=component.name if kind == component.kind else f"{component.name}, taken back",
                    # Frozen: the rate, the basis and the account as they stood
                    # when this slip was worked out. All three can change, and
                    # a payslip that restates itself afterwards cannot be
                    # reconciled to the entry that paid it.
                    rate=row.amount,
                    basis=component.basis,
                    is_taxable=component.is_taxable,
                    amount=amount,
                    quantity=quantity,
                ))
                if kind == ComponentKind.EARNING and component.is_taxable:
                    running_taxable += amount
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

    def piece_work(self, component):
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
        earned, made = Decimal("0"), Decimal("0")
        for day, quantity in counter(self.employee, self.run.period_end):
            row = next((rate for rate in rates if rate.covers(day)), None)
            if row is not None:
                earned += quantity * row.amount
                made += quantity
        # What a final run took back counts against what was paid.
        paid_amount, paid_quantity = Decimal("0"), Decimal("0")
        for kind, amount, quantity in paid_lines.values_list("kind", "amount", "quantity"):
            sign = -1 if kind == ComponentKind.DEDUCTION else 1
            paid_amount += sign * amount
            paid_quantity += sign * (quantity or Decimal("0"))
        in_force = next((rate for rate in reversed(rates)
                         if rate.effective_from <= self.run.period_end), rates[-1])
        return round_money(earned) - paid_amount, made - paid_quantity, in_force

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
            if component.base_ceiling is not None:
                base = min(base, component.base_ceiling)
            if component.basis == ComponentBasis.SLAB:
                amount = component.slab_for(base, self.run.period_end.month)
            else:
                amount = base * row.amount / Decimal("100")
            return amount if whole else self._part(amount, start, end)
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
    return (line.component_id, line.kind, line.basis, line.is_taxable, line.rate, line.amount, line.quantity)


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
    lines = PayslipLine.objects.filter(
        posted_liability_account__isnull=False, payslip__run__status=PayRunStatus.POSTED,
        payslip__run__period_end__lte=as_of,
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
