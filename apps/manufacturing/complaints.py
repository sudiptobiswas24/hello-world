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
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, lock_rows, serialised, to_date

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

    def _check_not_paid_and_refused(self, status, settled):
        """
        A complaint is not both rejected and paid for.

        Asked by settle() and by reject() alike, each with the state it
        would leave, so neither can be written without the other: settle()
        refused a rejected complaint while reject() never asked about a
        settled one, and a complaint read "not ours" still said it had cost
        500.00. Each holds the complaint first.
        """
        if status != ComplaintStatus.REJECTED or not settled:
            return
        if self.status == ComplaintStatus.REJECTED:
            raise ValidationError("A rejected complaint is not paid for; reopen it if it was upheld after all.")
        notes = ", ".join(row.credit_note.number for row in self.settlements.select_related("credit_note"))
        raise ValidationError(f"{self} was settled by {notes}: a complaint paid for was upheld. It "
                              "cannot be rejected while that credit stands.")

    # -- the batches --------------------------------------------------

    @serialised("status")
    def settle(self, invoice, net, reason, memo=""):
        """Money given back for this complaint, by a claim credit note on the customer's invoice."""
        self._check_not_paid_and_refused(self.status, settled=True)
        if invoice.customer_id != self.customer_id:
            raise ValidationError(f"{invoice.number} is {invoice.customer}'s, not {self.customer}'s.")
        with transaction.atomic():
            note = invoice.credit_claim(net, reason, memo=memo or f"Complaint {self.number}")
            ComplaintSettlement.objects.create(complaint=self, credit_note=note)
        return note

    def cost(self):
        """What settling it has given back, before tax."""
        return sum((row.credit_note.subtotal() for row in self.settlements.all()), Decimal("0"))

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

        from .tape_settings import TapeRunSetting

        lots = self.lots()
        made_from = {lot.code: genealogy(lot, depth) for lot in lots}
        runs = {row["made_by"].pk for rows in made_from.values() for row in rows}
        return {
            "made_from": made_from,
            "also_held_by": [row for row in held_by_customers(lots)
                             if row["customer"].pk != self.customer_id],
            # What the tape lines were set to on the runs that made them.
            "tape_settings": list(TapeRunSetting.objects.filter(work_order_id__in=runs).select_related(
                "work_order", "machine")),
        }

    # -- deciding -----------------------------------------------------

    @serialised("status")
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

    @serialised("status")
    def reject(self, reason, by, on_date=None):
        if not self.is_open():
            raise ValidationError(f"{self} is {self.status}.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why the complaint is not ours.")
        self._check_not_paid_and_refused(ComplaintStatus.REJECTED, settled=self.settlements.exists())
        self.status, self.rejection_reason = ComplaintStatus.REJECTED, reason[:255]
        self.decided_on, self.decided_by = to_date(on_date) or timezone.localdate(), by
        self._decide(["status", "rejection_reason", "decided_on", "decided_by"])

    @serialised("status")
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
    """An action on a complaint or on a quality alert, one or the other: done by its owner, then checked by somebody else."""

    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.PROTECT, related_name="actions")
    alert = models.ForeignKey("manufacturing.QualityAlert", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="actions")
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
        ordering = ["complaint", "alert", "due_on", "id"]
        constraints = [
            models.CheckConstraint(
                check=(Q(complaint__isnull=False, alert__isnull=True) | Q(complaint__isnull=True, alert__isnull=False)),
                name="corrective_action_on_a_complaint_or_an_alert"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} on {self.parent()}: {self.description}"

    def parent(self):
        """The complaint or the alert this action is on, read afresh: its state decides what may change."""
        if self.complaint_id:
            return Complaint.objects.get(pk=self.complaint_id)
        from .alerts import QualityAlert

        return QualityAlert.objects.get(pk=self.alert_id)

    def _check_parent(self):
        """
        The complaint or alert is open, asked with it held.

        close() holds it while it counts the actions; an action added at
        the same moment was written after the count, and the complaint
        closed with an action not done. Held here too, the one waits for
        the other and then sees what it did. The action's own row, where a
        step holds it, is taken first, as done() and an edit over the API
        take it; nothing takes the complaint and then an action.
        """
        if bool(self.complaint_id) == bool(self.alert_id):
            raise ValidationError("An action is on a complaint or on a quality alert, one or the other.")
        parent = self.parent()
        lock_rows(parent)
        if not parent.is_open():
            raise ValidationError(f"{parent} is {parent.status}; reopen it to change it.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._check_parent()
        if not _text(self.description):
            raise ValidationError("Say what the action is.")
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        if self.done_on is not None:
            raise ValidationError(f"{self} is done; it is part of the record.")
        self._check_parent()
        return super().delete(*args, **kwargs)

    @serialised("done_on")
    def done(self, note, on_date=None):
        if self.done_on is not None:
            raise ValidationError(f"{self} was done on {self.done_on}.")
        note = _text(note)
        if not note:
            raise ValidationError("Say what was done.")
        self.done_on, self.done_note = to_date(on_date) or timezone.localdate(), note[:255]
        self.save(update_fields=["done_on", "done_note", "updated_at"])

    @serialised("done_on", "verified_on")
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
    """Actions not done by their date, on complaints and alerts still open."""
    on_date = to_date(on_date) or timezone.localdate()
    return list(CorrectiveAction.objects.filter(
        Q(complaint__status=ComplaintStatus.OPEN) | Q(alert__status="open"),
        done_on__isnull=True, due_on__lt=on_date,
    ).select_related("complaint", "alert", "owner__party").order_by("due_on", "id"))


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


class ComplaintSettlement(AuditModel):
    """
    A credit note that settled a complaint, so a complaint can say what it
    cost and a claim which complaint it answered.
    """

    complaint = models.ForeignKey(Complaint, on_delete=models.PROTECT, related_name="settlements")
    credit_note = models.OneToOneField("sales.Invoice", on_delete=models.PROTECT,
                                       related_name="complaint_settlement", editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.credit_note} for {self.complaint}"
