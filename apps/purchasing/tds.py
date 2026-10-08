"""
Tax the company deducts from what it owes a vendor, and pays over by
challan.

A deduction is made on a posted bill, under a section, at the vendor's
rate, on the bill's taxable value: Dr Payable / Cr TDS payable. It reduces
what the bill has due, so the payment is the net and the payable account
clears to nothing. A challan pays a month's deductions under one section
over to the government: Dr TDS payable / Cr Bank.

Both have their reverse here: a deduction is reversed until a challan pays
it, and a challan is voided, which frees what it paid.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from apps.accounting.models import Account, JournalEntry, JournalLine, TdsSection
from apps.accounting.tds import ThresholdMode, deduction_terms, financial_year
from apps.core.models import AuditModel, Company, lock_rows, serialised, to_date


def taxable_net(bill):
    """The bill's taxable value, less its posted debit notes'."""
    return bill.subtotal() - sum(
        (note.subtotal() for note in bill.debit_notes.all() if note.posted), Decimal("0"))


def _live(queryset):
    return queryset.filter(reversed_entry__isnull=True)


class TdsDeduction(AuditModel):
    bill = models.ForeignKey("purchasing.Bill", on_delete=models.PROTECT, related_name="tds_deductions")
    section = models.ForeignKey(TdsSection, on_delete=models.PROTECT, related_name="+")
    # What the deduction was made at, kept: a rate or PAN changed on the
    # vendor later does not change what was deducted.
    base = models.DecimalField(max_digits=18, decimal_places=2)
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4)
    pan = models.CharField(max_length=10, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    covered_bills = models.ManyToManyField(
        "purchasing.Bill", related_name="+",
        help_text="The bills its base is made of: this one, and under 194C the year's earlier "
                  "ones untaxed until the threshold was passed.")
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False)
    reversed_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)
    challan = models.ForeignKey("purchasing.TdsChallan", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="deductions", editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="tds_deduction_positive"),
            models.UniqueConstraint(fields=["bill", "section"], condition=Q(reversed_entry__isnull=True),
                                    name="one_standing_tds_per_bill_and_section"),
        ]

    def __str__(self):
        return f"{self.section.code} {self.amount} on {self.bill}"

    def is_paid_over(self):
        return bool(self.challan_id and self.challan.voided_entry_id is None)

    @serialised("reversed_entry", "challan")
    def reverse(self, on_date=None):
        """
        Take a deduction back: made under the wrong section, or the bill
        corrected. On its own date by default, so the month it was owed in
        nets to what is still owed.
        """
        if self.reversed_entry_id:
            raise ValidationError("This deduction has already been reversed.")
        if self.is_paid_over():
            raise ValidationError(f"Challan {self.challan} has paid this over; void the challan first.")
        self.reversed_entry = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or self.date, memo=f"Reversed: {self.journal_entry.memo}")
        self.save(update_fields=["reversed_entry", "updated_at"])
        return self.reversed_entry


def deduct(bill, section=None, on_date=None):
    """
    Deduct tax from a posted bill under `section`, or its vendor's.
    Locks the vendor: the year's other bills are read, and two deductions
    at once could both catch up the same untaxed ones.
    """
    from .models import Bill

    with transaction.atomic():
        lock_rows(bill.vendor, bill)
        if not bill.posted:
            raise ValidationError("Tax is deducted from a posted bill.")
        if bill.is_debit_note():
            raise ValidationError("A debit note reduces the bill it corrects; deduct on that bill.")
        if getattr(bill, "is_prepayment", False):
            raise ValidationError("Deduct on the bill the prepayment is drawn against.")
        if bill.currency_id not in (None, Company.get().base_currency_id):
            raise ValidationError("Tax on a payment abroad is section 195, which is not built.")
        section, rate, pan = deduction_terms(bill.vendor, section)
        if section.payable_account_id is None:
            raise ValidationError(f"{section} has no payable account to owe the tax into.")
        if _live(bill.tds_deductions.filter(section=section)).exists() or _live(
                TdsDeduction.objects.filter(section=section, covered_bills=bill)).exists():
            raise ValidationError(f"Tax under {section.code} has already been deducted on {bill}.")

        start, end = financial_year(to_date(bill.bill_date))
        year = Bill.objects.filter(
            vendor=bill.vendor, posted=True, debits__isnull=True, bill_date__gte=start, bill_date__lte=end,
        ).exclude(is_prepayment=True).prefetch_related("lines__taxes", "debit_notes__lines__taxes")
        earlier = [other for other in year
                   if (to_date(other.bill_date), other.pk) < (to_date(bill.bill_date), bill.pk)]
        covered = set(_live(TdsDeduction.objects.filter(section=section, covered_bills__in=earlier))
                      .values_list("covered_bills", flat=True))
        untaxed = [other for other in earlier if other.pk not in covered]
        nets = {other.pk: taxable_net(other) for other in earlier}
        base, amount = section.owed(
            rate, taxable_net(bill), sum(nets.values(), Decimal("0")),
            sum((nets[other.pk] for other in untaxed), Decimal("0")))
        if amount <= 0:
            raise ValidationError(
                f"Nothing to deduct under {section.code}: {bill.vendor}'s bills this year have not "
                "passed its threshold.")
        if amount > bill.amount_due():
            raise ValidationError(
                f"{amount} is due under {section.code} but only {bill.amount_due()} is left to pay on "
                f"{bill}; deduct it on the vendor's next bill instead.")

        # Deducted when the bill is booked, which is when the law says it is
        # owed (on credit or payment, whichever is first): the challan for
        # the bill's month is the one that pays it.
        day = to_date(on_date) or to_date(bill.bill_date)
        memo = f"TDS {section.code} on {bill.number}"
        entry = JournalEntry.objects.create(date=day, reference=bill.number, memo=memo)
        JournalLine.objects.create(entry=entry, account=bill.payable_account, party=bill.vendor,
                                   debit=amount, description=memo)
        JournalLine.objects.create(entry=entry, account=section.payable_account, party=bill.vendor,
                                   credit=amount, description=memo)
        entry.post()
        deduction = TdsDeduction.objects.create(
            bill=bill, section=section, base=base, rate_percent=rate, pan=pan, amount=amount,
            date=day, journal_entry=entry)
        deduction.covered_bills.set([bill, *untaxed] if section.mode == ThresholdMode.WHOLE else [bill])
        return deduction


class TdsChallan(AuditModel):
    """One payment of a month's deductions under one section to the government."""

    section = models.ForeignKey(TdsSection, on_delete=models.PROTECT, related_name="+")
    month = models.DateField(help_text="The month the deductions were made in (its first day).")
    date = models.DateField(help_text="The day it was paid.")
    bank_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    challan_number = models.CharField(max_length=16, help_text="The challan serial number.")
    bsr_code = models.CharField(max_length=7, help_text="The bank branch's BSR code.")
    amount = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False)
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="tds_challan_positive")]

    def __str__(self):
        return f"{self.bsr_code}/{self.challan_number} {self.section.code}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            # Paid is a fact; a wrong challan is voided and paid again.
            before = TdsChallan.objects.get(pk=self.pk)
            if any(getattr(before, field) != getattr(self, field)
                   for field in ("section_id", "month", "date", "bank_account_id", "amount")):
                raise ValidationError("A challan is not changed once paid; void it.")
        super().save(*args, **kwargs)

    @classmethod
    def pay(cls, section, month, date, bank_account, challan_number, bsr_code):
        """Pay over everything deducted under `section` in `month` and not yet paid."""
        from apps.accounting.money import refuse_as_money_account

        refuse_as_money_account(bank_account)
        month = to_date(month).replace(day=1)
        following = (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        with transaction.atomic():
            lock_rows(section)
            owed = list(_live(TdsDeduction.objects.filter(section=section, date__gte=month, date__lt=following))
                        .filter(Q(challan__isnull=True) | Q(challan__voided_entry__isnull=False)))
            if not owed:
                raise ValidationError(f"Nothing deducted under {section.code} in {month:%B %Y} is waiting "
                                      "to be paid over.")
            if section.payable_account_id is None:
                raise ValidationError(f"{section} has no payable account.")
            total = sum((row.amount for row in owed), Decimal("0"))
            memo = f"TDS {section.code} for {month:%B %Y}, challan {challan_number}"
            entry = JournalEntry.objects.create(date=to_date(date), reference=challan_number, memo=memo)
            JournalLine.objects.create(entry=entry, account=section.payable_account, debit=total,
                                       description=memo)
            JournalLine.objects.create(entry=entry, account=bank_account, credit=total, description=memo)
            entry.post()
            challan = cls.objects.create(section=section, month=month, date=to_date(date),
                                         bank_account=bank_account, challan_number=challan_number,
                                         bsr_code=bsr_code, amount=total, journal_entry=entry)
            TdsDeduction.objects.filter(pk__in=[row.pk for row in owed]).update(challan=challan)
            return challan

    @serialised("voided_entry")
    def void(self, on_date=None):
        """The payment did not go through: the tax is owed again, and its deductions wait for another."""
        if self.voided_entry_id:
            raise ValidationError("This challan has already been voided.")
        self.voided_entry = self.journal_entry.create_reversal(
            entry_date=to_date(on_date) or self.date, memo=f"Voided: {self.journal_entry.memo}")
        super().save(update_fields=["voided_entry", "updated_at"])
        return self.voided_entry


def tds_return(start, end):
    """
    The quarter's deductions as the return lists them: who, their PAN,
    under what, how much was credited and deducted, and the challan that
    paid it over.
    """
    rows = _live(TdsDeduction.objects.filter(date__gte=to_date(start), date__lte=to_date(end))) \
        .select_related("bill__vendor", "section", "challan").order_by("section__code", "date", "pk")
    return [{
        "deduction": row.pk, "date": row.date, "section": row.section.code, "vendor": row.bill.vendor.name,
        "pan": row.pan or "PANNOTAVBL", "bill": row.bill.number, "base": row.base,
        "rate_percent": row.rate_percent, "amount": row.amount,
        "challan": f"{row.challan.bsr_code}/{row.challan.challan_number}" if row.is_paid_over() else "",
        "paid_on": row.challan.date if row.is_paid_over() else None,
    } for row in rows]
