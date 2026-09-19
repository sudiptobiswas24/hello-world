from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.core.models import AuditModel, Currency, Party, PartyRole, UnitOfMeasure
from apps.inventory.models import Item


def _require_customer_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.CUSTOMER).exists():
        raise ValidationError(f"{party} does not have the Customer role.")


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class SalesOrder(AuditModel):
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="sales_orders")
    order_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"SO-{self.pk} {self.customer}"

    def clean(self):
        _require_customer_role(self.customer)

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))


class SalesOrderLine(AuditModel):
    order = models.ForeignKey(SalesOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sales_order_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    def __str__(self):
        return f"{self.item} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price


class Invoice(AuditModel):
    """
    Sales invoice. Posting creates a balanced JournalEntry (Dr Accounts
    Receivable / Cr Revenue) via Accounting — Sales never writes ledger
    rows itself. Once posted, an invoice is immutable exactly like a
    JournalEntry: the only way to correct one is a credit note, which
    reuses JournalEntry.create_reversal() rather than inventing its own
    correction logic.
    """

    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    sales_order = models.ForeignKey(
        SalesOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    credits = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="credit_notes",
        help_text="Set when this invoice is a credit note correcting another invoice.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]

    def __str__(self):
        kind = "CN" if self.credits_id else "INV"
        return f"{kind}-{self.pk} {self.customer}"

    def clean(self):
        _require_customer_role(self.customer)
        if self.credits_id and self.credits.customer_id != self.customer_id:
            raise ValidationError("A credit note must be for the same customer as the invoice it credits.")

    def total(self):
        return sum((line.subtotal() for line in self.lines.all()), Decimal("0"))

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Invoice.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This invoice is posted and immutable. Issue a credit note instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted invoices cannot be deleted. Issue a credit note instead.")
        super().delete(*args, **kwargs)

    def _build_journal_entry(self):
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post an invoice with no lines.")
        entry = JournalEntry.objects.create(
            date=self.invoice_date,
            reference=self.reference,
            memo=f"Invoice INV-{self.pk} for {self.customer}",
        )
        JournalLine.objects.create(
            entry=entry,
            account=self.receivable_account,
            party=self.customer,
            debit=self.total(),
            description=f"Invoice INV-{self.pk}",
        )
        for line in lines:
            JournalLine.objects.create(
                entry=entry,
                account=line.revenue_account,
                party=self.customer,
                credit=line.subtotal(),
                description=line.description or str(line.item),
            )
        return entry

    @transaction.atomic
    def post(self, memo=None):
        if self.posted:
            raise ValidationError("This invoice is already posted.")
        if self.credits_id:
            original = self.credits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a credit note against an unposted invoice.")
            entry = original.journal_entry.create_reversal(
                entry_date=self.invoice_date,
                memo=memo or f"Credit note CN-{self.pk} for INV-{original.pk}",
            )
        else:
            entry = self._build_journal_entry()
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Invoice, self).save(update_fields=["journal_entry", "posted", "posted_at", "updated_at"])

    @transaction.atomic
    def create_credit_note(self, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted invoice can be credited.")
        if self.credits_id:
            raise ValidationError("Cannot issue a credit note against a credit note.")

        credit_note = Invoice.objects.create(
            customer=self.customer,
            invoice_date=timezone.now().date(),
            reference=self.reference,
            receivable_account=self.receivable_account,
            credits=self,
        )
        for line in self.lines.all():
            InvoiceLine.objects.create(
                invoice=credit_note,
                item=line.item,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                revenue_account=line.revenue_account,
            )
        credit_note.post(memo=memo)
        return credit_note


class InvoiceLine(AuditModel):
    invoice = models.ForeignKey(Invoice, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"{self.item or self.description} x{self.quantity}"

    def subtotal(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if self.invoice_id and Invoice.objects.filter(pk=self.invoice_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted invoice. Issue a credit note instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.posted:
            raise ValidationError(
                "Cannot delete a line on a posted invoice. Issue a credit note instead."
            )
        super().delete(*args, **kwargs)
