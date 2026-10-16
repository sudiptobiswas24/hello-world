"""
Tax a customer deducted from what it paid: a cement buyer pays an invoice
short by its TDS, and the shortfall is not owed. It is a claim on the
government instead, to be matched against Form 26AS.

Dr TDS receivable / Cr Accounts receivable. It settles the invoice as a
payment would, and is reversed if the customer's return never shows it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import JournalEntry, JournalLine, TdsSection, round_money
from apps.core.models import AuditModel, Company, lock_rows, serialised, to_date


class CustomerTds(AuditModel):
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.PROTECT, related_name="tds_withheld")
    section = models.ForeignKey(TdsSection, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField(help_text="The day the customer deducted it, as its certificate says.")
    certificate = models.CharField(max_length=32, blank=True, help_text="The Form 16A number, once it comes.")
    confirmed_on = models.DateField(
        null=True, blank=True, help_text="When it was found in Form 26AS; until then it is only the "
                                         "customer's word.")
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False)
    reversed_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "customer TDS"
        verbose_name_plural = "customer TDS"
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="customer_tds_positive")]

    def __str__(self):
        return f"{self.section.code} {self.amount} on {self.invoice}"

    @serialised("reversed_entry", "confirmed_on")
    def reverse(self, on_date=None):
        """The customer never filed it: the invoice is owed that much again."""
        if self.reversed_entry_id:
            raise ValidationError("This has already been reversed.")
        if self.confirmed_on:
            raise ValidationError("Form 26AS shows it; clear the confirmation first if 26AS was revised.")
        self.reversed_entry = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or timezone.localdate(), memo=f"Reversed: {self.journal_entry.memo}")
        self.save(update_fields=["reversed_entry", "updated_at"])
        return self.reversed_entry

    @serialised("reversed_entry", "confirmed_on")
    def confirm(self, on_date=None, certificate=None):
        if self.reversed_entry_id:
            raise ValidationError("A reversed deduction is not confirmed.")
        self.confirmed_on = to_date(on_date) or timezone.localdate()
        if certificate:
            self.certificate = certificate
        self.save(update_fields=["confirmed_on", "certificate", "updated_at"])

    @serialised("reversed_entry", "confirmed_on")
    def unconfirm(self):
        self.confirmed_on = None
        self.save(update_fields=["confirmed_on", "updated_at"])


def record(invoice, section, amount, on_date=None, certificate=""):
    with transaction.atomic():
        lock_rows(invoice)
        if not invoice.posted:
            raise ValidationError("Only a posted invoice can have tax deducted from its payment.")
        if invoice.is_credit_note():
            raise ValidationError("A credit note is not paid; nothing is deducted from it.")
        if invoice.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("A customer abroad deducts no Indian TDS.")
        if section.receivable_account_id is None:
            raise ValidationError(f"{section} has no receivable account to claim the tax in.")
        amount = round_money(Decimal(amount))
        if amount <= 0:
            raise ValidationError("The tax deducted is more than nothing.")
        due = invoice.amount_due()
        if amount > due:
            raise ValidationError(f"Only {due} is left owing on {invoice.number}; the customer cannot have "
                                  f"deducted {amount} from it.")
        day = to_date(on_date) or timezone.localdate()
        memo = f"TDS {section.code} deducted by {invoice.customer} on {invoice.number}"
        entry = JournalEntry.objects.create(date=day, reference=invoice.number, memo=memo)
        JournalLine.objects.create(entry=entry, account=section.receivable_account, party=invoice.customer,
                                   debit=amount, description=memo)
        JournalLine.objects.create(entry=entry, account=invoice.receivable_account, party=invoice.customer,
                                   credit=amount, description=memo)
        entry.post()
        return CustomerTds.objects.create(invoice=invoice, section=section, amount=amount, date=day,
                                          certificate=certificate, journal_entry=entry)
