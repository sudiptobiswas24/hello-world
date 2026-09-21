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
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import JournalEntry, JournalLine, round_money
from apps.core.models import AuditModel, Company, DocumentSequence, to_date

from .calendars import working_days
from .models import Employee, LeaveRequest, LeaveStatus


class ComponentKind(models.TextChoices):
    EARNING = "earning", "Earning"
    DEDUCTION = "deduction", "Employee deduction"
    EMPLOYER_COST = "employer_cost", "Employer cost"


class ComponentBasis(models.TextChoices):
    FIXED = "fixed", "Fixed amount"
    PERCENT_OF_GROSS = "percent", "Percentage of taxable gross"
    PER_HOUR = "per_hour", "Rate per hour"


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

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
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

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


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

    @transaction.atomic
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
        people = [
            person for person in people
            if person.is_employed_on(self.period_start)
            or person.is_employed_on(self.period_end)
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

    @transaction.atomic
    def post(self, memo=""):
        if self.status == PayRunStatus.POSTED:
            raise ValidationError("This pay run is already posted.")
        if self.status == PayRunStatus.VOIDED:
            raise ValidationError("A voided pay run cannot be posted again.")
        slips = list(self.payslips.prefetch_related("lines"))
        if not slips:
            raise ValidationError("Cannot post a pay run with no payslips.")
        self._check_not_already_paid()

        if not self.number:
            self.number = DocumentSequence.next_for(
                "hr.payrun", self.pay_date, name="Pay Runs", prefix="PAY-"
            )
        label = memo or f"Payroll {self.number} for {self.period_start}..{self.period_end}"

        debits, credits = {}, {}

        def add(bucket, account, amount):
            if account is None or not amount:
                return
            bucket[account] = bucket.get(account, Decimal("0")) + amount

        net_total = Decimal("0")
        for slip in slips:
            for line in slip.lines.all():
                account = line.resolve_account()
                # Where it landed is a fact from here on, not something a
                # later reader recomputes: a department can be moved to a
                # different cost centre and this entry must not follow it.
                if line.posted_account_id != getattr(account, "pk", None):
                    line.posted_account = account
                    super(PayslipLine, line).save(
                        update_fields=["posted_account", "updated_at"]
                    )
                if line.kind == ComponentKind.EARNING:
                    add(debits, account, line.amount)
                elif line.kind == ComponentKind.DEDUCTION:
                    add(credits, account, line.amount)
                else:
                    add(debits, account, line.amount)
                    add(credits, line.component.liability_account, line.amount)
            net_total += slip.net()

        add(credits, _net_pay_account(), net_total)

        entry = JournalEntry.objects.create(
            date=self.pay_date, reference=self.number, memo=label[:255]
        )
        for account, amount in debits.items():
            JournalLine.objects.create(
                entry=entry, account=account, debit=round_money(amount),
                description=label[:255],
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

    @transaction.atomic
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
        paid = self.payslips.filter(payment__isnull=False).first()
        if paid is not None:
            raise ValidationError(
                f"{paid.employee} has already been paid from this run. Reverse the "
                "payment before voiding the payroll that owed it."
            )
        self.voided_entry = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or timezone.now().date(),
            memo=memo or f"Void of payroll {self.number}",
        )
        self.status = PayRunStatus.VOIDED
        self.voided_at = timezone.now()
        super().save(update_fields=["voided_entry", "status", "voided_at", "updated_at"])
        return self.voided_entry

    def unpaid_net(self):
        """What this run still owes its people."""
        return sum(
            (slip.net() for slip in self.payslips.filter(payment__isnull=True)),
            Decimal("0"),
        )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = PayRun.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status == PayRunStatus.POSTED:
                raise ValidationError(
                    "Cannot modify a posted pay run. Void it and raise another."
                )
        super().save(*args, **kwargs)


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
        return self.payment_id is not None

    # -- working out what somebody earned --------------------------------

    def period_working_days(self):
        return Decimal(working_days(
            self.run.period_start, self.run.period_end,
            pattern=self.employee.working_days,
            region=self.employee.holiday_region,
        ))

    def days_employed(self):
        """
        Working days in this period the person was actually employed for.

        A joiner halfway through the month is owed half a month, and
        paying them a full salary because the run happened to include
        them is the kind of error nobody reports.
        """
        start = max(self.run.period_start, self.employee.hire_date)
        end = self.run.period_end
        if self.employee.termination_date:
            end = min(end, self.employee.termination_date)
        if end < start:
            return Decimal("0")
        return Decimal(working_days(
            start, end,
            pattern=self.employee.working_days,
            region=self.employee.holiday_region,
        ))

    def unpaid_leave_days(self):
        """
        Approved days in this period under a policy that does not pay.

        This is the join between the two halves of the module: leave
        knows the days, payroll knows what a day costs, and neither is
        much use without the other.
        """
        total = Decimal("0")
        for request in LeaveRequest.objects.filter(
            employee=self.employee,
            status=LeaveStatus.APPROVED,
            policy__is_paid=False,
            start_date__lte=self.run.period_end,
            end_date__gte=self.run.period_start,
        ).select_related("policy"):
            overlap_start = max(request.start_date, self.run.period_start)
            overlap_end = min(request.end_date, self.run.period_end)
            total += Decimal(working_days(
                overlap_start, overlap_end,
                pattern=self.employee.working_days,
                region=self.employee.holiday_region,
            ))
        return total

    def paid_proportion(self):
        """
        The fraction of a full period's pay this person has earned.

        Days employed, less unpaid leave, over the days the period holds.
        """
        full = self.period_working_days()
        if full <= 0:
            return Decimal("0")
        earned = self.days_employed() - self.unpaid_leave_days()
        if earned <= 0:
            return Decimal("0")
        return min(earned / full, Decimal("1"))

    @transaction.atomic
    def calculate(self, hours=None):
        """
        Build this slip's lines from the compensation in force.

        Components are worked out in sequence order, so a percentage
        component can only ever be taken of what has already been
        computed. A percentage of a gross that later grows is a number
        that was right when it was calculated and wrong when it was
        posted.
        """
        self.lines.all().delete()
        proportion = self.paid_proportion()
        rows = EmployeeCompensation.objects.filter(
            employee=self.employee,
            component__is_active=True,
            effective_from__lte=self.run.period_end,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=self.run.period_start)
        ).select_related("component").order_by("component__sequence", "component__code")

        running_taxable = Decimal("0")
        for row in rows:
            component = row.component
            amount = self._amount_for(row, proportion, running_taxable, hours)
            amount = round_money(amount)
            if not amount:
                continue
            PayslipLine.objects.create(
                payslip=self,
                component=component,
                kind=component.kind,
                description=component.name,
                # Frozen: the rate, the basis and the account as they stood
                # when this slip was worked out. All three can change, and
                # a payslip that restates itself afterwards cannot be
                # reconciled to the entry that paid it.
                rate=row.amount,
                basis=component.basis,
                is_taxable=component.is_taxable,
                amount=amount,
            )
            if component.kind == ComponentKind.EARNING and component.is_taxable:
                running_taxable += amount
        return list(self.lines.all())

    def worked_hours(self):
        """
        Hours signed off for this person within the period.

        Read from approved timesheets rather than handed in from outside.
        Hours that arrive as an argument came from a spreadsheet, and
        nothing in this system could then say where a number on a payslip
        came from.
        """
        from .timesheets import approved_hours

        return approved_hours(self.employee, self.run.period_start, self.run.period_end)

    def _amount_for(self, row, proportion, running_taxable, hours):
        component = row.component
        if component.basis == ComponentBasis.PERCENT_OF_GROSS:
            return running_taxable * row.amount / Decimal("100")
        if component.basis == ComponentBasis.PER_HOUR:
            worked = Decimal(hours) if hours is not None else self.worked_hours()
            if not worked:
                raise ValidationError(
                    f"{component.code} is paid by the hour and {self.employee} has no "
                    "approved hours in this period. Approve their timesheet, or pass "
                    "the hours in explicitly."
                )
            return worked * row.amount
        amount = row.amount
        if component.reduces_for_unpaid_leave:
            amount = amount * proportion
        elif proportion <= 0:
            # Somebody who was employed for none of this period is owed
            # nothing, whatever the component says it pays regardless.
            return Decimal("0")
        return amount

    @transaction.atomic
    def pay(self, payment):
        """
        Settle this slip's net against a payment, clearing the control
        account.

        Net pay payable exists to be emptied. A payroll that posts the
        liability and never clears it leaves a balance that grows by a
        month's wages every month and that nobody can explain.
        """
        if not self.run.posted:
            raise ValidationError("A pay run must be posted before it can be paid.")
        if self.is_paid():
            raise ValidationError(f"{self.employee} has already been paid for this run.")
        expected = self.net()
        if round_money(payment.amount) != expected:
            raise ValidationError(
                f"This payment is {payment.amount} and {self.employee}'s net pay is "
                f"{expected}. Pay the net, or split the payment."
            )
        self.payment = payment
        super().save(update_fields=["payment", "updated_at"])
        return self


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
    posted_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Where this line actually landed when the run posted, frozen so a "
                  "reversal gives it back to the same place.",
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
        if self.kind == ComponentKind.DEDUCTION:
            return self.component.liability_account
        department = self.payslip.employee.department
        if department is not None and department.cost_centre_id:
            return department.cost_centre
        return self.component.expense_account
