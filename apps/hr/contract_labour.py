"""
The registers of contractors and of the workers they bring (Contract
Labour Act, Forms XII and XIII): who supplies labour to the plant, under
what licence and for how many, and who worked here through them.

A register is a record the inspector reads, so nothing in it is deleted:
a worker leaves, a contractor stops.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, lock_rows, to_date


class LabourContractor(AuditModel):
    party = models.OneToOneField("core.Party", on_delete=models.PROTECT, related_name="labour_contractor")
    licence_number = models.CharField(max_length=32)
    licence_valid_to = models.DateField()
    work_nature = models.CharField(max_length=128, help_text="Loom operation, bag stitching, loading")
    max_workers = models.PositiveIntegerField(help_text="As many as the licence allows at once.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["party__name"]
        constraints = [models.CheckConstraint(check=Q(max_workers__gt=0), name="contractor_licence_covers_someone")]

    def __str__(self):
        return f"{self.party.name} ({self.licence_number})"

    def save(self, *args, **kwargs):
        if self._state.adding:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            lock_rows(self, refresh=False)  # keep the edit in hand; the workers are what is read after the lock
            # O362: a licence cut below the workers already on it from today on is refused, as a worker
            # joining past the count is: the same count (most_at_once) answers both.
            today = timezone.localdate()
            busiest, on = most_at_once(self, today)
            if busiest > self.max_workers:
                raise ValidationError({"max_workers": f"{busiest} workers are on {self}'s register on "
                                                      f"{on:%d %b %Y}; give the day some of them leave first."})
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A contractor stays in the register; mark it no longer active.")

    def working_on(self, day):
        day = to_date(day)
        return self.workers.filter(joined_on__lte=day).filter(Q(left_on__isnull=True) | Q(left_on__gte=day))


def most_at_once(contractor, start, end=None, exclude=None):
    """
    The most workers on a contractor's register at once between start and end (open: from start on), and the
    day it comes. The count only rises on a day someone joins, so those days and start are all it reads.
    """
    workers = contractor.workers.exclude(pk=exclude) if exclude else contractor.workers.all()
    joins = workers.filter(joined_on__gt=start)
    if end:
        joins = joins.filter(joined_on__lte=end)
    busiest, on = 0, start
    for day in sorted({start, *joins.values_list("joined_on", flat=True)}):
        here = contractor.working_on(day).exclude(pk=exclude).count()
        if here > busiest:
            busiest, on = here, day
    return busiest, on


class Gender(models.TextChoices):
    FEMALE = "female", "Female"
    MALE = "male", "Male"
    OTHER = "other", "Other"


class ContractWorker(AuditModel):
    contractor = models.ForeignKey(LabourContractor, on_delete=models.PROTECT, related_name="workers")
    name = models.CharField(max_length=128)
    gender = models.CharField(max_length=8, choices=Gender.choices)
    designation = models.CharField(max_length=64, blank=True)
    daily_wage = models.DecimalField(max_digits=10, decimal_places=2)
    joined_on = models.DateField()
    left_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["contractor", "name", "id"]
        constraints = [
            models.CheckConstraint(check=Q(daily_wage__gt=0), name="contract_wage_positive"),
            models.CheckConstraint(check=Q(left_on__isnull=True) | Q(left_on__gte=models.F("joined_on")),
                                   name="contract_worker_leaves_after_joining"),
        ]

    def __str__(self):
        return f"{self.name} through {self.contractor.party.name}"

    def locked_before_it(self):
        """The contractor, as save() takes it before the worker: an edit through the API holds it first (O37)."""
        return [self.contractor] if self.contractor_id else []

    def save(self, *args, **kwargs):
        self.joined_on, self.left_on = to_date(self.joined_on), to_date(self.left_on)
        if self.left_on and self.left_on < self.joined_on:
            raise ValidationError({"left_on": "A worker leaves after joining."})
        with transaction.atomic():
            contractor = self.contractor
            lock_rows(contractor)
            stored = None if self._state.adding else ContractWorker.objects.filter(pk=self.pk).values(
                "contractor_id", "left_on").first()
            joins = stored is None or stored["contractor_id"] != self.contractor_id or (
                stored["left_on"] is not None and self.left_on is None)
            if joins and not contractor.is_active:
                # O309: a contractor who has stopped (licence surrendered) brings nobody new: not a new worker, not
                # one moved to it, not one who had left brought back (review 4: the edit paths). A worker already
                # working through it is still edited, to give the day they left.
                raise ValidationError({"contractor": [f"{contractor} is no longer active; a new worker comes "
                                                      "through an active contractor."]})
            if self.joined_on > contractor.licence_valid_to:
                raise ValidationError({"joined_on": f"{contractor}'s licence ran out on "
                                                    f"{contractor.licence_valid_to:%d %b %Y}."})
            # The most at once over this worker's time, the count a cut licence is held to as well (O362).
            others, day = most_at_once(contractor, self.joined_on, self.left_on, exclude=self.pk)
            if others >= contractor.max_workers:
                raise ValidationError({"joined_on": f"{contractor}'s licence covers {contractor.max_workers} at "
                                                    f"once, and {others} others are working on {day:%d %b %Y}."})
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A worker stays in the register; give the day they left.")


def licences_due(within_days=30, on_date=None):
    """Active contractors whose licence runs out within `within_days`, or has."""
    today = to_date(on_date) or timezone.localdate()
    limit = today + datetime.timedelta(days=within_days)
    return LabourContractor.objects.filter(is_active=True, licence_valid_to__lte=limit).select_related(
        "party").order_by("licence_valid_to")
