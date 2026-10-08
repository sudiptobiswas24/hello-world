"""
Expense claims: money a person spent for the company and asks back.

A claim is lines (what, when, how much, against which expense account)
that the person submits, their manager approves or rejects with a
reason, and accounts pays: the payment is one journal, each line's
account debited and the cash or bank account credited, recorded on the
claim and never recomputed. Paid wrongly, it is reversed by a second
journal and the claim stands approved again. A claim is never deleted
once submitted; a draft the person changes their mind about is.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import AuditModel, DocumentSequence, serialised, to_date

from .models import Employee

ZERO = Decimal("0")


class ClaimStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"


def _text(value):
    return " ".join((value or "").split())


class ExpenseClaim(AuditModel):
    number = models.CharField(max_length=32, blank=True, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="expense_claims")
    claim_date = models.DateField()
    purpose = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=ClaimStatus.choices, default=ClaimStatus.DRAFT)
    decided_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decision_note = models.CharField(max_length=255, blank=True, editable=False)
    paid_on = models.DateField(null=True, blank=True, editable=False)
    paid_from = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+", editable=False, help_text="The cash or bank account it was paid from.")
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    voided_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)

    class Meta:
        ordering = ["-claim_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_expense_claim_number"),
        ]
        permissions = [
            ("decide_expenseclaim", "Can approve or reject an expense claim of a report"),
            ("decide_any_expenseclaim", "Can approve or reject anyone's expense claim"),
            ("view_every_expenseclaim", "Can read everyone's expense claims"),
            ("pay_expenseclaim", "Can pay an approved expense claim"),
        ]

    def __str__(self):
        return self.number or f"Claim by {self.employee}"

    def is_open(self):
        return self.status in (ClaimStatus.DRAFT, ClaimStatus.SUBMITTED)

    def total(self):
        # At the paisa on either database: SQLite sums to whatever places the figures had.
        return (self.lines.aggregate(total=models.Sum("amount"))["total"] or ZERO).quantize(Decimal("0.01"))

    LINES_FIXED = "its lines change while it is a draft; what is submitted is what is decided."

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_moving", False):
            stored = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored != ClaimStatus.DRAFT:
                raise ValidationError(f"{self} is {stored}; a claim is changed while it is a draft.")
        self.purpose = _text(self.purpose)
        if not self.purpose:
            raise ValidationError({"purpose": ["Say what the money was spent on."]})
        self.claim_date = to_date(self.claim_date) or timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != ClaimStatus.DRAFT:
            raise ValidationError(f"{self} was submitted; it is rejected, not deleted.")
        return super().delete(*args, **kwargs)

    def _move(self, fields):
        self._moving = True
        try:
            self.save(update_fields=[*fields, "updated_at"])
        finally:
            self._moving = False

    @serialised("status")
    def submit(self):
        if self.status != ClaimStatus.DRAFT:
            raise ValidationError(f"{self} is already {self.get_status_display().lower()}.")
        if not self.lines.exists():
            raise ValidationError("A claim with no lines claims nothing.")
        if self.total() <= 0:
            raise ValidationError("A claim comes to more than nothing.")
        if not self.number:
            self.number = DocumentSequence.next_for("hr.expense_claim", self.claim_date, name="Expense claims", prefix="EXP-")
        self.status = ClaimStatus.SUBMITTED
        self._move(["number", "status"])

    def _check_decider(self, by, as_hr):
        if by is not None and by.pk == self.employee_id:
            raise ValidationError("Nobody decides their own claim.")
        if not as_hr and (by is None or self.employee.manager_id != by.pk and self.employee_id not in by.reports()):
            raise ValidationError(f"{by} does not manage {self.employee}; their manager or HR decides.")

    @serialised("status")
    def approve(self, by, note="", as_hr=False):
        if self.status != ClaimStatus.SUBMITTED:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}; only a submitted claim is decided.")
        self._check_decider(by, as_hr)
        self.status, self.decided_by, self.decided_at, self.decision_note = ClaimStatus.APPROVED, by, timezone.now(), _text(note)[:255]
        self._move(["status", "decided_by", "decided_at", "decision_note"])

    @serialised("status")
    def reject(self, by, note, as_hr=False):
        if self.status != ClaimStatus.SUBMITTED:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}; only a submitted claim is decided.")
        self._check_decider(by, as_hr)
        note = _text(note)
        if not note:
            raise ValidationError("Say why, or the same claim comes back unchanged.")
        self.status, self.decided_by, self.decided_at, self.decision_note = ClaimStatus.REJECTED, by, timezone.now(), note[:255]
        self._move(["status", "decided_by", "decided_at", "decision_note"])

    @serialised("status")
    def pay(self, paid_from, on_date=None, memo=""):
        """One journal: each line's account debited, the cash or bank account credited; the entry recorded here."""
        from apps.accounting.models import JournalEntry, JournalLine

        if self.status != ClaimStatus.APPROVED:
            raise ValidationError(f"{self} is {self.get_status_display().lower()}; only an approved claim is paid.")
        if paid_from is None:
            raise ValidationError({"paid_from": ["Say which cash or bank account it is paid from."]})
        on_date = to_date(on_date) or timezone.localdate()
        by_account = defaultdict(lambda: ZERO)
        for line in self.lines.select_related("expense_account"):
            by_account[line.expense_account] += line.amount
        with transaction.atomic():
            entry = JournalEntry.objects.create(date=on_date, memo=memo or f"Expense claim {self.number} paid to {self.employee}")
            for account, amount in by_account.items():
                JournalLine.objects.create(entry=entry, account=account, debit=amount)
            JournalLine.objects.create(entry=entry, account=paid_from, credit=self.total())
            entry.post()
            self.status, self.paid_on, self.paid_from, self.journal_entry = ClaimStatus.PAID, on_date, paid_from, entry
            self._move(["status", "paid_on", "paid_from", "journal_entry"])

    @serialised("status")
    def unpay(self, reason, on_date=None):
        """The payment reversed by a second journal, the claim approved again: a claim paid to the wrong account or twice."""
        if self.status != ClaimStatus.PAID:
            raise ValidationError(f"{self} is not paid.")
        reason = _text(reason)
        if not reason:
            raise ValidationError("Say why the payment is reversed.")
        with transaction.atomic():
            self.voided_entry = self.journal_entry.create_reversal(entry_date=to_date(on_date) or timezone.localdate())
            # The payment no longer stands, so neither does when and from where it was made: left,
            # an approved claim read "paid on 3 June from petty cash". Its entry and the reversal
            # stay recorded.
            self.status, self.paid_on, self.paid_from = ClaimStatus.APPROVED, None, None
            self.decision_note = f"Payment reversed: {reason}"[:255]
            self._move(["status", "paid_on", "paid_from", "voided_entry", "decision_note"])


class ExpenseLine(AuditModel):
    claim = models.ForeignKey(ExpenseClaim, on_delete=models.CASCADE, related_name="lines")
    spent_on = models.DateField()
    expense_account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    receipt_reference = models.CharField(max_length=64, blank=True, help_text="The bill or ticket number, if there is one.")

    class Meta:
        ordering = ["spent_on", "id"]
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="expense_line_amount_positive")]

    def __str__(self):
        return f"{self.description}: {self.amount}"

    def _check_open(self):
        claim = ExpenseClaim.objects.get(pk=self.claim_id)
        if claim.status != ClaimStatus.DRAFT:
            raise ValidationError(f"{claim}: {ExpenseClaim.LINES_FIXED}")

    def save(self, *args, **kwargs):
        self._check_open()
        self.description = _text(self.description)
        if not self.description:
            raise ValidationError({"description": ["Say what was bought."]})
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": ["An expense is more than nothing."]})
        self.spent_on = to_date(self.spent_on) or timezone.localdate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_open()
        return super().delete(*args, **kwargs)
