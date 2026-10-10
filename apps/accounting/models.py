from decimal import ROUND_HALF_UP, Decimal

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

UDYAM = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")

from apps.core.models import (
    AuditModel,
    Country,
    Currency,
    DocumentSequence,
    Party,
    serialised,
    to_date,
)

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
    holds_money = models.BooleanField(
        default=False,
        help_text="A bank, cash box, card or overdraft: what payments, claims and challans are paid "
                  "from or into. Nothing else is offered as one.")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        self._check_tree()
        self._check_money()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_tree()
        self._check_money()
        super().save(*args, **kwargs)

    def _check_money(self):
        """
        Money is an asset or a liability (a card or an overdraft is owed).
        Once anything is posted to an account it stays what it was: the
        payments through it, and the documents owed on it, were read by it.
        """
        from .money import purposes_kept_on

        if self.holds_money and self.account_type not in (AccountType.ASSET, AccountType.LIABILITY):
            raise ValidationError({"holds_money": [
                "A bank, cash or card account is an asset, or a liability (a card, an overdraft)."]})
        if self.pk is None:
            return
        before = Account.objects.filter(pk=self.pk).values_list("holds_money", flat=True).first()
        if before is None or before == self.holds_money:
            return
        if JournalLine.objects.filter(account=self, entry__posted=True).exists():
            was = "a bank, cash or card account" if before else "not one"
            raise ValidationError({"holds_money": [
                f"{self} has posted entries as {was}. What it was is what they were read as; "
                "open a new account instead."]})
        kept = purposes_kept_on(self) if self.holds_money else ""
        if kept:
            raise ValidationError({"holds_money": [
                f"{self} is the {kept}; money does not move through it."]})

    def _check_tree(self):
        # Each beside the field it is about, so the form that made the
        # choice shows it there and not only in a passing notice.
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": ["An account cannot be its own parent."]})
        if self.parent_id and self.parent.account_type != self.account_type:
            raise ValidationError({"parent": ["A sub-account must have the same account_type as its parent."]})
        # Its own parent at one remove is still its own parent: the chart
        # would have no top, and every report that walks it would not end.
        # The loop closes on this account, read back; the one chosen is
        # what is already under it.
        seen, above = {self.pk}, self.parent
        while above is not None and self.pk is not None:
            if above.pk in seen:
                raise ValidationError({"parent": [
                    f"{self.parent} is already under {self}; it cannot also be above it."]})
            seen.add(above.pk)
            above = above.parent
        if self.pk:
            before = Account.objects.filter(pk=self.pk).values_list("account_type", flat=True).first()
            if before and before != self.account_type:
                if self.children.exclude(account_type=self.account_type).exists():
                    raise ValidationError({"account_type": [
                        "Its sub-accounts are of the old type; change them first."]})
                if JournalLine.objects.filter(account=self, entry__posted=True).exists():
                    raise ValidationError({"account_type": [
                        f"{self} has posted entries, reported as {before}. Changing its type would "
                        "move them between the statements; open a new account instead."]})


class AccountingPeriod(AuditModel):
    """
    A span of time the books have been reported for, and may be closed
    against.

    Financial statements that can change after they are issued are not
    statements. A close is the only thing standing between a signed-off
    month and somebody back-dating an invoice into it.
    """

    name = models.CharField(max_length=64)
    start_date = models.DateField()
    end_date = models.DateField()
    closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+", editable=False,
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-start_date"]
        permissions = [("close_accountingperiod", "Can close and reopen accounting periods")]
        constraints = [
            models.UniqueConstraint(
                fields=["start_date", "end_date"], name="one_period_per_span"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        self._check_span()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_span()
        super().save(*args, **kwargs)

    def _check_span(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A period cannot end before it starts.")
        if self.pk:
            before = AccountingPeriod.objects.filter(pk=self.pk).values(
                "start_date", "end_date", "closed").first()
            if before and before["closed"] and self.closed and (
                    before["start_date"], before["end_date"]) != (self.start_date, self.end_date):
                raise ValidationError(f"{self} is closed; reopen it before moving its dates.")
        overlapping = AccountingPeriod.objects.filter(
            start_date__lte=self.end_date, end_date__gte=self.start_date
        ).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError(
                f"This overlaps {overlapping.first()}. Periods that overlap would let one "
                "close while the other stays open, which locks nothing."
            )

    def covers(self, on_date):
        on_date = to_date(on_date)
        return self.start_date <= on_date <= self.end_date

    def delete(self, *args, **kwargs):
        # Deleting a closed period would unlock its months as a side
        # effect of tidying up; unlocking is a decision, made by reopening.
        if self.closed:
            raise ValidationError(f"{self} is closed; reopen it before deleting it.")
        super().delete(*args, **kwargs)

    @classmethod
    def blocking(cls, on_date, lock=False):
        """
        The closed period covering this date, if any. `lock`: the periods
        covering it held until the caller's transaction ends, so a close
        cannot land between a posting's check and its commit.
        """
        on_date = to_date(on_date)
        if on_date is None:
            return None
        covering = cls.objects.filter(start_date__lte=on_date, end_date__gte=on_date)
        if lock:
            covering = covering.select_for_update()
        return next((period for period in covering.order_by("pk") if period.closed), None)

    @classmethod
    def refuse_closed(cls, on_date, doing):
        """
        Refuse `doing` on a day a closed period covers: the one period check.

        JournalEntry.post() asks it of every entry. A document that posts no
        entry but is reported to the government all the same (a job-work
        challan and the losses on it, on ITC-04) asks it itself, as it is
        issued and as it is withdrawn: nothing else would, and the closed
        half-year's return would change under it. `doing` says what is
        refused, "JWC-2026-00001 is not issued on 2026-05-15".
        """
        blocking = cls.blocking(on_date, lock=True)
        if blocking is not None:
            raise ValidationError(
                f"{blocking} is closed: {doing}. Nothing further is dated into it; "
                "date it in an open period, or reopen that one."
            )

    @serialised("closed")
    def close(self, by=None, note=""):
        if self.closed:
            raise ValidationError("This period is already closed.")
        self.closed = True
        self.closed_at = timezone.now()
        self.closed_by = by
        self.note = note[:255] or self.note
        self.save(update_fields=["closed", "closed_at", "closed_by", "note", "updated_at"])

    @serialised("closed")
    def reopen(self, by=None, note=""):
        """
        Unlock a closed period.

        Deliberately possible and deliberately permissioned: books do get
        reopened, and a system that makes it impossible gets worked around
        by back-dating into the next period instead, which is worse.
        """
        if not self.closed:
            raise ValidationError("This period is not closed.")
        self.closed = False
        self.closed_at = None
        self.closed_by = by
        self.note = note[:255] or self.note
        self.save(update_fields=["closed", "closed_at", "closed_by", "note", "updated_at"])


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
    recurring_journal = models.ForeignKey(
        "accounting.RecurringJournal", null=True, blank=True, on_delete=models.PROTECT, related_name="entries",
        help_text="The schedule this entry was taken from (recurring.py), if any.",
    )

    class Meta:
        verbose_name_plural = "journal entries"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date", "id"], name="journal_entry_by_date")]
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
        if self.posted or self._was_posted_in_db():
            raise ValidationError(
                "Posted journal entries cannot be deleted. Create a reversing entry instead."
            )
        super().delete(*args, **kwargs)

    def recorded_by(self):
        """
        The document that posted this entry and keeps it, if any: it is
        corrected there, by a credit note, a void or a return. Reversed
        from the journal, an invoice still read as owed while receivables
        said nothing was, and a payment could no longer be voided.

        Asked of every model that points at an entry, so a document added
        later is covered the day it keeps its entry.
        """
        for relation in self._meta.get_fields(include_hidden=True):
            if not relation.auto_created or relation.concrete or relation.related_model in (JournalEntry, JournalLine):
                continue
            if relation.related_model._meta.auto_created:
                continue  # a many-to-many's own table: the document is asked through the relation itself
            found = relation.related_model._base_manager.filter(**{relation.field.name: self}).first()
            if found is not None:
                return found
        return None

    @serialised("posted")
    def reverse_by_hand(self, memo=""):
        """A person's reversal, from the journal: only of an entry no document keeps, and never of a reversal."""
        document = self.recorded_by()
        if document is not None:
            raise ValidationError(
                f"JE-{self.pk} was posted by {document._meta.verbose_name} {document}: correct it there "
                "(a credit note, a void, a return), not by reversing its entry."
            )
        # As the journal screen already offers it. A document corrected twice keeps only its
        # latest reversal, so the earlier one belongs to nothing; reversed by hand it put back
        # a payment, a close or a disposal its document had taken back.
        if self.reverses_id is not None:
            raise ValidationError(
                f"JE-{self.pk} reverses JE-{self.reverses_id}. To stand again, JE-{self.reverses_id} is "
                "posted again as an entry of its own, not by reversing its reversal."
            )
        return self.create_reversal(memo=memo)

    def total_debit(self):
        return self.lines.aggregate(total=Sum("debit"))["total"] or Decimal("0")

    def total_credit(self):
        return self.lines.aggregate(total=Sum("credit"))["total"] or Decimal("0")

    def is_balanced(self):
        debit = self.total_debit()
        return debit > 0 and debit == self.total_credit()

    @serialised("posted")
    def post(self):
        if self.posted:
            raise ValidationError("This journal entry is already posted.")
        # The one chokepoint every module posts through, so the period
        # lock only has to be enforced here. A guard per document type
        # would be six guards, and the seventh would be forgotten. What
        # posts no entry asks the same rule itself (refuse_closed).
        AccountingPeriod.refuse_closed(self.date, f"an entry is not posted on {to_date(self.date)}")
        if not self.is_balanced():
            raise ValidationError(
                f"Cannot post an unbalanced entry (debit={self.total_debit()}, "
                f"credit={self.total_credit()})."
            )
        self.posted = True
        self.posted_at = timezone.now()
        super(JournalEntry, self).save(update_fields=["posted", "posted_at", "updated_at"])

    @serialised("posted")
    def create_reversal(self, entry_date=None, memo=""):
        if not self.posted:
            raise ValidationError("Only a posted journal entry can be reversed.")
        # Nothing asked before: reversing an entry twice through the API
        # took it back twice, and a second click was all it needed.
        # Asked of the database after the lock, not of self.reversed_by: a
        # list that prefetched the reversals answers from what it read
        # before the lock, and a reversal committed since would be missed.
        standing = list(JournalEntry.objects.filter(reverses=self).values_list("pk", flat=True))
        if standing:
            raise ValidationError(
                f"JE-{self.pk} has already been reversed by "
                f"{', '.join(f'JE-{pk}' for pk in standing)}."
            )
        reversal = JournalEntry.objects.create(
            date=entry_date or timezone.localdate(),
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
                cost_centre=line.cost_centre,
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
    cost_centre = models.ForeignKey(
        "accounting.CostCentre", null=True, blank=True, on_delete=models.PROTECT, related_name="lines",
        help_text="The centre that incurred a cost, stamped when the line was posted (analytic.py).",
    )

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

    def _on_a_posted_entry(self):
        """Where it is going and where it has been, both asked of the database: moved out of a
        posted entry into a draft, a line left an invoice's entry unbalanced and its amount off the
        ledger."""
        return (bool(self.entry_id) and JournalEntry.objects.filter(pk=self.entry_id, posted=True).exists()) or (
            bool(self.pk) and JournalLine.objects.filter(pk=self.pk, entry__posted=True).exists())

    def save(self, *args, **kwargs):
        if self._on_a_posted_entry():
            raise ValidationError(
                "Cannot modify a line on a posted journal entry. Create a reversing entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._on_a_posted_entry():
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
    gst_head = models.CharField(
        max_length=8, blank=True,
        choices=[
            ("cgst", "Central tax (CGST)"),
            ("sgst", "State or union territory tax (SGST/UTGST)"),
            ("igst", "Integrated tax (IGST)"),
            ("cess", "Compensation cess"),
            ("other", "Not GST"),
        ],
        help_text="Which column of a GST return this tax is reported in. "
                  "Blank is refused on a document posted under GST: a return "
                  "cannot put an unclassified tax anywhere.",
    )
    reverse_charge = models.BooleanField(
        default=False,
        help_text="Paid by the company rather than charged by the vendor: freight from a goods "
                  "transport agency, say. Adds nothing to what the vendor is owed; the company owes "
                  "it to the government and claims it back as credit.",
    )
    reverse_charge_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Reverse charge: where the tax the company owes on the vendor's supply waits to be "
                  "paid in cash.",
    )

    class Meta:
        verbose_name_plural = "taxes"
        ordering = ["sequence", "code"]
        constraints = [models.CheckConstraint(check=Q(rate__gte=0), name="tax_rate_not_negative")]

    def __str__(self):
        if self.computation == TaxComputation.FIXED:
            return f"{self.name} ({self.rate}/unit)"
        return f"{self.name} ({self.rate}%)"

    def clean(self):
        self._check_accounts()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_accounts()
        from .money import refuse_money_kept_as, settings_fields

        refuse_money_kept_as(self, *settings_fields(Tax))
        super().save(*args, **kwargs)

    def _check_accounts(self):
        if self.scope in (TaxScope.SALES, TaxScope.BOTH) and not self.collected_account_id:
            raise ValidationError("A sales tax needs a collected_account to post to.")
        if self.scope in (TaxScope.PURCHASE, TaxScope.BOTH) and not self.paid_account_id:
            raise ValidationError("A purchase tax needs a paid_account to post to.")
        if self.price_included and self.computation == TaxComputation.FIXED and self.rate < 0:
            raise ValidationError("A fixed included tax cannot be negative.")
        if self.reverse_charge:
            if self.scope != TaxScope.PURCHASE:
                raise ValidationError({"reverse_charge": "Reverse charge is on what the company buys; the "
                                                         "tax's scope is purchases."})
            if not self.reverse_charge_account_id:
                raise ValidationError({"reverse_charge_account": "Say where the tax the company owes on it "
                                                                 "is owed."})
            if self.price_included:
                raise ValidationError({"price_included": "The vendor's price holds no tax it does not charge."})
        if self.pk and self._used():
            before = Tax.objects.get(pk=self.pk)
            if before.reverse_charge != self.reverse_charge:
                raise ValidationError({"reverse_charge": "Posted documents bore this tax as they found it; a "
                                                         "tax charged the other way is a new tax."})

    def _used(self):
        """Whether any posted line recorded this tax."""
        from django.apps import apps

        from .mixins import RecordedLineTax

        return any(model._base_manager.filter(tax=self).exists() for model in apps.get_models()
                   if issubclass(model, RecordedLineTax))

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
    hsn_code = models.CharField(
        max_length=8, blank=True,
        help_text="SAC for a service charge — 9965 for goods transport, say.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from .gst import validate_hsn

        self.hsn_code = validate_hsn(self.hsn_code)
        super().save(*args, **kwargs)

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
        self._check_target()

    def save(self, *args, **kwargs):
        # In save() as well: ModelSerializer never calls clean(), and the
        # office writes through the API. Only the admin ever asked this.
        self._check_target()
        super().save(*args, **kwargs)

    def _check_target(self):
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
    gstin = models.CharField(
        max_length=15, blank=True,
        help_text="The party's GST registration, checked character by "
                  "character. Its first two digits are its state.",
    )
    gst_state = models.CharField(
        max_length=2, blank=True,
        help_text="Where the party is, as a GST state code. Read off the "
                  "GSTIN when there is one; typed only for an unregistered "
                  "party, whose state still decides which taxes a supply to "
                  "it bears.",
    )
    gst_registration = models.CharField(
        max_length=16, blank=True,
        choices=[
            ("regular", "Registered, regular"),
            ("composition", "Registered, composition"),
            ("sez", "Special economic zone"),
            ("unregistered", "Unregistered"),
            ("overseas", "Overseas"),
        ],
    )
    pan = models.CharField(
        max_length=10, blank=True,
        help_text="The party's PAN. Read off the GSTIN when there is one; without either, "
                  "tax is deducted at the no-PAN rate.",
    )
    tds_section = models.ForeignKey(
        "accounting.TdsSection", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="The section its bills are deducted under by default.",
    )
    tds_rate_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
        help_text="A rate of its own under that section: an individual's 1% under 194C, or a "
                  "lower-deduction certificate (section 197). Empty for the section's rate.",
    )
    tds_rate_reference = models.CharField(
        max_length=64, blank=True, help_text="The certificate or reason the rate of its own rests on.",
    )
    msme_category = models.CharField(
        max_length=8, blank=True, choices=[("micro", "Micro"), ("small", "Small"), ("medium", "Medium")],
        help_text="As its Udyam registration says. A micro or small vendor is paid within 45 days, or "
                  "the expense waits for the payment before it is deducted (section 43B(h)).",
    )
    udyam_number = models.CharField(max_length=19, blank=True, help_text="UDYAM-XX-00-0000000")

    def __str__(self):
        return f"Tax profile for {self.party}"

    def clean(self):
        self._check()

    def save(self, *args, **kwargs):
        # On save as well: profiles are made in code by every import and
        # fixture, and `clean()` is not called for those — which is how
        # an exempt party with no exemption on file got through before.
        self._check()
        super().save(*args, **kwargs)

    def _check(self):
        from .gst import STATES, validate_gstin

        if self.tax_exempt and not self.exemption_reference:
            raise ValidationError("An exempt party needs an exemption reference on file.")
        if self.gstin:
            self.gstin = validate_gstin(self.gstin)
            if self.gst_state and self.gst_state != self.gstin[:2]:
                raise ValidationError(
                    f"{self.gstin} is registered in {STATES[self.gstin[:2]]}, "
                    f"not {STATES.get(self.gst_state, self.gst_state)}."
                )
            self.gst_state = self.gstin[:2]
            if not self.gst_registration or self.gst_registration == "unregistered":
                self.gst_registration = "regular"
        elif self.gst_state and self.gst_state not in STATES:
            raise ValidationError(f"{self.gst_state} is not a GST state code.")
        self._check_tds()
        self._check_msme()

    def _check_tds(self):
        from .tds import validate_pan

        if self.pan:
            self.pan = validate_pan(self.pan)
            if self.gstin and self.gstin[2:12] != self.pan:
                raise ValidationError({"pan": f"The GSTIN {self.gstin} carries the PAN {self.gstin[2:12]}, "
                                              f"not {self.pan}."})
        if self.tds_rate_percent is not None:
            if self.tds_section_id is None:
                raise ValidationError({"tds_rate_percent": "A rate of its own is under a section; name it."})
            if not self.tds_rate_reference:
                raise ValidationError({"tds_rate_reference": "Say what the rate rests on: the certificate "
                                                             "number, or the deductee being an individual."})
            if not self.pan_on_file():
                raise ValidationError({"tds_rate_percent": "Without a PAN the no-PAN rate applies whatever "
                                                           "else is on file."})

    def _check_msme(self):
        if self.udyam_number:
            self.udyam_number = self.udyam_number.strip().upper()
            if not UDYAM.match(self.udyam_number):
                raise ValidationError({"udyam_number": f"{self.udyam_number!r} is not a Udyam number: "
                                                       "UDYAM-, the state, two digits, seven digits."})
        if self.msme_category and not self.udyam_number:
            raise ValidationError({"udyam_number": "An MSME category rests on its Udyam registration; give the "
                                                   "number."})

    def pan_on_file(self):
        """The PAN given, or the one inside the GSTIN."""
        return self.pan or (self.gstin[2:12] if self.gstin else "")

    def place_of_supply(self):
        from .gst import OVERSEAS_PLACE

        if self.gst_registration == "overseas":
            return OVERSEAS_PLACE
        return self.gst_state

    def applicable_taxes(self, taxes, place=None):
        """`place`: where the supply is, when its document says (gst.place_of_supply); else the party's state."""
        from .gst import gst_taxes

        if self.tax_exempt:
            return []
        if self.fiscal_position_id:
            # What the party is (an exporter under bond, a unit in a
            # special economic zone) outranks where it is.
            return self.fiscal_position.map_taxes(taxes)
        return gst_taxes(self, taxes, place=place)


class PaymentDirection(models.TextChoices):
    RECEIPT = "receipt", "Receipt (money in)"
    DISBURSEMENT = "disbursement", "Disbursement (money out)"


# What a trading module refuses of a payment with one of its parties
# (a vendor whose payments are held), registered when it loads:
# `check(payment) -> the reason in words, or None`. Accounting imports
# neither module, so each says its own.
PAYMENT_CHECKS = []


def register_payment_check(check):
    if check not in PAYMENT_CHECKS:
        PAYMENT_CHECKS.append(check)


# What else a void takes back, asked of the side that knows which documents the money settled
# (accounting imports neither): a discount for paying early, once the payment is returned.
VOID_FOLLOW_UPS = []


def register_void_follow_up(follow_up):
    if follow_up not in VOID_FOLLOW_UPS:
        VOID_FOLLOW_UPS.append(follow_up)


# Money a document moves through a bank without a Payment: a TDS challan paid
# over, an expense claim paid out. Each module that books one says which
# entries are its documents' (the payment, and the reversal that voided it),
# without accounting importing it. A statement line is then matched to the
# entry rather than posted a second time, and a reconciliation counts the entry
# as not yet presented until a line is. A challan's line could only be posted:
# the books then said -1,400 for 700 paid.
BANK_MOVEMENTS = []


def register_bank_movements(entries):
    """entries() -> a JournalEntry queryset: what the module's documents posted, wherever the money went."""
    if entries not in BANK_MOVEMENTS:
        BANK_MOVEMENTS.append(entries)


def bank_movements(bank_account):
    """The posted entries a document moved through `bank_account`, by what each module registered."""
    if not BANK_MOVEMENTS:
        return JournalEntry.objects.none()
    registered = Q(pk__in=BANK_MOVEMENTS[0]().values("pk"))
    for entries in BANK_MOVEMENTS[1:]:
        registered |= Q(pk__in=entries().values("pk"))
    return JournalEntry.objects.filter(registered, posted=True, lines__account=bank_account).distinct()


def moved_through(entry, bank_account):
    """What `entry` moved through `bank_account`, signed as the bank sees it: money in is positive."""
    rows = entry.lines.filter(account=bank_account).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    # At the paisa on either database: SQLite sums to whatever places the figures had.
    return round_money((rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0")))


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
        indexes = [models.Index(fields=["payment_date", "id"], name="payment_by_date")]
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
        if self._state.adding:
            from .defaults import default_account

            if not self.bank_account_id:
                self.bank_account = default_account("bank", "bank_account")
            if not self.counterpart_account_id:
                # Money in settles what a customer owes; money out, what is
                # owed to a vendor.
                purpose = "receivable" if self.is_receipt() else "payable"
                self.counterpart_account = default_account(purpose, "counterpart_account")
        from .money import refuse_as_money_account

        refuse_as_money_account(self.bank_account)
        if self.bank_account_id == self.counterpart_account_id:
            raise ValidationError({"bank_account": [
                f"{self.bank_account} is the account this payment settles as well; money moves "
                "between two accounts, not out of one and into it again."]})
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

    @serialised("posted")
    def post(self):
        if self.posted:
            raise ValidationError("This payment is already posted.")
        from .money import refuse_as_money_account

        # Asked again as the money moves: a draft outlives the account it named being closed.
        refuse_as_money_account(Account.objects.get(pk=self.bank_account_id))
        for check in PAYMENT_CHECKS:
            said = check(self)
            if said:
                raise ValidationError(said)

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

    @serialised("posted", "voided_entry")
    def void(self, memo="", on_date=None):
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
        # The entry's reversals asked of the database, not of a cache filled
        # before the lock (create_reversal asks again, under its own lock).
        if self.voided_entry_id or JournalEntry.objects.filter(reverses_id=self.journal_entry_id).exists():
            raise ValidationError("This payment has already been voided.")

        # On the day the bank returned it, not the day someone keyed it in: dated today, a cheque
        # returned on the 3rd and voided next month left that month's books holding the money, and
        # its statement could not be reconciled.
        on_date = to_date(on_date) or timezone.localdate()
        if on_date < to_date(self.payment_date):
            raise ValidationError("A payment is not voided before the day it was made.")
        # Ahead of the ledger, the documents would read it unpaid from now and the books paid until then.
        if on_date > timezone.localdate():
            raise ValidationError("A payment is voided on the day the bank returned it, and that day has not come.")
        entry = self.journal_entry.create_reversal(
            entry_date=on_date, memo=memo or f"Void of payment {self.number}"
        )
        self.voided_entry = entry
        super(Payment, self).save(update_fields=["voided_entry", "updated_at"])
        for follow_up in VOID_FOLLOW_UPS:
            follow_up(self, on_date)
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

    def render_pdf(self):
        """Remittance advice for money out, a receipt for money in."""
        from .documents import render_payment_pdf

        return render_payment_pdf(self)

    def email_to_party(self, to=None, subject=None, body=None, user=None):
        """The advice or the receipt to the party, once posted and standing. Returns the address used."""
        from apps.core.mail import send_document

        if not self.posted:
            raise ValidationError("Only a posted payment is advised; post it first.")
        if self.is_voided():
            raise ValidationError("This payment is void; nothing of it is sent.")
        what = "Remittance advice" if self.direction == PaymentDirection.DISBURSEMENT else "Payment receipt"
        return send_document(self, self.party, what, to=to, subject=subject, body=body, user=user)


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
        self._check_span()

    def _check_span(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("A statement cannot end before it starts.")

    def save(self, *args, **kwargs):
        if self.pk and BankStatement.objects.filter(pk=self.pk, closed=True).exists():
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        self._check_span()
        stored = BankStatement.objects.filter(pk=self.pk).values_list("bank_account_id", flat=True).first()
        if stored != self.bank_account_id:
            from .money import refuse_as_money_account

            # A bank's statement is of an account money moves through.
            refuse_as_money_account(self.bank_account)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Its lines go with it, and a line posted to an account would leave
        # its entry in the ledger with nothing to say why.
        if self.closed:
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        if self.lines.filter(journal_entry__isnull=False).exists():
            raise ValidationError("Lines on this statement were posted to the ledger; reverse them first.")
        super().delete(*args, **kwargs)

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

    def unpresented_entries(self):
        """
        [(entry, amount)]: money a document moved through this account by the
        statement's end (bank_movements) that no statement line is matched to.
        A payment that was taken back is two such entries, and they net to
        nothing until the bank shows either.
        """
        # Presented by the statement's end: a line of a later statement leaves it unpresented at this one's.
        matched = BankStatementLine.objects.filter(booked_entry__isnull=False, date__lte=self.end_date).values(
            "booked_entry")
        entries = bank_movements(self.bank_account).filter(date__lte=self.end_date).exclude(pk__in=matched)
        return [(entry, moved_through(entry, self.bank_account)) for entry in entries.order_by("date", "pk")]

    def reconciliation(self):
        """
        Books to bank, with every reconciling item named.

        Reconciled when the ledger balance plus the payments the bank has
        not seen yet equals what the bank says it is holding.
        """
        unpresented = self.unpresented()
        unpresented_entries = self.unpresented_entries()
        adjustment = sum((payment.signed_base_amount() for payment in unpresented), Decimal("0")) + sum(
            (amount for _, amount in unpresented_entries), Decimal("0"))
        ledger = self.ledger_balance()
        return {
            "statement": self,
            "ledger_balance": ledger,
            "statement_balance": self.closing_balance,
            "unpresented": unpresented,
            "unpresented_entries": unpresented_entries,
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

    @serialised("closed")
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

    @serialised("closed")
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
                (line.match, payment) for payment in self.unpresented()
                if payment.signed_base_amount() == line.amount
                and abs((to_date(payment.payment_date) - to_date(line.date)).days)
                <= tolerance_days
            ] + [
                (line.match_entry, entry) for entry, amount in self.unpresented_entries()
                if amount == line.amount and abs((to_date(entry.date) - to_date(line.date)).days) <= tolerance_days
            ]
            if len(candidates) == 1:
                match, what = candidates[0]
                match(what)
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
    returned_payment = models.OneToOneField(
        Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="returned_line",
        help_text="The payment the bank took back on this line: a cheque returned unpaid, a transfer recalled.",
    )
    booked_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="Money a document already booked through the bank, not as a payment: a TDS challan, an "
                  "expense claim paid out. Matched to it, not posted again.",
    )

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(check=~Q(amount=0), name="statement_line_amount_not_zero"),
            models.UniqueConstraint(fields=["payment"], name="one_statement_line_per_payment"),
            models.UniqueConstraint(fields=["booked_entry"], name="one_statement_line_per_booked_entry"),
        ]

    def __str__(self):
        return f"{self.date:%d %b %Y} {self.description} {self.amount}"

    def is_resolved(self):
        return bool(self.payment_id or self.journal_entry_id or self.returned_payment_id or self.booked_entry_id)

    def save(self, *args, **kwargs):
        if self.statement_id and BankStatement.objects.filter(
            pk=self.statement_id, closed=True
        ).exists():
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        if not self._state.adding:
            # Matching checked the amount against the payment, and posting
            # booked it; changed afterwards, the line says one thing and
            # what explains it another.
            before = BankStatementLine.objects.get(pk=self.pk)
            moved = (before.amount, before.date, before.statement_id) != (
                self.amount, to_date(self.date), self.statement_id)
            if moved and before.is_resolved():
                raise ValidationError("This line is explained already; unmatch or reverse it before "
                                      "changing it.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.statement.closed:
            raise ValidationError("This statement is closed. Reopen it to make changes.")
        if self.journal_entry_id:
            raise ValidationError("This line was posted to the ledger; reverse it first.")
        super().delete(*args, **kwargs)

    @serialised("payment", "journal_entry", "returned_payment", "booked_entry")
    def match(self, payment):
        """Say this line is that payment."""
        if self.is_resolved():
            raise ValidationError("This line is already explained.")
        if not payment.posted:
            raise ValidationError("Only a posted payment can be matched.")
        if payment.bank_account_id != self.statement.bank_account_id:
            raise ValidationError("That payment went through a different bank account.")
        if payment.is_voided():
            returned = BankStatementLine.objects.filter(returned_payment=payment).select_related("statement").first()
            # The bank taking it back: a cheque returned unpaid, the payment's own amount the other
            # way. Matched nowhere, a bounce reported after its cheque's statement closed could
            # never be explained: posted instead, it took the money off the books a second time.
            if self.amount == -payment.signed_base_amount():
                if returned is not None:
                    raise ValidationError(f"The bank's return of that payment is already matched, on {returned.statement}.")
                if to_date(self.date) < to_date(payment.payment_date):
                    raise ValidationError("A payment is not returned before it was made.")
                self.returned_payment = payment
                self.save(update_fields=["returned_payment", "updated_at"])
                return self
            # Voided because it never happened, it never reached the bank; one the bank returned did.
            if returned is None:
                raise ValidationError("A voided payment never reached the bank, unless the bank returned it: "
                                      "match the line that returned it first.")
        elsewhere = BankStatementLine.objects.filter(payment=payment).select_related("statement").first()
        if elsewhere is not None:
            raise ValidationError(f"That payment is already matched, on the statement {elsewhere.statement}.")
        if payment.signed_base_amount() != self.amount:
            raise ValidationError(
                f"The bank shows {self.amount} and the payment is "
                f"{payment.signed_base_amount()}. Matching them would hide the difference."
            )
        self.payment = payment
        self.save(update_fields=["payment", "updated_at"])
        return self

    @serialised("payment", "journal_entry", "returned_payment", "booked_entry")
    def match_entry(self, entry):
        """
        Say this line is money a document already booked through the bank (a
        TDS challan, an expense claim paid out), as match() says it of a
        payment. Explained by posting instead, the books took it twice.
        """
        if self.is_resolved():
            raise ValidationError("This line is already explained.")
        bank = self.statement.bank_account
        if not bank_movements(bank).filter(pk=entry.pk).exists():
            raise ValidationError(f"JE-{entry.pk} is not money a document moved through {bank}: match a "
                                  "payment as a payment, and post what nothing booked.")
        elsewhere = BankStatementLine.objects.filter(booked_entry=entry).select_related("statement").first()
        if elsewhere is not None:
            raise ValidationError(f"JE-{entry.pk} is already matched, on the statement {elsewhere.statement}.")
        moved = moved_through(entry, bank)
        if moved != self.amount:
            raise ValidationError(f"The bank shows {self.amount} and JE-{entry.pk} moved {moved}. Matching them "
                                  "would hide the difference.")
        self.booked_entry = entry
        self.save(update_fields=["booked_entry", "updated_at"])
        return self

    @serialised("payment", "journal_entry", "returned_payment", "booked_entry")
    def unmatch(self):
        if self.journal_entry_id:
            raise ValidationError("This line was posted, not matched. Reverse it instead.")
        if self.booked_entry_id:
            self.booked_entry = None
            self.save(update_fields=["booked_entry", "updated_at"])
            return
        if not (self.payment_id or self.returned_payment_id):
            raise ValidationError("This line is not matched.")
        # The return comes off after the payment's own line, as it went on before it: alone, that
        # line says a voided payment reached the bank and nothing says the bank gave it back.
        if self.returned_payment_id and BankStatementLine.objects.filter(payment_id=self.returned_payment_id).exists():
            raise ValidationError("That payment's own line is still matched to it; unmatch that line first.")
        self.payment = self.returned_payment = None
        self.save(update_fields=["payment", "returned_payment", "updated_at"])

    @serialised("payment", "journal_entry", "returned_payment", "booked_entry")
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
        # A line a document already booked (a challan, a claim) is matched to it, not
        # posted a second time: the bank and the account it went to would carry it twice (O164).
        matched = BankStatementLine.objects.filter(booked_entry__isnull=False).values("booked_entry")
        for entry in bank_movements(bank).filter(date=to_date(self.date)).exclude(pk__in=matched):
            if moved_through(entry, bank) == self.amount:
                raise ValidationError(
                    f"JE-{entry.pk} ({entry.reference or entry.memo}) already moved {self.amount} through {bank} on "
                    f"{entry.date} and no line is matched to it: match this line to it rather than post it again.")
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

    @serialised("payment", "journal_entry", "returned_payment", "booked_entry")
    def reverse_posting(self, on_date=None):
        """
        Take back a line posted to the wrong account. `unmatch` said to
        reverse it and nothing could: the entry stayed, and the line read
        as explained by it whatever was done to the entry by hand.
        """
        if not self.journal_entry_id:
            raise ValidationError("This line was not posted to an account.")
        # On the posting's own date by default: dated later, the bank
        # account to the statement's end still holds the wrong posting, and
        # once the line is posted again it holds the movement twice.
        reversal = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or self.journal_entry.date,
            memo=f"Reversed: {self.journal_entry.memo}",
        )
        self.journal_entry = None
        self.save(update_fields=["journal_entry", "updated_at"])
        return reversal


from .gst import GstSettings  # noqa: E402,F401
from .analytic import CostCentre  # noqa: E402,F401
from .budgets import Budget, BudgetLine  # noqa: E402,F401
from .recurring import RecurringJournal, RecurringJournalLine  # noqa: E402,F401
from .tds import TdsSection  # noqa: E402,F401
