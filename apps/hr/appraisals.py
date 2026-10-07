"""
Appraisals: a reviewer's reading of a person over a span, acknowledged
by the person. Written by the reviewer as a draft, submitted with a
rating out of five, and acknowledged by the person with a comment of
their own; submitted, the reviewer's words do not change, and the
person's acknowledgement is theirs alone. One per person per span.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, serialised, to_date

from .models import Employee


class AppraisalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"


class Appraisal(AuditModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="appraisals")
    reviewer = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="appraisals_given")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=AppraisalStatus.choices, default=AppraisalStatus.DRAFT)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Out of five.")
    strengths = models.TextField(blank=True)
    improvements = models.TextField(blank=True)
    goals = models.TextField(blank=True, help_text="For the next span.")
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    employee_comment = models.TextField(blank=True, editable=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-period_start", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "period_start"], name="one_appraisal_per_employee_per_start"),
            models.CheckConstraint(check=Q(rating__isnull=True) | (Q(rating__gte=1) & Q(rating__lte=5)),
                                   name="appraisal_rating_out_of_five"),
            models.CheckConstraint(check=Q(period_end__gte=models.F("period_start")), name="appraisal_period_in_order"),
        ]
        permissions = [
            ("view_every_appraisal", "Can read everyone's appraisals"),
            ("acknowledge_appraisal", "Can acknowledge one's own appraisal"),
        ]

    def __str__(self):
        return f"Appraisal of {self.employee}, {self.period_start:%b %Y} to {self.period_end:%b %Y}"

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_moving", False):
            stored = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored != AppraisalStatus.DRAFT:
                raise ValidationError(f"{self} is {stored}; the reviewer's words stand once submitted.")
        self.period_start, self.period_end = to_date(self.period_start), to_date(self.period_end)
        if self.period_start is None or self.period_end is None:
            raise ValidationError({"period_start": ["Say the span it covers."]})
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": ["The span ends before it starts."]})
        if self.reviewer_id == self.employee_id:
            raise ValidationError({"reviewer": ["Nobody appraises themselves."]})
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise ValidationError({"rating": ["A rating is 1 to 5."]})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != AppraisalStatus.DRAFT:
            raise ValidationError(f"{self} was submitted; it is part of the record.")
        return super().delete(*args, **kwargs)

    def _move(self, fields):
        self._moving = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._moving = False

    @serialised("status")
    def submit(self):
        if self.status != AppraisalStatus.DRAFT:
            raise ValidationError(f"{self} is already {self.get_status_display().lower()}.")
        if self.rating is None:
            raise ValidationError({"rating": ["Rate it out of five before submitting."]})
        self.status, self.submitted_at = AppraisalStatus.SUBMITTED, timezone.now()
        self._move(["status", "submitted_at"])

    @serialised("status")
    def acknowledge(self, comment=""):
        """The person has read it; what they say about it is theirs and is kept with it."""
        if self.status != AppraisalStatus.SUBMITTED:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}; only a submitted appraisal is acknowledged.")
        self.status, self.acknowledged_at = AppraisalStatus.ACKNOWLEDGED, timezone.now()
        self.employee_comment = " ".join((comment or "").split())
        self._move(["status", "acknowledged_at", "employee_comment"])
