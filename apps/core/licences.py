"""
The plant's own licences and when each lapses: the factory licence, the
pollution board's consent to operate, the fire NOC, the stamping of each
weighbridge and scale. A lapsed one is a notice or a closure, and nothing
reminded anyone before.

A licence is renewed, not edited forward: the new one is its own record
with its own number and dates, and the old one points to it. Taking the
renewal off (it was entered against the wrong licence) puts the old one
back on the calendar.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import AuditModel, serialised, to_date


class LicenceKind(models.TextChoices):
    FACTORY = "factory", "Factory licence"
    CONSENT = "consent", "Consent to operate (pollution board)"
    FIRE = "fire", "Fire NOC"
    METROLOGY = "metrology", "Weights and measures stamping"
    BOILER = "boiler", "Boiler certificate"
    TRADE = "trade", "Trade licence"
    OTHER = "other", "Other"


class Licence(AuditModel):
    kind = models.CharField(max_length=16, choices=LicenceKind.choices)
    licence_number = models.CharField(max_length=64, help_text="The authority's number, not one of ours.")
    issued_by = models.CharField(max_length=128, blank=True, help_text="The office that issues and renews it.")
    covers = models.CharField(max_length=128, blank=True,
                              help_text="What it is for, where the plant holds several: \"Weighbridge WB-1\".")
    valid_from = models.DateField()
    valid_to = models.DateField()
    remind_days = models.PositiveSmallIntegerField(
        default=60, help_text="How long before it lapses to start renewing: the board's own processing time "
                              "and some.")
    renewed_by = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="renews", editable=False)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["valid_to", "id"]
        constraints = [
            models.CheckConstraint(check=Q(valid_to__gte=F("valid_from")), name="licence_ends_after_it_starts"),
            # The factory licence keeps its number from one year to the next, so the number
            # alone is not the record: the same issue entered twice is.
            models.UniqueConstraint(fields=["kind", "licence_number", "valid_from"], name="licence_issued_once"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.licence_number}"

    def renew_from(self):
        """The day to start renewing it."""
        return self.valid_to - datetime.timedelta(days=self.remind_days)

    def status(self, on=None):
        on = on or timezone.localdate()
        if self.renewed_by_id:
            return "renewed"
        if self.valid_to < on:
            return "lapsed"
        return "due" if self.renew_from() <= on else "valid"

    @serialised("renewed_by")
    def renew(self, number, valid_from, valid_to, note=""):
        if self.renewed_by_id:
            raise ValidationError(f"{self} was renewed by {self.renewed_by}; renew that one.")
        valid_from = to_date(valid_from)
        if valid_from <= self.valid_from:
            raise ValidationError({"valid_from": f"A renewal starts after {self.valid_from}, when this one began."})
        with transaction.atomic():
            renewal = Licence.objects.create(kind=self.kind, licence_number=number, issued_by=self.issued_by,
                                             covers=self.covers, valid_from=valid_from, valid_to=to_date(valid_to),
                                             remind_days=self.remind_days, note=note)
            self.renewed_by = renewal
            super().save(update_fields=["renewed_by"])
        return renewal


def licences_due(on=None):
    """Every licence not yet renewed whose renewal should have started by `on`, soonest to lapse first."""
    on = on or timezone.localdate()
    return [licence for licence in Licence.objects.filter(renewed_by__isnull=True) if licence.renew_from() <= on]
