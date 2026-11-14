"""
Recruitment: openings and the people who apply for them.

An opening says what is wanted and how many; an applicant moves through
screening, interview and offer to hired or rejected, and hiring makes
the employee record, which is the day the person stops being an
applicant and starts being a person on the rolls. An opening is filled
when as many are hired as it asked for.
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, Party, PartyRole, PartyRoleAssignment, serialised, to_date

from .models import Employee


class OpeningStatus(models.TextChoices):
    OPEN = "open", "Open"
    ON_HOLD = "on_hold", "On hold"
    FILLED = "filled", "Filled"
    CLOSED = "closed", "Closed"


class Stage(models.TextChoices):
    APPLIED = "applied", "Applied"
    SCREENING = "screening", "Screening"
    INTERVIEW = "interview", "Interview"
    OFFERED = "offered", "Offered"
    HIRED = "hired", "Hired"
    REJECTED = "rejected", "Rejected"


FORWARD = [Stage.APPLIED, Stage.SCREENING, Stage.INTERVIEW, Stage.OFFERED, Stage.HIRED]


class Source(models.TextChoices):
    WALK_IN = "walk_in", "Walk-in"
    REFERRAL = "referral", "Referral"
    AGENCY = "agency", "Agency"
    PORTAL = "portal", "Job portal"
    OTHER = "other", "Other"


def _text(value):
    return " ".join((value or "").split())


class JobOpening(AuditModel):
    title = models.CharField(max_length=255)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="openings")
    openings = models.PositiveSmallIntegerField(default=1, help_text="How many people are wanted.")
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=OpeningStatus.choices, default=OpeningStatus.OPEN)
    opened_on = models.DateField()
    closed_on = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-opened_on", "-id"]
        constraints = [models.CheckConstraint(check=Q(openings__gte=1), name="opening_wants_someone")]

    def __str__(self):
        return self.title

    def hired(self):
        return self.applicants.filter(stage=Stage.HIRED).count()

    def is_open(self):
        return self.status == OpeningStatus.OPEN

    def save(self, *args, **kwargs):
        self.title = _text(self.title)
        if not self.title:
            raise ValidationError({"title": ["Say what the job is."]})
        if not self.openings or self.openings < 1:
            raise ValidationError({"openings": ["An opening is for one person or more."]})
        self.opened_on = to_date(self.opened_on) or timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.applicants.exists():
            raise ValidationError(f"{self} has applicants; close it instead.")
        return super().delete(*args, **kwargs)

    @serialised("status")
    def hold(self):
        if self.status != OpeningStatus.OPEN:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        self.status = OpeningStatus.ON_HOLD
        self.save(update_fields=["status", "updated_at"])

    @serialised("status")
    def reopen(self):
        if self.status not in (OpeningStatus.ON_HOLD, OpeningStatus.CLOSED):
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        self.status = OpeningStatus.OPEN
        self.closed_on = None
        self.save(update_fields=["status", "closed_on", "updated_at"])

    @serialised("status")
    def close(self, on_date=None):
        """Not wanted after all, or filled another way; the applicants still waiting are told so."""
        if self.status in (OpeningStatus.CLOSED, OpeningStatus.FILLED):
            raise ValidationError(f"{self} is {self.get_status_display().lower()}.")
        self.status, self.closed_on = OpeningStatus.CLOSED, to_date(on_date) or timezone.localdate()
        self.save(update_fields=["status", "closed_on", "updated_at"])

    def _settle(self):
        """Filled once as many are hired as were wanted; open again if a hire is undone."""
        if self.status in (OpeningStatus.OPEN, OpeningStatus.FILLED):
            filled = self.hired() >= self.openings
            status = OpeningStatus.FILLED if filled else OpeningStatus.OPEN
            if status != self.status:
                self.status = status
                self.closed_on = timezone.localdate() if filled else None
                self.save(update_fields=["status", "closed_on", "updated_at"])


class Applicant(AuditModel):
    opening = models.ForeignKey(JobOpening, on_delete=models.PROTECT, related_name="applicants")
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    applied_on = models.DateField()
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.WALK_IN)
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.APPLIED)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Out of five, after the interview.")
    expected_pay = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, help_text="A month.")
    notes = models.TextField(blank=True)
    rejected_reason = models.CharField(max_length=255, blank=True, editable=False)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["opening", "applied_on", "id"]
        constraints = [
            models.CheckConstraint(check=Q(rating__isnull=True) | (Q(rating__gte=1) & Q(rating__lte=5)),
                                   name="applicant_rating_out_of_five"),
        ]

    def __str__(self):
        return f"{self.name} for {self.opening}"

    def is_open(self):
        return self.stage not in (Stage.HIRED, Stage.REJECTED)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_moving", False):
            stored = type(self).objects.filter(pk=self.pk).values_list("stage", flat=True).first()
            if stored in (Stage.HIRED, Stage.REJECTED):
                raise ValidationError(f"{self} is {stored}; the record stands.")
        self.name = _text(self.name)
        if not self.name:
            raise ValidationError({"name": ["Say who applied."]})
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise ValidationError({"rating": ["A rating is 1 to 5."]})
        self.applied_on = to_date(self.applied_on) or timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.stage}; the record stands.")
        return super().delete(*args, **kwargs)

    def _move(self, fields):
        self._moving = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._moving = False

    @serialised("stage")
    def advance(self, stage):
        """Forward only, one step or more, and never past the offer: hiring makes the employee."""
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}.")
        if stage not in FORWARD or stage == Stage.HIRED:
            raise ValidationError({"stage": ["Move to screening, interview or offered; hiring is its own step."]})
        if FORWARD.index(stage) <= FORWARD.index(self.stage):
            raise ValidationError({"stage": [f"{self.name} is already at {self.get_stage_display().lower()}; applicants move forward."]})
        if not self.opening.is_open():
            raise ValidationError(f"{self.opening} is {self.opening.get_status_display().lower()}; reopen it first.")
        self.stage = stage
        self._move(["stage"])

    @serialised("stage")
    def reject(self, reason):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.get_stage_display().lower()}.")
        reason = _text(reason)
        if not reason:
            raise ValidationError({"reason": ["Say why, for the next time they apply."]})
        self.stage, self.rejected_reason = Stage.REJECTED, reason[:255]
        self._move(["stage", "rejected_reason"])

    @serialised("stage")
    def hire(self, employee_number, hire_date=None, job_title=""):
        """
        The day they stop being an applicant: a party and an employee
        record are made with the number given, in the opening's
        department, and the opening is filled if this was the last one
        wanted.
        """
        if self.stage != Stage.OFFERED:
            raise ValidationError(f"{self.name} is at {self.get_stage_display().lower()}; an offer is made and accepted first.")
        if not self.opening.is_open():
            raise ValidationError(f"{self.opening} is {self.opening.get_status_display().lower()}; reopen it first.")
        employee_number = _text(employee_number)
        if not employee_number:
            raise ValidationError({"employee_number": ["Give the new employee their number."]})
        if Employee.objects.filter(employee_number=employee_number).exists() or Party.objects.filter(code=employee_number).exists():
            raise ValidationError({"employee_number": [f"{employee_number} is taken."]})
        with transaction.atomic():
            party = Party.objects.create(code=employee_number, name=self.name, email=self.email, phone=self.phone)
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
            self.employee = Employee.objects.create(
                party=party, employee_number=employee_number, department=self.opening.department,
                hire_date=to_date(hire_date) or timezone.localdate(), job_title=_text(job_title) or self.opening.title)
            self.stage = Stage.HIRED
            self._move(["stage", "employee"])
            self.opening._settle()
        return self.employee
