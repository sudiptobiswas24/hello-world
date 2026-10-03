"""
A customer's complaint, the batches it is about, and what is done so it
does not happen again.

**Linked to the batches shipped.** A complaint names the batches the
customer says are wrong, and a batch is accepted only if it was shipped
to that customer: a complaint about somebody else's sacks is a
mis-read label, and chasing its genealogy chases the wrong polymer.

**Investigated through the genealogy that already exists**: what each
batch was made from, back to the polymer, and who else holds any of the
same batches — the containment question, which is the one with a
deadline.

**Corrective and preventive action** is a list of actions, each with an
owner and a date. Containment is done or it is not; a corrective or a
preventive action is also checked afterwards for whether it worked, and
a complaint is closed only when every action is done and every
corrective and preventive one has been verified — and only with the
root cause written down. A complaint that turns out not to be ours is
rejected, with the reason.

Closed is not deleted: a customer who comes back reopens it, with why,
and the actions and their history stay on it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, to_date

ZERO = Decimal("0")


class ComplaintCategory(models.TextChoices):
    WEIGHT = "weight", "Weight or size"
    STRENGTH = "strength", "Strength or bursting"
    SEAM = "seam", "Stitching or seam"
    PRINT = "print", "Print"
    LAMINATION = "lamination", "Lamination or coating"
    CONTAMINATION = "contamination", "Contamination"
    COUNT = "count", "Count short"
    OTHER = "other", "Other"


class ComplaintStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    REJECTED = "rejected", "Rejected"


class ActionKind(models.TextChoices):
    CONTAINMENT = "containment", "Containment"
    CORRECTIVE = "corrective", "Corrective"
    PREVENTIVE = "preventive", "Preventive"


def _text(value):
    return " ".join((value or "").split())


class Complaint(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    customer = models.ForeignKey("core.Party", on_delete=models.PROTECT,
                                 related_name="complaints")
    received_on = models.DateField()
    category = models.CharField(max_length=16, choices=ComplaintCategory.choices)
    description = models.TextField()
    quantity_affected = models.DecimalField(max_digits=18, decimal_places=4, null=True,
                                            blank=True)
    status = models.CharField(max_length=12, choices=ComplaintStatus.choices,
                              default=ComplaintStatus.OPEN, editable=False)
    root_cause = models.TextField(blank=True, editable=False)
    decided_on = models.DateField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+", editable=False)
    rejection_reason = models.CharField(max_length=255, blank=True, editable=False)
    reopened_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-received_on", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~models.Q(number=""),
                                    name="complaint_number_unique"),
            models.CheckConstraint(
                check=models.Q(quantity_affected__isnull=True)
                | models.Q(quantity_affected__gt=0),
                name="complaint_quantity_positive"),
        ]

    def __str__(self):
        return self.number or f"Complaint {self.pk}"

    def is_open(self):
        return self.status == ComplaintStatus.OPEN

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_deciding", False):
            stored = type(self).objects.filter(pk=self.pk).values("status").first()
            if stored and stored["status"] != ComplaintStatus.OPEN:
                raise ValidationError(f"{self} is {stored['status']}; reopen it to change it.")
        if not _text(self.description):
            raise ValidationError("Say what the customer complains of.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "manufacturing.complaint", to_date(self.received_on),
                name="Complaints", prefix="CMP-")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self} is a record of what a customer said; reject it.")

    def _decide(self, fields):
        self._deciding = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._deciding = False

    # -- the batches --------------------------------------------------

    def add_lot(self, lot, quantity=None):
        return ComplaintLot.objects.create(complaint=self, lot=lot,
                                           quantity=None if quantity is None
                                           else Decimal(str(quantity)))

    def lots(self):
        return [row.lot for row in self.complained_lots.select_related("lot__item")]

    def investigation(self, depth=4):
        """What each batch was made from, and who else holds the same batches."""
        from .demand import genealogy
        from .trace import held_by_customers

        lots = self.lots()
        return {
            "made_from": {lot.code: genealogy(lot, depth) for lot in lots},
            "also_held_by": [row for row in held_by_customers(lots)
                             if row["customer"].pk != self.customer_id],
        }

    # -- deciding -----------------------------------------------------

    @transaction.atomic
    def close(self, root_cause, by, on_date=None):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.status}.")
        root_cause = _text(root_cause)
        if not root_cause:
            raise ValidationError("Write down the root cause before closing.")
        if not self.complained_lots.exists():
            raise ValidationError(f"{self} names no batch; a complaint closed against "
                                  "nothing cannot be traced.")
        actions = list(self.actions.all())
        if not any(action.kind != ActionKind.CONTAINMENT for action in actions):
            raise ValidationError(f"{self} has no corrective or preventive action.")
        for action in actions:
            if action.done_on is None:
                raise ValidationError(f"{action} is not done.")
            if action.kind != ActionKind.CONTAINMENT and action.verified_on is None:
                raise ValidationError(f"{action} has not been checked for whether it worked.")
        self.status, self.root_cause = ComplaintStatus.CLOSED, root_cause
        self.decided_on, self.decided_by = to_date(on_date) or timezone.localdate(), by
        self._decide(["status", "root_cause", "decided_on", "decided_by"])

    @transaction.atomic
    def reject(self, reason, by, on_date=None):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.status}.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why the complaint is not ours.")
        self.status, self.rejection_reason = ComplaintStatus.REJECTED, reason[:255]
        self.decided_on, self.decided_by = to_date(on_date) or timezone.localdate(), by
        self._decide(["status", "rejection_reason", "decided_on", "decided_by"])

    @transaction.atomic
    def reopen(self, reason):
        if self.is_open():
            raise ValidationError(f"{self} is open.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why it is reopened.")
        self.status, self.reopened_reason = ComplaintStatus.OPEN, reason[:255]
        self.decided_on = self.decided_by = None
        self._decide(["status", "reopened_reason", "decided_on", "decided_by"])


class ComplaintLot(AuditModel):
    complaint = models.ForeignKey(Complaint, on_delete=models.PROTECT,
                                  related_name="complained_lots")
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        ordering = ["complaint", "id"]
        constraints = [
            models.UniqueConstraint(fields=["complaint", "lot"],
                                    name="complaint_names_a_lot_once"),
        ]

    def __str__(self):
        return f"{self.lot} on {self.complaint}"

    def _check_open(self, complaint):
        if not complaint.is_open():
            raise ValidationError(f"{complaint} is {complaint.status}; reopen it to change it.")

    def save(self, *args, **kwargs):
        from apps.sales.models import DeliveryAllocation

        complaint = Complaint.objects.get(pk=self.complaint_id)
        self._check_open(complaint)
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("A quantity complained of is more than nothing.")
        # Returned in full still counts: the customer had it, and the
        # complaint is often why it came back.
        shipped = DeliveryAllocation.objects.filter(
            lot=self.lot, line__delivery__sales_order__customer_id=complaint.customer_id,
        ).exists()
        if not shipped:
            raise ValidationError(f"{self.lot} was never shipped to {complaint.customer}.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_open(self.complaint)
        return super().delete(*args, **kwargs)


class CorrectiveAction(AuditModel):
    complaint = models.ForeignKey(Complaint, on_delete=models.PROTECT, related_name="actions")
    kind = models.CharField(max_length=12, choices=ActionKind.choices)
    description = models.CharField(max_length=255)
    owner = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="+")
    due_on = models.DateField()
    done_on = models.DateField(null=True, blank=True, editable=False)
    done_note = models.CharField(max_length=255, blank=True, editable=False)
    verified_on = models.DateField(null=True, blank=True, editable=False)
    verified_by = models.ForeignKey("hr.Employee", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["complaint", "due_on", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} on {self.complaint}: {self.description}"

    def save(self, *args, **kwargs):
        complaint = Complaint.objects.get(pk=self.complaint_id)
        if not complaint.is_open():
            raise ValidationError(f"{complaint} is {complaint.status}; reopen it to change it.")
        if not _text(self.description):
            raise ValidationError("Say what the action is.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.done_on is not None:
            raise ValidationError(f"{self} is done; it is part of the record.")
        complaint = Complaint.objects.get(pk=self.complaint_id)
        if not complaint.is_open():
            raise ValidationError(f"{complaint} is {complaint.status}; reopen it to change it.")
        return super().delete(*args, **kwargs)

    def done(self, note, on_date=None):
        if self.done_on is not None:
            raise ValidationError(f"{self} was done on {self.done_on}.")
        note = _text(note)
        if not note:
            raise ValidationError("Say what was done.")
        self.done_on, self.done_note = to_date(on_date) or timezone.localdate(), note[:255]
        self.save(update_fields=["done_on", "done_note", "updated_at"])

    def verify(self, by, on_date=None):
        """It was checked afterwards and it worked — by somebody who did not do it."""
        if self.kind == ActionKind.CONTAINMENT:
            raise ValidationError("Containment is done or it is not; there is nothing to "
                                  "verify afterwards.")
        if self.done_on is None:
            raise ValidationError(f"{self} is not done yet.")
        if self.verified_on is not None:
            raise ValidationError(f"{self} was verified on {self.verified_on}.")
        if by.pk == self.owner_id:
            raise ValidationError("Whether it worked is checked by somebody other than "
                                  "its owner.")
        on_date = to_date(on_date) or timezone.localdate()
        if on_date < self.done_on:
            raise ValidationError("It is checked after it was done.")
        self.verified_on, self.verified_by = on_date, by
        self.save(update_fields=["verified_on", "verified_by", "updated_at"])


def overdue_actions(on_date=None):
    """Actions not done by their date, on complaints still open."""
    on_date = to_date(on_date) or timezone.localdate()
    return list(CorrectiveAction.objects.filter(
        done_on__isnull=True, due_on__lt=on_date, complaint__status=ComplaintStatus.OPEN,
    ).select_related("complaint", "owner__party").order_by("due_on", "id"))


def complaints_by(start, end, field="category"):
    """How many complaints of each category (or customer) were received in a window."""
    from collections import Counter

    rows = Complaint.objects.filter(received_on__gte=to_date(start),
                                    received_on__lte=to_date(end))
    if field == "customer":
        counts = Counter(row.customer.code for row in rows.select_related("customer"))
    elif field == "category":
        counts = Counter(row.category for row in rows)
    else:
        raise ValidationError("Count by category or by customer.")
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
