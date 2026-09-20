from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, Country, Currency, DocumentSequence, Party, to_date

CENTS = Decimal("0.01")


def round_money(amount):
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


class Account(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    currency = models.ForeignKey(
        Currency,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accounts",
        help_text="Defaults to the company's base currency if unset.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError("An account cannot be its own parent.")
        if self.parent_id and self.parent.account_type != self.account_type:
            raise ValidationError("A sub-account must have the same account_type as its parent.")


class JournalEntry(AuditModel):
    """
    A single balanced transaction. Draft entries (posted=False) may be
    freely edited. Once posted, an entry and its lines are immutable —
    correcting a mistake means posting a reversing entry, never editing
    history. That immutability is enforced in save()/delete() below, not
    just by convention.
    """

    date = models.DateField()
    reference = models.CharField(max_length=64, blank=True)
    memo = models.TextField(blank=True)
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by"
    )

    class Meta:
        verbose_name_plural = "journal entries"
        ordering = ["-date", "-id"]
        permissions = [("post_journalentry", "Can post and reverse journal entries")]

    def __str__(self):
        return f"JE-{self.pk} {self.date} {self.memo[:40]}"

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return JournalEntry.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError(
                "This journal entry is posted and immutable. Create a reversing entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError(
                "Posted journal entries cannot be deleted. Create a reversing entry instead."
            )
        super().delete(*args, **kwargs)

    def total_debit(self):
        return self.lines.aggregate(total=Sum("debit"))["total"] or Decimal("0")

    def total_credit(self):
        return self.lines.aggregate(total=Sum("credit"))["total"] or Decimal("0")

    def is_balanced(self):
        debit = self.total_debit()
        return debit > 0 and debit == self.total_credit()

    @transaction.atomic
    def post(self):
        if self.posted:
            raise ValidationError("This journal entry is already posted.")
        if not self.is_balanced():
            raise ValidationError(
                f"Cannot post an unbalanced entry (debit={self.total_debit()}, "
                f"credit={self.total_credit()})."
            )
        self.posted = True
        self.posted_at = timezone.now()
        super(JournalEntry, self).save(update_fields=["posted", "posted_at", "updated_at"])

    @transaction.atomic
    def create_reversal(self, entry_date=None, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted journal entry can be reversed.")
        reversal = JournalEntry.objects.create(
            date=entry_date or timezone.now().date(),
            reference=self.reference,
            memo=memo or f"Reversal of JE-{self.pk}",
            reverses=self,
        )
        for line in self.lines.all():
            JournalLine.objects.create(
                entry=reversal,
                account=line.account,
                party=line.party,
                debit=line.credit,
                credit=line.debit,
                description=line.description,
            )
        reversal.post()
        return reversal


class JournalLine(AuditModel):
    entry = models.ForeignKey(JournalEntry, related_name="lines", on_delete=models.CASCADE)
    account = models.ForeignKey(Account, related_name="lines", on_delete=models.PROTECT)
    party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="journal_lines"
    )
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~(Q(debit__gt=0) & Q(credit__gt=0)),
                name="line_not_both_debit_and_credit",
            ),
            models.CheckConstraint(
                check=Q(debit__gte=0) & Q(credit__gte=0),
                name="line_amounts_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.account} D{self.debit}/C{self.credit}"

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError("A journal line cannot have both a debit and a credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line must have either a debit or a credit.")

    def save(self, *args, **kwargs):
        if self.entry_id and JournalEntry.objects.filter(pk=self.entry_id, posted=True).exists():
            raise ValidationError(
                "Cannot modify a line on a posted journal entry. Create a reversing entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.posted:
            raise ValidationError(
                "Cannot delete a line on a posted journal entry. Create a reversing entry instead."
            )
        super().delete(*args, **kwargs)


class TaxComputation(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage of base"
    FIXED = "fixed", "Fixed amount per unit"


class TaxScope(models.TextChoices):
    SALES = "sales", "Sales only"
    PURCHASE = "purchase", "Purchases only"
    BOTH = "both", "Sales and purchases"


class TaxGroup(AuditModel):
    """Groups taxes for summary lines and reporting (e.g. 'VAT 20%')."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class Tax(AuditModel):
    """
    A single tax rate. Lives in Accounting rather than Core because a tax
    is meaningless without the GL accounts it posts to, and Core must not
    depend on Accounting.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    group = models.ForeignKey(
        TaxGroup, null=True, blank=True, on_delete=models.PROTECT, related_name="taxes"
    )
    computation = models.CharField(
        max_length=16, choices=TaxComputation.choices, default=TaxComputation.PERCENTAGE
    )
    rate = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        help_text="Percentage (20.0000 = 20%) or, for fixed taxes, amount per unit.",
    )
    price_included = models.BooleanField(
        default=False, help_text="The unit price already contains this tax (common in EU retail)."
    )
    include_base_amount = models.BooleanField(
        default=False,
        help_text="Add this tax to the base used by later taxes in sequence (compound tax).",
    )
    sequence = models.PositiveSmallIntegerField(
        default=10, help_text="Application order; matters for compound taxes."
    )
    scope = models.CharField(max_length=16, choices=TaxScope.choices, default=TaxScope.BOTH)
    collected_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Sales: where tax collected is credited (usually a liability).",
    )
    paid_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Purchases: where tax paid is debited (usually a recoverable asset).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "taxes"
        ordering = ["sequence", "code"]
        constraints = [models.CheckConstraint(check=Q(rate__gte=0), name="tax_rate_not_negative")]

    def __str__(self):
        if self.computation == TaxComputation.FIXED:
            return f"{self.name} ({self.rate}/unit)"
        return f"{self.name} ({self.rate}%)"

    def clean(self):
        if self.scope in (TaxScope.SALES, TaxScope.BOTH) and not self.collected_account_id:
            raise ValidationError("A sales tax needs a collected_account to post to.")
        if self.scope in (TaxScope.PURCHASE, TaxScope.BOTH) and not self.paid_account_id:
            raise ValidationError("A purchase tax needs a paid_account to post to.")
        if self.price_included and self.computation == TaxComputation.FIXED and self.rate < 0:
            raise ValidationError("A fixed included tax cannot be negative.")

    def applies_to_sales(self):
        return self.scope in (TaxScope.SALES, TaxScope.BOTH)

    def applies_to_purchases(self):
        return self.scope in (TaxScope.PURCHASE, TaxScope.BOTH)

    def account_for(self, is_sale):
        return self.collected_account if is_sale else self.paid_account

    def compute(self, base_amount, quantity=Decimal("1")):
        """Tax due on `base_amount`, which must already be tax-exclusive."""
        if self.computation == TaxComputation.FIXED:
            return round_money(self.rate * quantity)
        return round_money(base_amount * self.rate / Decimal("100"))


def compute_taxes(taxes, amount, quantity=Decimal("1")):
    """
    Apply `taxes` to `amount` and return (net_base, [(tax, tax_amount)], total).

    `amount` is the line amount as entered. Any price-included taxes are
    stripped out of it first to find the tax-exclusive base, then every
    tax is applied in `sequence` order, with compound taxes adding their
    own amount to the base seen by later taxes.
    """
    ordered = sorted(taxes, key=lambda tax: (tax.sequence, tax.code))
    base = amount

    included_percentage = [
        tax for tax in ordered
        if tax.price_included and tax.computation == TaxComputation.PERCENTAGE
    ]
    included_fixed = [
        tax for tax in ordered
        if tax.price_included and tax.computation == TaxComputation.FIXED
    ]
    for tax in included_fixed:
        base -= tax.rate * quantity
    if included_percentage:
        total_rate = sum(tax.rate for tax in included_percentage)
        base = base * Decimal("100") / (Decimal("100") + total_rate)
    base = round_money(base)

    results = []
    running_base = base
    for tax in ordered:
        tax_amount = tax.compute(running_base, quantity)
        results.append((tax, tax_amount))
        if tax.include_base_amount:
            running_base += tax_amount

    total = base + sum(amount for _, amount in results)
    return base, results, round_money(total)


class ChargeType(AuditModel):
    """
    Something billable that isn't stock: freight, handling, installation,
    a rush surcharge.

    Modelled as a line on the document rather than a separate charges
    table, because that is what it is — the other party sees it as a line,
    and it needs the same discount, tax and correction treatment every
    other line gets. Making it an Item instead would put freight through
    inventory valuation and fold it into product margin, since a charge
    has revenue but no cost of goods.

    One row serves both directions because it is one concept: a carrier
    charges the company freight, and the company recharges freight to its
    customers. Two models would mean two code lists to keep aligned and
    two places to get the tax treatment wrong.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    revenue_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where this lands when charged to a customer. Keep it separate from "
                  "product revenue: recharged freight with no cost behind it otherwise "
                  "flatters gross margin.",
    )
    expense_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Where this lands when a vendor charges it to the company.",
    )
    taxes = models.ManyToManyField(
        Tax, blank=True, related_name="charge_types",
        help_text="Applied by default when this charge is added to a document.",
    )
    capitalise_into_inventory = models.BooleanField(
        default=False,
        help_text="Inbound only: add this charge to the value of the goods it brought in "
                  "rather than expensing it, so cost of sales reflects what the stock "
                  "actually cost to get here.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

    def account_for(self, is_sale):
        account = self.revenue_account if is_sale else self.expense_account
        if account is None:
            side = "revenue" if is_sale else "expense"
            raise ValidationError(f"Charge '{self.code}' has no {side} account configured.")
        return account


class FiscalPosition(AuditModel):
    """
    Substitutes taxes based on who you're dealing with — zero-rating an
    export, or swapping domestic VAT for a reverse charge. Without this,
    every customer would be taxed as if they were domestic.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    country = models.ForeignKey(
        Country, null=True, blank=True, on_delete=models.PROTECT, related_name="fiscal_positions"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

    def map_tax(self, tax):
        """The tax that replaces `tax` here; None means it doesn't apply at all."""
        mapping = self.tax_mappings.filter(source_tax=tax).first()
        if mapping is None:
            return tax
        return mapping.target_tax

    def map_taxes(self, taxes):
        mapped = [self.map_tax(tax) for tax in taxes]
        return [tax for tax in mapped if tax is not None]


class FiscalPositionTaxMapping(AuditModel):
    fiscal_position = models.ForeignKey(
        FiscalPosition, on_delete=models.CASCADE, related_name="tax_mappings"
    )
    source_tax = models.ForeignKey(Tax, on_delete=models.CASCADE, related_name="+")
    target_tax = models.ForeignKey(
        Tax, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
        help_text="Leave empty to drop the source tax entirely (e.g. an exempt export).",
    )

    class Meta:
        ordering = ["fiscal_position", "source_tax"]
        constraints = [
            models.UniqueConstraint(
                fields=["fiscal_position", "source_tax"], name="unique_tax_mapping_per_position"
            )
        ]

    def __str__(self):
        target = self.target_tax.code if self.target_tax_id else "no tax"
        return f"{self.source_tax.code} -> {target}"

    def clean(self):
        if self.target_tax_id and self.target_tax_id == self.source_tax_id:
            raise ValidationError("Mapping a tax to itself has no effect.")


class PartyTaxProfile(AuditModel):
    """
    Tax settings for a Party. Held here rather than on core.Party so the
    kernel stays independent of Accounting.
    """

    party = models.OneToOneField(Party, on_delete=models.CASCADE, related_name="tax_profile")
    fiscal_position = models.ForeignKey(
        FiscalPosition, null=True, blank=True, on_delete=models.PROTECT, related_name="parties"
    )
    tax_exempt = models.BooleanField(default=False)
    exemption_reference = models.CharField(
        max_length=64, blank=True, help_text="Certificate or registration number for the exemption."
    )

    def __str__(self):
        return f"Tax profile for {self.party}"

    def clean(self):
        if self.tax_exempt and not self.exemption_reference:
            raise ValidationError("An exempt party needs an exemption reference on file.")

    def applicable_taxes(self, taxes):
        if self.tax_exempt:
            return []
        if self.fiscal_position_id:
            return self.fiscal_position.map_taxes(taxes)
        return list(taxes)


class PaymentDirection(models.TextChoices):
    RECEIPT = "receipt", "Receipt (money in)"
    DISBURSEMENT = "disbursement", "Disbursement (money out)"


class Payment(AuditModel):
    """
    Money actually moving, posted to the ledger. Deliberately generic and
    free of any link to invoices or bills: Accounting must not import
    Sales or Purchasing, so each of those owns its own allocation model
    pointing back here.

    Posting is one-way like everything else in the ledger — a mistaken
    payment is voided with a reversing entry, never edited.
    """

    number = models.CharField(max_length=32, blank=True, editable=False)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="payments")
    direction = models.CharField(max_length=16, choices=PaymentDirection.choices)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.ForeignKey(
        Currency, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, editable=False,
        help_text="Rate to the base currency captured at posting time.",
    )
    bank_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="The cash or bank account the money moves through.",
    )
    counterpart_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="Receivable for a receipt, payable for a disbursement.",
    )
    reference = models.CharField(max_length=64, blank=True)
    memo = models.TextField(blank=True)
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        permissions = [("post_payment", "Can post and void payments")]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payment_amount_positive"),
            # Drafts share an empty number until posted.
            models.UniqueConstraint(
                fields=["number"], condition=~Q(number=""), name="unique_payment_number"
            ),
        ]

    def __str__(self):
        return f"{self.number or f'PAY-draft-{self.pk}'} {self.party} {self.amount}"

    def _was_posted_in_db(self):
        if not self.pk:
            return False
        return Payment.objects.filter(pk=self.pk, posted=True).exists()

    def save(self, *args, **kwargs):
        if self._was_posted_in_db():
            raise ValidationError("This payment is posted and immutable. Void it instead.")
        if self._state.adding and not self.currency_id and self.party_id:
            self.currency = self.party.default_currency
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.posted:
            raise ValidationError("Posted payments cannot be deleted. Void the payment instead.")
        super().delete(*args, **kwargs)

    def is_receipt(self):
        return self.direction == PaymentDirection.RECEIPT

    def _rate_for_posting(self):
        if self.currency is None:
            return Decimal("1")
        return self.currency.rate_on(self.payment_date)

    @transaction.atomic
    def post(self):
        if self.posted:
            raise ValidationError("This payment is already posted.")

        self.payment_date = to_date(self.payment_date)
        if not self.number:
            self.number = DocumentSequence.next_for(
                "accounting.payment", self.payment_date, name="Payments", prefix="PAY-"
            )
        self.exchange_rate = self._rate_for_posting()
        base_amount = round_money(self.amount * self.exchange_rate)

        entry = JournalEntry.objects.create(
            date=self.payment_date,
            reference=self.number,
            memo=self.memo or f"Payment {self.number} for {self.party}",
        )
        if self.is_receipt():
            debit_account, credit_account = self.bank_account, self.counterpart_account
        else:
            debit_account, credit_account = self.counterpart_account, self.bank_account

        JournalLine.objects.create(
            entry=entry, account=debit_account, party=self.party,
            debit=base_amount, description=f"Payment {self.number}",
        )
        JournalLine.objects.create(
            entry=entry, account=credit_account, party=self.party,
            credit=base_amount, description=f"Payment {self.number}",
        )
        entry.post()

        self.journal_entry = entry
        self.posted = True
        self.posted_at = timezone.now()
        super(Payment, self).save(
            update_fields=[
                "number", "payment_date", "exchange_rate", "journal_entry",
                "posted", "posted_at", "updated_at",
            ]
        )

    @transaction.atomic
    def void(self, memo=""):
        """Reverse a posted payment. Allocations must be released first."""
        if not self.posted:
            raise ValidationError("Only a posted payment can be voided.")
        if self.journal_entry.reversed_by.exists():
            raise ValidationError("This payment has already been voided.")
        return self.journal_entry.create_reversal(
            memo=memo or f"Void of payment {self.number}"
        )

    def is_voided(self):
        return bool(self.journal_entry_id) and self.journal_entry.reversed_by.exists()
