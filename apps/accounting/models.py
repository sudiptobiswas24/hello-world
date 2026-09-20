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
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="The reversing entry, set when this payment is voided.",
    )

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
    @transaction.atomic
    def void(self, memo=""):
        """
        Reverse a posted payment — a bounced cheque, a recalled transfer.

        Voiding records the fact on the payment rather than deleting its
        allocations. The allocations are history: they say what this money
        was once believed to settle, and that belief is worth keeping.
        What changes is that every balance stops counting them, which the
        readers do by ignoring allocations whose payment has been voided.

        This used to only post the reversal. The ledger was right and every
        document was wrong: a bounced receipt left its invoice reading as
        paid, so dunning never chased it, aging never showed it and the
        customer's credit limit was quietly freed — while the ledger
        insisted the money was still owed.
        """
        if not self.posted:
            raise ValidationError("Only a posted payment can be voided.")
        if self.voided_entry_id or self.journal_entry.reversed_by.exists():
            raise ValidationError("This payment has already been voided.")

        entry = self.journal_entry.create_reversal(
            memo=memo or f"Void of payment {self.number}"
        )
        self.voided_entry = entry
        super(Payment, self).save(update_fields=["voided_entry", "updated_at"])
        return entry

    def base_amount(self):
        """What this payment moved through the bank in base currency."""
        return round_money(self.amount * (self.exchange_rate or Decimal("1")))

    def signed_base_amount(self):
        """Positive for money in, negative for money out — as a bank sees it."""
        amount = self.base_amount()
        return amount if self.is_receipt() else -amount

    def is_reconciled(self):
        return self.statement_lines.exists()

    def is_voided(self):
        if self.voided_entry_id:
            return True
        return bool(self.journal_entry_id) and self.journal_entry.reversed_by.exists()


class BankStatement(AuditModel):
    """
    A period of a bank account as the bank reports it.

    Reconciliation is the control that proves the ledger matches reality.
    Everything else here derives one number from another inside the same
    system; this is the only place an outside source gets to disagree, and
    a books-to-bank difference nobody has explained is how both fraud and
    plain error stay invisible.
    """

    bank_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="statements",
        help_text="The cash or bank account this statement covers.",
    )
    reference = models.CharField(max_length=64, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2)
    closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-end_date", "-id"]
        permissions = [("close_bankstatement", "Can close a reconciled bank statement")]

    def __str__(self):
        return f"{self.bank_account.code} to {self.end_date:%d %b %Y}"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A statement cannot end before it starts.")

    def save(self, *args, **kwargs):
        if self.pk and BankStatement.objects.filter(pk=self.pk, closed=True).exists():
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        super().save(*args, **kwargs)

    def line_total(self):
        return sum((line.amount for line in self.lines.all()), Decimal("0"))

    def computed_closing(self):
        return self.opening_balance + self.line_total()

    def statement_difference(self):
        """
        Closing balance the bank states, less what its own lines come to.

        Non-zero means the statement was keyed in wrong — a missing line
        or a typo — and there is no point reconciling against it until
        that is fixed.
        """
        return self.closing_balance - self.computed_closing()

    def unresolved_lines(self):
        return [line for line in self.lines.all() if not line.is_resolved()]

    def ledger_balance(self):
        """What the books say this account holds at the statement's end."""
        rows = JournalLine.objects.filter(
            account=self.bank_account, entry__posted=True, entry__date__lte=self.end_date
        ).aggregate(debit=models.Sum("debit"), credit=models.Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def unpresented(self):
        """
        Posted payments through this account that no statement line
        matches — cheques written but not yet cashed, mostly.

        These are the legitimate reason the books and the bank differ, and
        naming them is the difference between a reconciliation and a
        shrug.
        """
        matched = BankStatementLine.objects.filter(
            payment__isnull=False
        ).values_list("payment_id", flat=True)
        payments = Payment.objects.filter(
            bank_account=self.bank_account, posted=True,
            payment_date__lte=self.end_date, voided_entry__isnull=True,
        ).exclude(pk__in=matched)
        return list(payments)

    def reconciliation(self):
        """
        Books to bank, with every reconciling item named.

        Reconciled when the ledger balance plus the payments the bank has
        not seen yet equals what the bank says it is holding.
        """
        unpresented = self.unpresented()
        adjustment = sum((payment.signed_base_amount() for payment in unpresented), Decimal("0"))
        ledger = self.ledger_balance()
        return {
            "statement": self,
            "ledger_balance": ledger,
            "statement_balance": self.closing_balance,
            "unpresented": unpresented,
            "unpresented_total": adjustment,
            "unresolved_lines": self.unresolved_lines(),
            "statement_difference": self.statement_difference(),
            # The books, less what the bank has not seen, should be what
            # the bank says.
            "difference": (ledger - adjustment) - self.closing_balance,
        }

    def is_reconciled(self):
        report = self.reconciliation()
        return (
            report["difference"] == 0
            and report["statement_difference"] == 0
            and not report["unresolved_lines"]
        )

    @transaction.atomic
    def close(self):
        """
        Sign the statement off. Refused while anything is unexplained,
        because a reconciliation that closes over a difference is not a
        reconciliation.
        """
        if self.closed:
            raise ValidationError("This statement is already closed.")
        report = self.reconciliation()
        if report["statement_difference"]:
            raise ValidationError(
                f"The statement's own lines come to {self.computed_closing()}, not the "
                f"{self.closing_balance} it reports. Fix the statement before reconciling."
            )
        if report["unresolved_lines"]:
            raise ValidationError(
                f"{len(report['unresolved_lines'])} statement line(s) are still "
                "unexplained. Match them to a payment or post them to an account."
            )
        if report["difference"]:
            raise ValidationError(
                f"The books and the bank differ by {report['difference']} with nothing "
                "left to explain it."
            )
        self.closed = True
        self.closed_at = timezone.now()
        super(BankStatement, self).save(update_fields=["closed", "closed_at", "updated_at"])

    def reopen(self):
        if not self.closed:
            raise ValidationError("This statement is not closed.")
        self.closed = False
        self.closed_at = None
        super(BankStatement, self).save(update_fields=["closed", "closed_at", "updated_at"])

    def auto_match(self, tolerance_days=5):
        """
        Match the obvious ones: same signed amount, same account, a date
        close by, and the payment not already matched.

        Deliberately conservative. An automatic match that is wrong is
        worse than no match, because nobody looks at it again — so two
        candidates for the same line means neither is taken.
        """
        matched = []
        for line in self.lines.all():
            if line.is_resolved():
                continue
            candidates = [
                payment for payment in self.unpresented()
                if payment.signed_base_amount() == line.amount
                and abs((to_date(payment.payment_date) - to_date(line.date)).days)
                <= tolerance_days
            ]
            if len(candidates) == 1:
                line.match(candidates[0])
                matched.append(line)
        return matched


class BankStatementLine(AuditModel):
    """
    One movement as the bank reports it, signed the way a bank sees it:
    positive is money arriving.
    """

    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Positive for money in, negative for money out.",
    )
    payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="statement_lines",
        help_text="The payment this line is, once matched.",
    )
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
        help_text="Set when this line was posted directly — bank charges, interest.",
    )

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(check=~Q(amount=0), name="statement_line_amount_not_zero"),
            models.UniqueConstraint(fields=["payment"], name="one_statement_line_per_payment"),
        ]

    def __str__(self):
        return f"{self.date:%d %b %Y} {self.description} {self.amount}"

    def is_resolved(self):
        return bool(self.payment_id or self.journal_entry_id)

    def save(self, *args, **kwargs):
        if self.statement_id and BankStatement.objects.filter(
            pk=self.statement_id, closed=True
        ).exists():
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.statement.closed:
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        super().delete(*args, **kwargs)

    def match(self, payment):
        """Say this line is that payment."""
        if self.is_resolved():
            raise ValidationError("This line is already explained.")
        if not payment.posted:
            raise ValidationError("Only a posted payment can be matched.")
        if payment.is_voided():
            raise ValidationError("A voided payment never reached the bank.")
        if payment.bank_account_id != self.statement.bank_account_id:
            raise ValidationError("That payment went through a different bank account.")
        if payment.signed_base_amount() != self.amount:
            raise ValidationError(
                f"The bank shows {self.amount} and the payment is "
                f"{payment.signed_base_amount()}. Matching them would hide the difference."
            )
        self.payment = payment
        self.save(update_fields=["payment", "updated_at"])
        return self

    def unmatch(self):
        if self.journal_entry_id:
            raise ValidationError("This line was posted, not matched. Reverse it instead.")
        self.payment = None
        self.save(update_fields=["payment", "updated_at"])

    @transaction.atomic
    def post_to(self, account, party=None, memo=""):
        """
        Explain a line that is not a payment at all — a bank charge,
        interest, a direct debit nobody recorded — by posting it straight
        to an account.

        Without this, reconciliation stalls on the handful of lines the
        bank originates, and those are exactly the ones nobody would
        otherwise enter.
        """
        if self.is_resolved():
            raise ValidationError("This line is already explained.")
        bank = self.statement.bank_account
        memo = memo or self.description or f"Bank statement {self.statement}"
        entry = JournalEntry.objects.create(
            date=to_date(self.date), reference=self.reference, memo=memo
        )
        size = abs(self.amount)
        money_in = self.amount > 0
        JournalLine.objects.create(
            entry=entry, account=bank, party=party,
            debit=size if money_in else Decimal("0"),
            credit=Decimal("0") if money_in else size,
            description=memo,
        )
        JournalLine.objects.create(
            entry=entry, account=account, party=party,
            debit=Decimal("0") if money_in else size,
            credit=size if money_in else Decimal("0"),
            description=memo,
        )
        entry.post()
        self.journal_entry = entry
        self.save(update_fields=["journal_entry", "updated_at"])
        return entry
