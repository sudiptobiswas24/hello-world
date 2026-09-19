from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import AuditModel, Currency, Party


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
