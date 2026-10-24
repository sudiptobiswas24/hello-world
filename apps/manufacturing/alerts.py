"""
Quality alerts: what the plant finds wrong before a customer does.

A complaint is what a customer said; an alert is what an inspector or
an operator saw: a roll off GSM, a seam that lets go, a print off
register, a batch to hold. It names where (a work centre, a machine),
what (an item, a batch, a run), how bad, and who is to deal with it.
Corrective actions hang off it as off a complaint, and it is closed
with its root cause once they are done, or cancelled as not a defect.
It is never deleted: a defect found and then forgotten is the one that
reaches the customer.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, serialised, to_date

from .complaints import ActionKind, _text


class AlertSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class AlertStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Not a defect"


class QualityAlert(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    raised_on = models.DateField()
    raised_by = models.ForeignKey("hr.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                  help_text="Who saw it.")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=8, choices=AlertSeverity.choices, default=AlertSeverity.MEDIUM)
    status = models.CharField(max_length=12, choices=AlertStatus.choices, default=AlertStatus.OPEN)
    work_centre = models.ForeignKey("WorkCentre", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="quality_alerts")
    machine = models.ForeignKey("manufacturing.Machine", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="quality_alerts")
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    work_order = models.ForeignKey("WorkOrder", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="quality_alerts")
    quantity_affected = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    owner = models.ForeignKey("hr.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                              help_text="Who is to deal with it.")
    root_cause = models.TextField(blank=True, editable=False)
    decided_on = models.DateField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey("hr.Employee", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", editable=False)
    cancelled_reason = models.CharField(max_length=255, blank=True, editable=False)
    reopened_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-raised_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_quality_alert_number"),
            models.CheckConstraint(check=Q(quantity_affected__isnull=True) | Q(quantity_affected__gte=0),
                                   name="quality_alert_quantity_not_negative"),
        ]

    def __str__(self):
        return self.number or "Quality alert"

    def is_open(self):
        return self.status == AlertStatus.OPEN

    def where(self):
        return self.machine.code if self.machine_id else (self.work_centre.code if self.work_centre_id else "")

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_deciding", False):
            stored = type(self).objects.filter(pk=self.pk).values("status").first()
            if stored and stored["status"] != AlertStatus.OPEN:
                raise ValidationError(f"{self} is {stored['status']}; reopen it to change it.")
        self.title = _text(self.title)
        if not self.title:
            raise ValidationError({"title": ["Say what was found."]})
        if self.machine_id:
            if self.work_centre_id and self.machine.work_centre_id != self.work_centre_id:
                raise ValidationError({"machine": [f"{self.machine} is not in {self.work_centre}."]})
            self.work_centre_id = self.machine.work_centre_id
        if self.quantity_affected is not None and self.quantity_affected < 0:
            raise ValidationError({"quantity_affected": ["How many is nothing or more."]})
        self.raised_on = to_date(self.raised_on) or timezone.localdate()
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.quality_alert", self.raised_on, name="Quality alerts", prefix="QA-")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is a record of what was found; cancel it as not a defect.")

    def _decide(self, fields):
        self._deciding = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._deciding = False

    @serialised("status")
    def close(self, root_cause, by, on_date=None):
        """
        Closed with its root cause once every action on it is done and
        the corrective ones checked. An alert may close with no action at
        all (a roll scrapped and nothing to change); a complaint may not.
        """
        if not self.is_open():
            raise ValidationError(f"{self} is {self.status}.")
        root_cause = _text(root_cause)
        if not root_cause:
            raise ValidationError("Write down the root cause before closing.")
        for action in self.actions.all():
            if action.done_on is None:
                raise ValidationError(f"{action} is not done.")
            if action.kind != ActionKind.CONTAINMENT and action.verified_on is None:
                raise ValidationError(f"{action} has not been checked for whether it worked.")
        self.status, self.root_cause = AlertStatus.CLOSED, root_cause
        self.decided_on, self.decided_by = to_date(on_date) or timezone.localdate(), by
        self._decide(["status", "root_cause", "decided_on", "decided_by"])

    @serialised("status")
    def cancel(self, reason, by, on_date=None):
        """Not a defect after all: said why, and kept."""
        if not self.is_open():
            raise ValidationError(f"{self} is {self.status}.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why it is not a defect.")
        self.status, self.cancelled_reason = AlertStatus.CANCELLED, reason[:255]
        self.decided_on, self.decided_by = to_date(on_date) or timezone.localdate(), by
        self._decide(["status", "cancelled_reason", "decided_on", "decided_by"])

    @serialised("status")
    def reopen(self, reason):
        if self.is_open():
            raise ValidationError(f"{self} is open.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why it is reopened.")
        self.status, self.reopened_reason = AlertStatus.OPEN, reason[:255]
        self.decided_on = self.decided_by = None
        self._decide(["status", "reopened_reason", "decided_on", "decided_by"])
