"""
People, and the days they are not at work.

The first version of this module modelled employees and leave requests
and enforced almost none of what it said it enforced: every rule lived
in `clean()`, Django never calls `full_clean()` on save, and every test
called it by hand. A probe that created records the way code does found
thirteen holes out of thirteen checks — a leaver booking next summer's
holiday, an employee managing themselves, two approved requests for the
same week, and an approval with no way back.

So the rules live in `save()` here. `clean()` still holds them too, for
forms and the admin, but nothing depends on it being called.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, Party, PartyRole, to_date

from .calendars import (  # noqa: F401
    DEFAULT_WORKING_DAYS,
    PublicHoliday,
    completed_months,
    parse_working_days,
    working_days,
)


def _require_employee_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists():
        raise ValidationError(f"{party} does not have the Employee role.")


class EmploymentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ON_LEAVE = "on_leave", "On Leave"
    TERMINATED = "terminated", "Terminated"


class Department(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    manager = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_departments",
    )
    cost_centre = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
        help_text="Where this department's payroll is charged. Falls back to the "
                  "company default when blank.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Employee(AuditModel):
    """
    An Employee is always backed by a core.Party with the EMPLOYEE role —
    HR doesn't invent its own idea of "a person" any more than Sales
    invents its own idea of "a customer".
    """

    party = models.OneToOneField(Party, on_delete=models.PROTECT, related_name="employee_profile")
    employee_number = models.CharField(max_length=32, unique=True)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees"
    )
    manager = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="direct_reports"
    )
    job_title = models.CharField(max_length=255, blank=True)
    hire_date = models.DateField()
    termination_date = models.DateField(null=True, blank=True)
    employment_status = models.CharField(
        max_length=16, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE
    )
    working_days = models.CharField(
        max_length=7, default=DEFAULT_WORKING_DAYS,
        help_text="ISO weekday numbers this person works: 1 is Monday, 7 is Sunday. "
                  "A four-day week is '1234'. Leave is counted against this, so a "
                  "part-timer is not charged for days they were never going to work.",
    )
    holiday_region = models.CharField(
        max_length=32, blank=True,
        help_text="Which public holidays apply. Blank means the company-wide set only.",
    )

    class Meta:
        ordering = ["employee_number"]

    def __str__(self):
        return f"{self.employee_number} - {self.party.name}"

    def is_employed_on(self, on_date):
        on_date = to_date(on_date)
        if on_date < self.hire_date:
            return False
        return self.termination_date is None or on_date <= self.termination_date

    def management_chain(self):
        """Everyone above this person, closest first."""
        chain, seen = [], {self.pk}
        node = self.manager
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            chain.append(node)
            node = node.manager
        return chain

    def clean(self):
        # Django lets an ISO string be assigned to a DateField and does not
        # coerce it until a refresh, so every comparison below would be a
        # string against a date. Normalising here rather than at each use
        # keeps the next reader from having to know that.
        self.hire_date = to_date(self.hire_date)
        self.termination_date = to_date(self.termination_date)
        _require_employee_role(self.party)
        if self.manager_id and self.manager_id == self.pk:
            raise ValidationError("An employee cannot be their own manager.")
        if self.termination_date and self.employment_status != EmploymentStatus.TERMINATED:
            raise ValidationError(
                "employment_status must be 'terminated' when a termination_date is set."
            )
        if self.termination_date and self.termination_date < self.hire_date:
            raise ValidationError("A termination date cannot precede the hire date.")
        parse_working_days(self.working_days)
        self._check_no_management_cycle()

    def _check_no_management_cycle(self):
        """
        A reports to B reports to A is not a hierarchy, and every walk up
        it — an approval chain, an org chart, a payroll authorisation —
        runs forever. The self-reference check caught only the shortest
        case of it.
        """
        seen = {self.pk} if self.pk else set()
        node = self.manager
        while node is not None:
            if node.pk in seen:
                raise ValidationError(
                    f"{self} would report to itself through {node}; a management line "
                    "cannot loop."
                )
            seen.add(node.pk)
            node = node.manager

    def dangling_after(self, on_date):
        """
        Approved leave and claimed hours that fall after a date.

        Asked before somebody's leaving date is set, because an approved
        holiday in March does not survive a December departure and time
        claimed after the last day was never worked — but both records
        look entirely reasonable on their own, and nothing would have
        said so.
        """
        on_date = to_date(on_date)
        if on_date is None or not self.pk:
            return [], []
        leave = list(
            self.leave_requests.filter(
                status=LeaveStatus.APPROVED, end_date__gt=on_date
            ).order_by("start_date")
        )
        from .timesheets import TimesheetEntry

        hours = list(
            TimesheetEntry.objects.filter(
                timesheet__employee=self, date__gt=on_date
            ).order_by("date")
        )
        return leave, hours

    @transaction.atomic
    def terminate(self, on_date, cancel_future=False):
        """
        Set a leaving date, having dealt with what lies beyond it.

        `cancel_future` cancels approved leave that starts after the date
        and removes time claimed after it, and says what it did. Without
        it the save below refuses and names the first thing in the way,
        because cancelling somebody's holiday is a decision rather than a
        side effect of editing a field.
        """
        on_date = to_date(on_date)
        cancelled, removed = [], []
        if cancel_future:
            leave, hours = self.dangling_after(on_date)
            for request in leave:
                if request.start_date <= on_date:
                    # Splitting somebody's holiday across their last day is
                    # a decision too, and not one to make for them.
                    raise ValidationError(
                        f"{request} spans {on_date}. Shorten or cancel it before "
                        "setting a leaving date inside it."
                    )
                request.cancel(on_date=on_date)
                cancelled.append(request)
            for entry in hours:
                entry.timesheet.send_back() if entry.timesheet.status != "draft" else None
                entry.delete()
                removed.append(entry)
        self.termination_date = on_date
        self.employment_status = EmploymentStatus.TERMINATED
        self.save()
        return cancelled, removed

    def _check_nothing_dangling(self):
        if self.termination_date is None:
            return
        previous = Employee.objects.filter(pk=self.pk).first() if self.pk else None
        if previous is not None and previous.termination_date == self.termination_date:
            return
        leave, hours = self.dangling_after(self.termination_date)
        if leave:
            raise ValidationError(
                f"{self} has approved leave to {leave[0].end_date}, after the leaving "
                f"date of {self.termination_date}. Cancel it first, or use "
                "terminate(cancel_future=True)."
            )
        if hours:
            raise ValidationError(
                f"{self} has time claimed on {hours[0].date}, after the leaving date "
                f"of {self.termination_date}. Remove it first, or use "
                "terminate(cancel_future=True)."
            )

    def save(self, *args, **kwargs):
        # In save() and not only clean(), because Django never calls
        # full_clean() for you and nothing here is created through a form.
        # Every rule this class claimed to enforce was decorative until a
        # probe created records the way the rest of the codebase does.
        self.clean()
        self._check_nothing_dangling()
        super().save(*args, **kwargs)


class LeavePolicy(AuditModel):
    """
    How much of a given kind of leave a year buys.

    Separate from the request so that changing next year's entitlement is
    a policy change rather than an edit to everybody's history, and so
    that an individual arrangement can override it without either of them
    having to know about the other.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    leave_type = models.CharField(max_length=16, choices=[
        ("vacation", "Vacation"), ("sick", "Sick"), ("unpaid", "Unpaid"), ("other", "Other"),
    ])
    annual_days = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Working days a full year grants.",
    )
    accrues_monthly = models.BooleanField(
        default=False,
        help_text="Earn it a twelfth at a time rather than all of it on day one. "
                  "A joiner in November has not earned a year's holiday.",
    )
    carry_over_limit = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Most days that may be carried into the next year. Anything above "
                  "it lapses.",
    )
    allows_negative = models.BooleanField(
        default=False,
        help_text="Permit booking beyond the balance — for sick leave, which is not "
                  "earned, and for companies that let holiday go overdrawn.",
    )
    is_paid = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "leave policies"

    def __str__(self):
        return f"{self.code} - {self.name}"

    def entitlement_for(self, employee, year, as_of=None):
        """
        Days this employee has earned under this policy by `as_of`.

        Prorated for a mid-year joiner or leaver, because a full year's
        holiday for somebody who was here for two months of it is a
        number nobody can defend. Monthly accrual prorates again within
        the year, so the balance is what has been earned rather than what
        will eventually be earned.
        """
        override = LeaveEntitlement.objects.filter(
            employee=employee, policy=self, year=year
        ).first()
        annual = override.days if override else self.annual_days
        carried = override.carried_over if override else Decimal("0")

        year_start = datetime.date(year, 1, 1)
        year_end = datetime.date(year, 12, 31)
        start = max(year_start, employee.hire_date)
        end = min(year_end, employee.termination_date or year_end)
        if end < start:
            return Decimal("0")

        earned = annual
        served = (end - start).days + 1
        whole_year = (year_end - year_start).days + 1
        if served < whole_year:
            earned = (annual * Decimal(served) / Decimal(whole_year))

        if self.accrues_monthly:
            # A twelfth at a time, which is what the field says and what a
            # leave policy actually does. Accruing by the day instead
            # would be a different rule wearing the same name, and it puts
            # a fraction of a day on every balance somebody reads.
            as_of = to_date(as_of) or timezone.now().date()
            months = completed_months(start, end)
            if months:
                earned_months = completed_months(start, min(as_of, end))
                earned = earned * Decimal(earned_months) / Decimal(months)

        return (earned + carried).quantize(Decimal("0.01"))


class LeaveEntitlement(AuditModel):
    """
    One person's allowance for one year, where it differs from the policy.

    A row exists only when somebody negotiated something: the policy is
    the answer otherwise. Writing a row per employee per year whether or
    not it differs would make the policy unchangeable in practice, since
    changing it would touch nothing.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="entitlements")
    policy = models.ForeignKey(LeavePolicy, on_delete=models.PROTECT, related_name="entitlements")
    year = models.PositiveIntegerField()
    days = models.DecimalField(max_digits=6, decimal_places=2)
    carried_over = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        help_text="Brought in from last year, already capped by that year's policy.",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-year", "employee"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "policy", "year"], name="one_entitlement_per_year"
            ),
            models.CheckConstraint(check=Q(days__gte=0), name="entitlement_days_not_negative"),
        ]

    def __str__(self):
        return f"{self.employee} {self.policy.code} {self.year}: {self.days}"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


# Kept as a plain list of choices for the requests that predate policies,
# so a company can run leave without configuring one.
class LeaveType(models.TextChoices):
    VACATION = "vacation", "Vacation"
    SICK = "sick", "Sick"
    UNPAID = "unpaid", "Unpaid"
    OTHER = "other", "Other"


class LeaveRequest(AuditModel):
    employee = models.ForeignKey(Employee, related_name="leave_requests", on_delete=models.CASCADE)
    policy = models.ForeignKey(
        LeavePolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="requests",
        help_text="Which allowance this draws against. Without one the request is "
                  "recorded and counts against nothing.",
    )
    leave_type = models.CharField(max_length=16, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField(
        default=False,
        help_text="A single day taken as a half. Only meaningful when the request "
                  "is one day long.",
    )
    status = models.CharField(max_length=16, choices=LeaveStatus.choices, default=LeaveStatus.PENDING)
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    days_taken = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Working days this cost, frozen when it was approved. The working "
                  "pattern and the public holiday list both change; what an approved "
                  "holiday cost does not.",
    )

    class Meta:
        ordering = ["-start_date"]
        permissions = [("decide_leaverequest", "Can approve or reject leave requests")]
        constraints = [
            models.CheckConstraint(
                check=Q(end_date__gte=models.F("start_date")),
                name="leave_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.employee} {self.leave_type} {self.start_date}..{self.end_date} [{self.status}]"

    def is_open(self):
        """Booked or taken: the states that hold days against a balance."""
        return self.status in (LeaveStatus.PENDING, LeaveStatus.APPROVED)

    def days(self):
        """
        Working days this request covers, for this person's pattern.

        Once approved the frozen figure is returned instead. What a
        holiday cost is a fact recorded when it was granted; recomputing
        it after the working pattern or the holiday calendar changed
        would silently restate last year's balances.
        """
        if self.days_taken is not None:
            return self.days_taken
        return self.compute_days()

    def compute_days(self):
        total = Decimal(working_days(
            self.start_date, self.end_date,
            pattern=self.employee.working_days,
            region=self.employee.holiday_region,
        ))
        if self.half_day and self.start_date == self.end_date and total:
            total = total / 2
        return total.quantize(Decimal("0.01"))

    def clean(self):
        self.start_date = to_date(self.start_date)
        self.end_date = to_date(self.end_date)
        if self.end_date < self.start_date:
            raise ValidationError("end_date cannot be before start_date.")
        if self.half_day and self.start_date != self.end_date:
            raise ValidationError("A half day is one day; give it the same start and end.")
        if self.start_date.year != self.end_date.year:
            # An allowance is annual, so days either side of new year come
            # out of two different ones. Charging all of them to the year
            # the holiday started in overspends one and underspends the
            # other, and a single frozen total cannot be split afterwards.
            raise ValidationError(
                "This leave crosses a year end, and an allowance is annual. Book the "
                "days either side of it as two requests."
            )
        if self.employee_id:
            self._check_within_employment()
            self._check_no_overlap()
        if self.policy_id and self.leave_type and self.policy.leave_type != self.leave_type:
            raise ValidationError(
                f"{self.policy} grants {self.policy.get_leave_type_display().lower()} "
                f"leave, not {self.get_leave_type_display().lower()}."
            )

    def _check_within_employment(self):
        if not self.employee.is_employed_on(self.start_date):
            raise ValidationError(
                f"{self.employee} is not employed on {self.start_date}; leave cannot be "
                "booked outside employment."
            )
        if not self.employee.is_employed_on(self.end_date):
            raise ValidationError(
                f"{self.employee} is not employed on {self.end_date}; leave cannot run "
                "past the end of employment."
            )

    def _check_no_overlap(self):
        """
        The same day cannot be booked off twice.

        Pending counts as well as approved: two requests for the same week
        sitting in a manager's queue is the same double booking, found a
        week later.
        """
        if not self.is_open():
            return
        clashes = LeaveRequest.objects.filter(
            employee=self.employee,
            status__in=(LeaveStatus.PENDING, LeaveStatus.APPROVED),
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        )
        if self.pk:
            clashes = clashes.exclude(pk=self.pk)
        clash = clashes.first()
        if clash is not None:
            raise ValidationError(
                f"{self.employee} already has leave booked from {clash.start_date} to "
                f"{clash.end_date}; the same days cannot be taken off twice."
            )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = LeaveRequest.objects.filter(pk=self.pk).first()
            if previous is not None and previous.status == LeaveStatus.APPROVED:
                changed = (
                    previous.start_date != self.start_date
                    or previous.end_date != self.end_date
                    or previous.half_day != self.half_day
                    or previous.policy_id != self.policy_id
                )
                if changed:
                    # An approval covers the dates somebody actually agreed
                    # to. Moving them afterwards is a different request.
                    raise ValidationError(
                        "This leave has been approved; its dates can no longer change. "
                        "Cancel it and raise another."
                    )
        self.clean()
        super().save(*args, **kwargs)

    # -- the decision, and its reverse ----------------------------------

    def check_approver(self, by):
        """
        Who may decide this.

        Not core.ApprovableMixin: that records an approval against a
        policy threshold an amount has breached, keyed to a login. This
        is a manager's decision about a colleague, always required rather
        than triggered by a limit, and made by an Employee. Bending the
        mixin to fit would leave approval_reasons() saying nothing and
        requires_approval() always true.
        """
        if by is None:
            raise ValidationError("Somebody has to decide this; say who.")
        if by.pk == self.employee_id:
            raise ValidationError(
                f"{self.employee} cannot decide their own leave."
            )

    @transaction.atomic
    def approve(self, by, note=""):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be approved.")
        self.check_approver(by)
        self._check_balance()
        # Freeze what it cost. The working pattern and the public holiday
        # list both change, and what an approved holiday cost does not.
        self.days_taken = self.compute_days()
        self.status = LeaveStatus.APPROVED
        self.decided_by = by
        self.decided_at = timezone.now()
        if note:
            self.reason = note
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "days_taken", "reason", "updated_at",
        ])
        return self

    def _check_balance(self):
        if self.policy_id is None or self.policy.allows_negative:
            return
        wanted = self.compute_days()
        available = leave_balance(
            self.employee, self.policy, self.start_date.year, as_of=self.start_date
        )
        # This request is still pending, so it is already counted against
        # the balance; adding it again would refuse every request that
        # exactly used up the remainder.
        available += wanted if self.is_open() else Decimal("0")
        if wanted > available:
            raise ValidationError(
                f"{self.employee} has {available} day(s) of {self.policy.name} left "
                f"in {self.start_date.year} and this request is {wanted}."
            )

    @transaction.atomic
    def withdraw_approval(self, by=None, note=""):
        """
        Put an approved request back in the queue.

        The mirror of approve(), written with it. Without it an approval
        given by mistake can only be cancelled, which throws away the
        request as well as the decision — and a manager who meant to
        approve next week rather than this one has to ask the employee to
        type it again.
        """
        if self.status != LeaveStatus.APPROVED:
            raise ValidationError("Only an approved leave request can be sent back.")
        self.status = LeaveStatus.PENDING
        self.decided_by = None
        self.decided_at = None
        self.days_taken = None
        if note:
            self.reason = note
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "days_taken", "reason", "updated_at",
        ])
        return self

    @transaction.atomic
    def reject(self, by, reason=None):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be rejected.")
        self.check_approver(by)
        self.status = LeaveStatus.REJECTED
        self.decided_by = by
        self.decided_at = timezone.now()
        if reason:
            self.reason = reason
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "reason", "updated_at",
        ])
        return self

    @transaction.atomic
    def cancel(self, on_date=None):
        """
        Give the days back.

        The first version accepted only pending requests, which made an
        approved holiday permanent: somebody who comes back early, or
        whose plans fall through, had no way to return the days and the
        balance stayed spent. Leave already taken is a different matter —
        those days are gone — so only the future can be given back.
        """
        if self.status in (LeaveStatus.CANCELLED, LeaveStatus.REJECTED):
            raise ValidationError("This leave request is already closed.")
        on_date = to_date(on_date) or timezone.now().date()
        if self.status == LeaveStatus.APPROVED and self.end_date < on_date:
            raise ValidationError(
                f"This leave ended on {self.end_date}; it has been taken and cannot be "
                "cancelled. Adjust the entitlement if it was recorded wrongly."
            )
        self.status = LeaveStatus.CANCELLED
        self.days_taken = None
        super().save(update_fields=["status", "days_taken", "updated_at"])
        return self


def leave_taken(employee, policy, year, statuses=None):
    """
    Days held against an allowance: approved, and pending because a
    request in a queue is a claim on the same days.
    """
    statuses = statuses or (LeaveStatus.PENDING, LeaveStatus.APPROVED)
    total = Decimal("0")
    for request in LeaveRequest.objects.filter(
        employee=employee, policy=policy, status__in=statuses,
        start_date__year=year,
    ):
        total += request.days()
    return total.quantize(Decimal("0.01"))


def leave_balance(employee, policy, year, as_of=None):
    """
    Earned minus spoken for, derived every time.

    Never stored. A running balance drifts the moment a request is
    cancelled, a date is corrected or an entitlement is revised, and the
    only way to find out is an employee counting their own days and
    disagreeing.
    """
    return (
        policy.entitlement_for(employee, year, as_of=as_of)
        - leave_taken(employee, policy, year)
    ).quantize(Decimal("0.01"))


def leave_summary(employee, year, as_of=None):
    """Every allowance this employee has, and where each stands."""
    rows = []
    for policy in LeavePolicy.objects.filter(is_active=True):
        entitled = policy.entitlement_for(employee, year, as_of=as_of)
        taken = leave_taken(employee, policy, year, statuses=(LeaveStatus.APPROVED,))
        booked = leave_taken(employee, policy, year, statuses=(LeaveStatus.PENDING,))
        if not (entitled or taken or booked):
            continue
        rows.append({
            "policy": policy,
            "entitled": entitled,
            "taken": taken,
            "booked": booked,
            "balance": (entitled - taken - booked).quantize(Decimal("0.01")),
        })
    return rows


# Payroll lives in its own module because it is a posting path rather
# than a description of people, but Django only discovers models this
# one pulls in. Last, so Employee and LeaveRequest are fully defined
# before payroll imports them back.
from .timesheets import (  # noqa: E402,F401
    Timesheet,
    TimesheetEntry,
    TimesheetStatus,
    approved_hours,
    hours_by_account,
)
from .payroll import (  # noqa: E402,F401
    ComponentBasis,
    ComponentKind,
    EmployeeCompensation,
    PayComponent,
    PayRun,
    PayRunStatus,
    Payslip,
    PayslipLine,
)
