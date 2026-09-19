from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.core.models import AuditModel, Currency, Party, PartyRole, UnitOfMeasure
from apps.inventory.models import Item


def _require_vendor_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.VENDOR).exists():
        raise ValidationError(f"{party} does not have the Vendor role.")


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(AuditModel):
    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"PO-{self.pk} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))


class PurchaseOrderLine(AuditModel):
    order = models.ForeignKey(PurchaseOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_order_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price


class Bill(AuditModel):
    """
    Vendor bill — the Purchasing mirror of Sales' Invoice. Posting builds
    a balanced JournalEntry (Dr Expense per line / Cr Accounts Payable)
    via Accounting; Purchasing never writes ledger rows itself. A posted
    bill is immutable; the only correction path is a debit note
    (create_debit_note), which — exactly like Invoice.create_credit_note —
    delegates to JournalEntry.create_reversal() instead of a bespoke
    correction mechanism.
    """

    vendor = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="bills")
    bill_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="bills"
    )
    payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    debits = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="debit_notes",
        help_text="Set when this bill is a debit note correcting another bill.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-bill_date", "-id"]

    def __str__(self):
        kind = "DN" if self.debits_id else "BILL"
        return f"{kind}-{self.pk} {self.vendor}"

    def clean(self):
        _require_vendor_role(self.vendor)
        if self.debits_id and self.debits.vendor_id != self.vendor_id:
            raise ValidationError("A debit note must be for the same vendor as the bill it corrects.")

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Bill.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError("This bill is posted and immutable. Issue a debit note instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted bills cannot be deleted. Issue a debit note instead.")
        super().delete(*args, **kwargs)

    def _build_journal_entry(self):
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post a bill with no lines.")
        entry = JournalEntry.objects.create(
            date=self.bill_date,
            reference=self.reference,
            memo=f"Bill BILL-{self.pk} from {self.vendor}",
        )
        JournalLine.objects.create(
            entry=entry,
            account=self.payable_account,
            party=self.vendor,
            credit=self.total(),
            description=f"Bill BILL-{self.pk}",
        )
        for line in lines:
            JournalLine.objects.create(
                entry=entry,
                account=line.expense_account,
                party=self.vendor,
                debit=line.subtotal(),
                description=line.description or str(line.item),
            )
        return entry

    @transaction.atomic
    def post(self, memo=None):
        if self.posted:
            raise ValidationError("This bill is already posted.")
        if self.debits_id:
            original = self.debits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a debit note against an unposted bill.")
            entry = original.journal_entry.create_reversal(
                entry_date=self.bill_date,
                memo=memo or f"Debit note DN-{self.pk} for BILL-{original.pk}",
            )
        else:
            entry = self._build_journal_entry()
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Bill, self).save(update_fields=["journal_entry", "posted", "posted_at", "updated_at"])

    @transaction.atomic
    def create_debit_note(self, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted bill can be corrected with a debit note.")
        if self.debits_id:
            raise ValidationError("Cannot issue a debit note against a debit note.")

        debit_note = Bill.objects.create(
            vendor=self.vendor,
            bill_date=timezone.now().date(),
            reference=self.reference,
            payable_account=self.payable_account,
            debits=self,
        )
        for line in self.lines.all():
            BillLine.objects.create(
                bill=debit_note,
                item=line.item,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                expense_account=line.expense_account,
            )
        debit_note.post(memo=memo)
        return debit_note


class BillLine(AuditModel):
    bill = models.ForeignKey(Bill, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="bill_lines")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"{self.item or self.description} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if self.bill_id and Bill.objects.filter(pk=self.bill_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted bill. Issue a debit note instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.bill.posted:
            raise ValidationError(
                "Cannot delete a line on a posted bill. Issue a debit note instead."
            )
        super().delete(*args, **kwargs)
