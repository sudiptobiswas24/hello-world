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
import hashlib
import hmac
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Extensible, AuditModel, Party, PartyRole, lock_rows, serialised, to_date

from .calendars import (  # noqa: F401
    DEFAULT_WORKING_DAYS,
    PublicHoliday,
    completed_months,
    parse_working_days,
    working_days,
)


def reversal_day(on_date, made_on, what):
    """
    The day a correction of something done on `made_on` is dated: the day
    asked, or today. Not before it was done, which takes it back in a period
    it was never in, and not ahead of today, which has the document read
    undone while the ledger still holds it. A payment's void asks the same.
    Dated 1 May, June's pay run took June's wages out of May.

    `what` says what was done, for the refusal: "This run was paid".
    """
    day = to_date(on_date) or timezone.localdate()
    made_on = to_date(made_on)
    if made_on is not None and day < made_on:
        raise ValidationError(f"{what} on {made_on}; it is not undone before then.")
    if day > timezone.localdate():
        raise ValidationError(f"{day} has not come yet; nothing is undone ahead of the day it is.")
    return day


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
    centre = models.ForeignKey(
        "accounting.CostCentre", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The cost centre this department's wages are read under in the analytic view "
                  "(accounting/analytic.py); stamped on the payroll's ledger lines when a run posts.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


def pin_digest(pin):
    return hmac.new(
        settings.SECRET_KEY.encode(), f"station-pin:{pin}".encode(), hashlib.sha256
    ).hexdigest()


class Employee(Extensible, AuditModel):
    """
    An Employee is always backed by a core.Party with the EMPLOYEE role —
    HR doesn't invent its own idea of "a person" any more than Sales
    invents its own idea of "a customer".
    """

    party = models.OneToOneField(Party, on_delete=models.PROTECT, related_name="employee_profile")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="employee",
        help_text="The login this person signs in with. Decisions they take in the "
                  "office application (approving a colleague's leave) are taken as "
                  "this employee, and they read their own requests and their reports'.",
    )
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
    paid_by_attendance = models.BooleanField(
        default=False,
        help_text="A day-rated worker: paid for the days the attendance register shows and no "
                  "other, so a pay run waits until every working day of theirs is marked.",
    )
    pin_digest = models.CharField(
        max_length=64, null=True, blank=True, unique=True, editable=False,
        help_text="A keyed digest of the person's shop-floor PIN, never the PIN. "
                  "Unique, because a station knows who you are from the PIN alone.",
    )
    uan = models.CharField(
        max_length=12, blank=True,
        help_text="The provident fund's Universal Account Number, twelve digits: what the "
                  "monthly ECR file names the person by.",
    )
    esi_number = models.CharField(
        max_length=17, blank=True,
        help_text="The ESI insurance number (IP number), ten digits: what the monthly "
                  "contribution file names the person by.",
    )

    class Meta:
        ordering = ["employee_number"]

    def __str__(self):
        return f"{self.employee_number} - {self.party.name}"

    def reports(self):
        """
        The pks of everyone this person manages: direct reports, theirs in
        turn, and everyone in a department they manage, however deep. A
        reporting line drawn in a circle stops where it began.
        """
        found, frontier = set(), {self.pk}
        while frontier:
            below = set(Employee.objects.filter(
                Q(manager_id__in=frontier) | Q(department__manager_id__in=frontier)
            ).values_list("pk", flat=True)) - found - {self.pk}
            found |= below
            frontier = below
        return found

    # -- the shop-floor PIN ------------------------------------------------

    def issue_pin(self):
        """
        Give this person a new six-digit PIN and return it, once.

        Issued rather than chosen. A PIN is the whole of a station
        login, so no two people may share one — and a person choosing
        theirs would learn, from the refusal, somebody else's.

        Kept as a keyed digest (HMAC with the site's secret), not a slow
        password hash: a station finds the person from the PIN alone,
        which a salted hash cannot do without trying every employee.
        That makes the digest only as private as SECRET_KEY; the station
        lockout, not the digest, is what stops guessing.
        """
        for _ in range(50):
            pin = f"{secrets.randbelow(10**6):06d}"
            digest = pin_digest(pin)
            if len(set(pin)) > 1 and not Employee.objects.filter(pin_digest=digest).exists():
                self.pin_digest = digest
                super().save(update_fields=["pin_digest", "updated_at"])
                return pin
        raise ValidationError("Could not find a free PIN; ask again.")

    def revoke_pin(self):
        self.pin_digest = None
        super().save(update_fields=["pin_digest", "updated_at"])

    @classmethod
    def by_pin(cls, pin, on_date=None):
        """The person this PIN belongs to, if they work here on `on_date`."""
        pin = str(pin or "").strip()
        person = cls.objects.select_related("party").filter(
            pin_digest=pin_digest(pin)
        ).first()
        if person is None or person.employment_status != EmploymentStatus.ACTIVE:
            return None
        if not person.is_employed_on(on_date or timezone.localdate()):
            return None
        return person

    def is_working_on(self, on_date):
        """Active, and employed on that day: who may sign or approve for the plant."""
        return self.employment_status == EmploymentStatus.ACTIVE and self.is_employed_on(on_date)

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

    def _check_statutory_ids(self):
        if self.uan and not (self.uan.isdigit() and len(self.uan) == 12):
            raise ValidationError({"uan": ["A UAN is twelve digits."]})
        if self.esi_number and not (self.esi_number.isdigit() and len(self.esi_number) in (10, 17)):
            raise ValidationError({"esi_number": ["An ESI number is ten digits (seventeen with its sub-code)."]})

    def clean(self):
        self._check_statutory_ids()
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

    @serialised("termination_date", "employment_status")
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

    def _check_not_paid_on(self):
        """
        A posted pay run paid this person for the days they were employed
        as they stood, and a start or leaving date moved across those days
        restates it: June paid all 22 days, a leaving date of 15 June set
        afterwards left the slip reading 11 days employed, and June's ESI
        file filed 11. Leave, the register, timesheets and rates refuse the
        same under a posted run; the run is voided to change it.
        """
        from .payroll import PayRunStatus, Payslip

        previous = Employee.objects.filter(pk=self.pk).values("hire_date", "termination_date").first() \
            if self.pk else None
        if previous is None:
            return
        moved = []
        before, after = previous["termination_date"], self.termination_date
        if before != after:
            # Employed or not from the day after the earlier of the two, to
            # the later, or for good when either is open.
            first = min(day for day in (before, after) if day is not None) + datetime.timedelta(days=1)
            moved.append(("leaving date", first, max(before, after) if before and after else None))
        before, after = previous["hire_date"], self.hire_date
        if before != after:
            moved.append(("start date", min(before, after), max(before, after) - datetime.timedelta(days=1)))
        for what, first, last in moved:
            paid = Payslip.objects.filter(employee_id=self.pk, run__status=PayRunStatus.POSTED,
                                          run__period_end__gte=first)
            if last is not None:
                paid = paid.filter(run__period_start__lte=last)
            paid = paid.select_related("run").order_by("run__period_start").first()
            if paid is not None:
                raise ValidationError(
                    f"{paid.run} is posted and paid {self} for {paid.run.period_start}..{paid.run.period_end} "
                    f"on the days they were employed then; void the run to change their {what}.")

    def save(self, *args, **kwargs):
        self._check_statutory_ids()
        # In save() and not only clean(), because Django never calls
        # full_clean() for you and nothing here is created through a form.
        # Every rule this class claimed to enforce was decorative until a
        # probe created records the way the rest of the codebase does.
        self.clean()
        self._check_nothing_dangling()
        self._check_not_paid_on()
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
            as_of = to_date(as_of) or timezone.localdate()
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
        permissions = [
            ("decide_leaverequest", "Can approve or reject leave requests"),
            ("view_every_leaverequest", "Can read everyone's leave requests, not only their own and their reports'"),
            ("decide_any_leaverequest", "Can decide anyone's leave, as HR"),
        ]
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
            if previous is not None and previous.employee_id != self.employee_id:
                # Approved for one person, an edit handed it to another, with
                # their first person's manager's yes on it and the days docked
                # from a run that had paid the first.
                raise ValidationError(
                    "Leave is asked for by one person and is not moved to another. Raise theirs, "
                    "and cancel this one."
                )
            if previous is not None and previous.status == LeaveStatus.APPROVED:
                changed = (
                    previous.start_date != self.start_date
                    or previous.end_date != self.end_date
                    or previous.half_day != self.half_day
                    or previous.policy_id != self.policy_id
                    or previous.leave_type != self.leave_type
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

    def delete(self, *args, **kwargs):
        # An approval is undone by cancelling it, which asks whether its days
        # have been taken or paid on. Deleted, approved unpaid leave that a
        # posted run had docked went with nothing asked, and the payslip kept
        # a deduction no leave stood behind.
        stored = LeaveRequest.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if stored == LeaveStatus.APPROVED:
            raise ValidationError(
                "This leave has been approved: cancel it rather than delete it, and what it took "
                "is given back as far as it can be."
            )
        return super().delete(*args, **kwargs)

    # -- the decision, and its reverse ----------------------------------

    def check_approver(self, by, as_hr=False):
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
        # Their manager, a manager above, or their department's: anyone
        # else's yes was recorded as a decision nobody with the standing
        # to make it had made. And HR (as_hr, the view's
        # hr.decide_any_leaverequest), for anyone: the plant's choice,
        # so someone with no manager is never left undecided.
        if as_hr:
            return
        if self.employee_id not in by.reports():
            raise ValidationError(
                f"{by} does not manage {self.employee}: their manager, a manager above "
                "them, or their department's manager decides their leave.")

    @serialised("status")
    def approve(self, by, note="", as_hr=False):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be approved.")
        # The balance is read below; two of this person's requests approved
        # at once must not both find the same days left.
        lock_rows(self.employee)
        self.check_approver(by, as_hr)
        self._check_balance()
        self._check_not_worked()
        self._check_not_paid_on(self.start_date, self.end_date, "approve")
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

    def _check_not_worked(self):
        """
        A day is not both taken off and worked. The register and the
        timesheet each refuse a day of approved leave; asked here as well,
        for the order it usually comes in: the day marked or the hours
        filed, and the leave asked for after. An absence in the register is
        what leave excuses and stands. Half a day off leaves the other half
        to have been worked, so only a full day in the register is against it.
        """
        from .attendance import AttendanceDay, AttendanceStatus, works_on
        from .calendars import holidays_between
        from .timesheets import TimesheetEntry

        holidays = holidays_between(self.start_date, self.end_date, self.employee.holiday_region)
        worked = [AttendanceStatus.PRESENT] + ([] if self.half_day else [AttendanceStatus.HALF_DAY])
        for day in AttendanceDay.objects.filter(employee=self.employee, status__in=worked,
                                                on__range=(self.start_date, self.end_date)).order_by("on"):
            if works_on(self.employee, day.on, holidays):
                raise ValidationError(
                    f"{self.employee} is marked {day.get_status_display().lower()} on {day.on}: a day "
                    "is not both worked and taken off. Correct the register, or ask for the days "
                    "either side of it.")
        if self.half_day:
            return
        for entry in TimesheetEntry.objects.filter(timesheet__employee=self.employee,
                                                   date__range=(self.start_date, self.end_date)).order_by("date"):
            if works_on(self.employee, entry.date, holidays):
                raise ValidationError(
                    f"{self.employee} has {entry.hours} hours on a timesheet for {entry.date}: a day "
                    "is not both worked and taken off. Take them off the timesheet, or ask for the "
                    "days either side of it.")

    def _check_not_paid_on(self, first, last, doing):
        """
        A posted pay run paid on these days as they stood. Unpaid leave came
        off the salary, an absence the leave covers did not, and a day-rated
        worker's day off was paid on the leave's word; changing the leave
        under the run leaves the payslip saying what the records no longer
        do. The register refuses the same inside a posted run, and so does
        a timesheet. Paid leave on a day the run read nothing for changes
        nothing it paid, and is not refused.
        """
        from .attendance import UNPAID, AttendanceDay
        from .payroll import PayRunStatus, Payslip

        read_by_pay = (
            (self.policy_id is not None and not self.policy.is_paid)
            or self.employee.paid_by_attendance
            or AttendanceDay.objects.filter(employee=self.employee, on__range=(first, last),
                                            status__in=list(UNPAID)).exists()
        )
        if not read_by_pay:
            return
        paid = Payslip.objects.filter(employee=self.employee, run__status=PayRunStatus.POSTED,
                                      run__period_start__lte=last, run__period_end__gte=first,
                                      ).select_related("run").first()
        if paid is not None:
            raise ValidationError(
                f"{paid.run} is posted and paid {self.employee} on these days as they stood; "
                f"void the run to {doing} this leave.")

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

    @serialised("status")
    def withdraw_approval(self, by, note="", as_hr=False):
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
        # Whoever may give the decision may take it back; `by` was taken
        # and never asked, so anyone could undo a manager's yes.
        self.check_approver(by, as_hr)
        self._check_not_paid_on(self.start_date, self.end_date, "send back")
        # As cancel() asks: days already taken are not given back. Sent back
        # and then cancelled, a June holiday was given back in October.
        today = timezone.localdate()
        if self.end_date < today:
            raise ValidationError(
                f"This leave ended on {self.end_date}; it has been taken and its approval is not sent "
                "back. Adjust the entitlement if it was recorded wrongly."
            )
        if self.start_date < today:
            raise ValidationError(
                f"This leave began on {self.start_date}, and the days taken since stay taken. Cancel it "
                "from today instead, and the rest is given back."
            )
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

    @serialised("status")
    def reject(self, by, reason=None, as_hr=False):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be rejected.")
        self.check_approver(by, as_hr)
        self.status = LeaveStatus.REJECTED
        self.decided_by = by
        self.decided_at = timezone.now()
        if reason:
            self.reason = reason
        super().save(update_fields=[
            "status", "decided_by", "decided_at", "reason", "updated_at",
        ])
        return self

    @serialised("status")
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
        on_date = to_date(on_date) or timezone.localdate()
        if self.status == LeaveStatus.APPROVED and self.end_date < on_date:
            raise ValidationError(
                f"This leave ended on {self.end_date}; it has been taken and cannot be "
                "cancelled. Adjust the entitlement if it was recorded wrongly."
            )
        if self.status == LeaveStatus.APPROVED:
            # Only the days given back change what a run read.
            self._check_not_paid_on(max(self.start_date, on_date), self.end_date, "cancel")
        if self.status == LeaveStatus.APPROVED and self.start_date < on_date:
            # Under way: back on `on_date`, the days before it were taken. Cancelled whole, the
            # leave gave every day back, the ones already gone with them.
            self.end_date = on_date - datetime.timedelta(days=1)
            self.days_taken = self.compute_days()
            super().save(update_fields=["end_date", "days_taken", "updated_at"])
            return self
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


from .attendance import AttendanceDay  # noqa: E402,F401
from .contract_labour import ContractWorker, LabourContractor  # noqa: E402,F401
from .expenses import ClaimStatus, ExpenseClaim, ExpenseLine  # noqa: E402,F401
from .appraisals import Appraisal, AppraisalStatus  # noqa: E402,F401
from .recruitment import Applicant, JobOpening, OpeningStatus, Source, Stage  # noqa: E402,F401
