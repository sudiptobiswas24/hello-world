from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    Payment,
    PaymentDirection,
    Tax,
    compute_taxes,
    round_money,
)
from apps.core.models import (
    Address,
    AuditModel,
    Currency,
    DocumentSequence,
    Party,
    PartyRole,
    PaymentTerms,
    UnitOfMeasure,
    to_date,
)
from apps.inventory.models import Item


def _require_customer_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.CUSTOMER).exists():
        raise ValidationError(f"{party} does not have the Customer role.")


class TaxedLineMixin(models.Model):
    """
    Shared money arithmetic for a sellable line: gross, discount, net, tax.
    Taxes apply to the discounted net, which is the conventional order.
    """

    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Line discount, e.g. 10.00 for 10% off.",
    )

    class Meta:
        abstract = True

    def gross_amount(self):
        return round_money(self.quantity * self.unit_price)

    def discount_amount(self):
        return round_money(self.gross_amount() * self.discount_percent / Decimal("100"))

    def net_amount(self):
        """Amount before tax, after discount — the invoice line 'subtotal'."""
        return self.gross_amount() - self.discount_amount()

    # Kept as the conventional name for the pre-tax line amount.
    def subtotal(self):
        return self.net_amount()

    def tax_amounts(self):
        """[(tax, amount)] for this line, honouring inclusive and compound taxes."""
        taxes = list(self.taxes.all())
        if not taxes:
            return []
        _, lines, _ = compute_taxes(taxes, self.net_amount(), self.quantity)
        return lines

    def tax_total(self):
        return sum((amount for _, amount in self.tax_amounts()), Decimal("0"))

    def total(self):
        return self.net_amount() + self.tax_total()


class TaxedDocumentMixin(models.Model):
    """Totals for a document made of TaxedLineMixin lines."""

    class Meta:
        abstract = True

    def subtotal(self):
        return sum((line.net_amount() for line in self.lines.all()), Decimal("0"))

    def tax_total(self):
        return sum((line.tax_total() for line in self.lines.all()), Decimal("0"))

    def total(self):
        return self.subtotal() + self.tax_total()

    def tax_breakdown(self):
        """{tax: amount} across all lines, for invoice summary lines."""
        totals = defaultdict(Decimal)
        for line in self.lines.all():
            for tax, amount in line.tax_amounts():
                totals[tax] += amount
        return dict(totals)


class SettlementStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNPAID = "unpaid", "Unpaid"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


class SalesOrder(TaxedDocumentMixin, AuditModel):
    number = models.CharField(max_length=32, unique=True, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="sales_orders")
    order_date = models.DateField()
    reference = models.CharField(
        max_length=64, blank=True, help_text="The customer's own PO number, if any."
    )
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    currency = models.ForeignKey(Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-order_date", "-id"]

    def __str__(self):
        return f"{self.number or f'SO-draft-{self.pk}'} {self.customer}"

    def clean(self):
        _require_customer_role(self.customer)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._apply_customer_defaults()
        super().save(*args, **kwargs)

    def _apply_customer_defaults(self):
        if not self.customer_id:
            return
        customer = self.customer
        self.currency = self.currency or customer.default_currency
        self.payment_terms = self.payment_terms or customer.payment_terms
        self.billing_address = self.billing_address or customer.billing_address()
        self.shipping_address = self.shipping_address or customer.shipping_address()

    @transaction.atomic
    def confirm(self):
        if self.status == OrderStatus.CONFIRMED:
            raise ValidationError("This order is already confirmed.")
        if self.status == OrderStatus.CANCELLED:
            raise ValidationError("A cancelled order cannot be confirmed.")
        if not self.lines.exists():
            raise ValidationError("Cannot confirm an order with no lines.")
        if not self.number:
            self.number = DocumentSequence.next_for(
                "sales.order", self.order_date, name="Sales Orders", prefix="SO-"
            )
        self.status = OrderStatus.CONFIRMED
        self.save(update_fields=["number", "status", "updated_at"])

    @transaction.atomic
    def create_invoice(self, receivable_account, invoice_date=None):
        """Turn this order into a draft invoice, carrying taxes and discounts across."""
        if self.status != OrderStatus.CONFIRMED:
            raise ValidationError("Only a confirmed order can be invoiced.")

        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date=invoice_date or timezone.now().date(),
            reference=self.reference,
            sales_order=self,
            receivable_account=receivable_account,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
        )
        for line in self.lines.all():
            invoice_line = InvoiceLine.objects.create(
                invoice=invoice,
                item=line.item,
                description=str(line.item),
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            invoice_line.taxes.set(line.taxes.all())
        return invoice


class SalesOrderLine(TaxedLineMixin, AuditModel):
    order = models.ForeignKey(SalesOrder, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sales_order_lines")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    revenue_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    taxes = models.ManyToManyField(Tax, blank=True, related_name="sales_order_lines")

    def __str__(self):
        return f"{self.item} x{self.quantity}"


class Invoice(TaxedDocumentMixin, AuditModel):
    """
    Sales invoice. Posting creates a balanced JournalEntry (Dr Accounts
    Receivable / Cr Revenue / Cr tax accounts) via Accounting — Sales
    never writes ledger rows itself. Once posted, an invoice is immutable
    exactly like a JournalEntry: the only way to correct one is a credit
    note, which reuses JournalEntry.create_reversal() rather than
    inventing its own correction logic.
    """

    number = models.CharField(max_length=32, unique=True, blank=True, editable=False)
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True, editable=False)
    reference = models.CharField(max_length=64, blank=True)
    sales_order = models.ForeignKey(
        SalesOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, editable=False,
        help_text="Rate to the base currency captured at posting time.",
    )
    payment_terms = models.ForeignKey(
        PaymentTerms, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    credits = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="credit_notes",
        help_text="Set when this invoice is a credit note correcting another invoice.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        permissions = [("post_invoice", "Can post invoices and issue credit notes")]

    def __str__(self):
        if self.number:
            return f"{self.number} {self.customer}"
        kind = "CN" if self.credits_id else "INV"
        return f"{kind}-draft-{self.pk} {self.customer}"

    def is_credit_note(self):
        return bool(self.credits_id)

    def amount_paid(self):
        return sum(
            (allocation.amount for allocation in self.payment_allocations.all()), Decimal("0")
        )

    def amount_credited(self):
        """Value of posted credit notes issued against this invoice."""
        return sum(
            (note.total() for note in self.credit_notes.filter(posted=True)), Decimal("0")
        )

    def amount_due(self):
        return self.total() - self.amount_paid() - self.amount_credited()

    def settlement_status(self):
        if not self.posted:
            return SettlementStatus.DRAFT
        if self.amount_due() <= 0:
            return SettlementStatus.PAID
        if self.amount_paid() or self.amount_credited():
            return SettlementStatus.PARTIAL
        return SettlementStatus.UNPAID

    def is_overdue(self, as_of=None):
        if not self.posted or not self.due_date or self.amount_due() <= 0:
            return False
        return self.due_date < (to_date(as_of) or timezone.now().date())

    def days_overdue(self, as_of=None):
        if not self.is_overdue(as_of):
            return 0
        return ((to_date(as_of) or timezone.now().date()) - self.due_date).days

    def clean(self):
        _require_customer_role(self.customer)
        if self.credits_id and self.credits.customer_id != self.customer_id:
            raise ValidationError("A credit note must be for the same customer as the invoice it credits.")

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Invoice.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This invoice is posted and immutable. Issue a credit note instead."
            )
        if self._state.adding:
            self._apply_customer_defaults()
        super().save(*args, **kwargs)

    def _apply_customer_defaults(self):
        if not self.customer_id:
            return
        customer = self.customer
        self.currency = self.currency or customer.default_currency
        self.payment_terms = self.payment_terms or customer.payment_terms
        self.billing_address = self.billing_address or customer.billing_address()
        self.shipping_address = self.shipping_address or customer.shipping_address()

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted invoices cannot be deleted. Issue a credit note instead.")
        super().delete(*args, **kwargs)

    def _rate_for_posting(self):
        if self.currency is None:
            return Decimal("1")
        return self.currency.rate_on(self.invoice_date)

    def _build_journal_entry(self, rate):
        """
        Build the ledger entry in base currency.

        Credits are computed first and the receivable debit is set to their
        exact sum: rounding each converted line independently can leave the
        debit a cent off the credits, which would make a legitimate invoice
        unpostable.
        """
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("Cannot post an invoice with no lines.")

        entry = JournalEntry.objects.create(
            date=self.invoice_date,
            reference=self.number or self.reference,
            memo=f"Invoice {self.number} for {self.customer}",
        )

        credits = []
        for line in lines:
            if line.revenue_account_id is None:
                raise ValidationError(f"Line '{line}' has no revenue account.")
            credits.append((line.revenue_account, round_money(line.net_amount() * rate),
                            line.description or str(line.item)))

        tax_totals = defaultdict(Decimal)
        for line in lines:
            for tax, amount in line.tax_amounts():
                if not tax.applies_to_sales():
                    raise ValidationError(f"Tax {tax.code} is not configured for sales.")
                account = tax.account_for(is_sale=True)
                if account is None:
                    raise ValidationError(f"Tax {tax.code} has no collected account.")
                tax_totals[account] += amount
        for account, amount in tax_totals.items():
            credits.append((account, round_money(amount * rate), "Tax"))

        receivable_total = sum(amount for _, amount, _ in credits)
        JournalLine.objects.create(
            entry=entry,
            account=self.receivable_account,
            party=self.customer,
            debit=receivable_total,
            description=f"Invoice {self.number}",
        )
        for account, amount, description in credits:
            if amount:
                JournalLine.objects.create(
                    entry=entry, account=account, party=self.customer,
                    credit=amount, description=description,
                )
        return entry

    @transaction.atomic
    def post(self, memo=None):
        if self.posted:
            raise ValidationError("This invoice is already posted.")

        self.invoice_date = to_date(self.invoice_date)
        if not self.number:
            if self.is_credit_note():
                self.number = DocumentSequence.next_for(
                    "sales.credit_note", self.invoice_date, name="Credit Notes", prefix="CN-"
                )
            else:
                self.number = DocumentSequence.next_for(
                    "sales.invoice", self.invoice_date, name="Customer Invoices", prefix="INV-"
                )

        self.exchange_rate = self._rate_for_posting()
        self.due_date = (
            self.payment_terms.due_date(self.invoice_date)
            if self.payment_terms_id else self.invoice_date
        )

        if self.is_credit_note():
            original = self.credits
            if not original.posted or not original.journal_entry_id:
                raise ValidationError("Cannot post a credit note against an unposted invoice.")
            entry = original.journal_entry.create_reversal(
                entry_date=self.invoice_date,
                memo=memo or f"Credit note {self.number} for {original.number}",
            )
        else:
            entry = self._build_journal_entry(self.exchange_rate)
            entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Invoice, self).save(
            update_fields=[
                "number", "invoice_date", "due_date", "exchange_rate", "journal_entry",
                "posted", "posted_at", "updated_at",
            ]
        )

    @transaction.atomic
    def create_credit_note(self, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted invoice can be credited.")
        if self.is_credit_note():
            raise ValidationError("Cannot issue a credit note against a credit note.")

        credit_note = Invoice.objects.create(
            customer=self.customer,
            invoice_date=timezone.now().date(),
            reference=self.reference,
            receivable_account=self.receivable_account,
            currency=self.currency,
            payment_terms=self.payment_terms,
            billing_address=self.billing_address,
            shipping_address=self.shipping_address,
            credits=self,
        )
        for line in self.lines.all():
            credit_line = InvoiceLine.objects.create(
                invoice=credit_note,
                item=line.item,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                revenue_account=line.revenue_account,
            )
            credit_line.taxes.set(line.taxes.all())
        credit_note.post(memo=memo)
        return credit_note


class InvoiceLine(TaxedLineMixin, AuditModel):
    invoice = models.ForeignKey(Invoice, related_name="lines", on_delete=models.CASCADE)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines")
    description = models.CharField(max_length=255, blank=True)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    taxes = models.ManyToManyField(Tax, blank=True, related_name="invoice_lines")

    def __str__(self):
        return f"{self.item or self.description} x{self.quantity}"

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


class InvoicePayment(AuditModel):
    """
    Applies part (or all) of a Payment to an Invoice. The ledger entry was
    already made when the payment posted — this records *which* invoices
    that money settles, which is what makes an aging report possible.

    Allocations stay editable after the fact: re-applying a payment to a
    different invoice is a bookkeeping correction, not a ledger change.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payment_allocations")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="invoice_allocations")
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "payment"], name="one_allocation_per_invoice_and_payment"
            ),
            models.CheckConstraint(check=Q(amount__gt=0), name="allocation_amount_positive"),
        ]

    def __str__(self):
        return f"{self.payment} -> {self.invoice} ({self.amount})"

    @staticmethod
    def allocated_for(payment, excluding=None):
        allocations = InvoicePayment.objects.filter(payment=payment)
        if excluding is not None and excluding.pk:
            allocations = allocations.exclude(pk=excluding.pk)
        return allocations.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")

    @staticmethod
    def unallocated_for(payment):
        return payment.amount - InvoicePayment.allocated_for(payment)

    def clean(self):
        if not self.payment_id or not self.invoice_id:
            return
        if not self.payment.posted:
            raise ValidationError("Only a posted payment can be allocated.")
        if not self.invoice.posted:
            raise ValidationError("Only a posted invoice can be settled.")
        if self.payment.direction != PaymentDirection.RECEIPT:
            raise ValidationError("Only a receipt can settle a customer invoice.")
        if self.payment.party_id != self.invoice.customer_id:
            raise ValidationError("The payment and the invoice belong to different parties.")

        available = self.payment.amount - InvoicePayment.allocated_for(self.payment, excluding=self)
        if self.amount > available:
            raise ValidationError(
                f"Only {available} of this payment is unallocated; cannot apply {self.amount}."
            )

        outstanding = self.invoice.amount_due() + (
            InvoicePayment.objects.filter(pk=self.pk).first().amount if self.pk else Decimal("0")
        )
        if self.amount > outstanding:
            raise ValidationError(
                f"The invoice only has {outstanding} outstanding; cannot apply {self.amount}."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


AGING_BUCKETS = ((1, 30), (31, 60), (61, 90))


def ar_aging(as_of=None):
    """
    Outstanding customer invoices bucketed by how overdue they are.

    Note: amount_due is computed per invoice in Python rather than
    annotated in SQL, so this is fine for reporting over thousands of
    invoices but would need an annotated query at much larger volumes.
    """
    as_of = to_date(as_of) or timezone.now().date()
    buckets = {"current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}

    invoices = (
        Invoice.objects.filter(posted=True, credits__isnull=True)
        .prefetch_related("lines__taxes", "payment_allocations", "credit_notes__lines__taxes")
    )
    for invoice in invoices:
        due = invoice.amount_due()
        if due <= 0:
            continue
        days = invoice.days_overdue(as_of)
        if days == 0:
            key = "current"
        elif days > 90:
            key = "90+"
        else:
            key = next(f"{lo}-{hi}" for lo, hi in AGING_BUCKETS if lo <= days <= hi)
        buckets[key].append({"invoice": invoice, "days_overdue": days, "amount_due": due})

    return {
        key: {
            "count": len(entries),
            "total": sum((entry["amount_due"] for entry in entries), Decimal("0")),
            "invoices": entries,
        }
        for key, entries in buckets.items()
    }
