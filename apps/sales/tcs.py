"""
TCS collected on a sale invoice, given back by its credit note, paid over
by challan, and listed for the quarter's return (Form 143; 27EQ before
April 2026). The sections, dated rates and buyers' declarations are
accounting's (apps/accounting/tcs.py); what an invoice collected is a fact
kept here, at the rate and under the declaration as they stood.

On posting, an invoice with lines of goods TCS applies to (an item that
names its section) collects on those lines' value, with the GST charged on
them where the section says so: Dr Receivable / Cr TCS payable, its own
entry, the amount kept on the invoice (Invoice.tcs_amount) so the buyer
owes it with the invoice. A whole number of rupees, half up, as TDS.
A declaration covering the day exempts: kept as a row collecting nothing,
which the return reports as not collected.

The reverses: a credit note on such an invoice gives back the TCS on the
value it credits (Dr TCS payable / Cr Receivable), at the rate it was
collected at, the last of the invoice's value taking whatever is left so
the two never differ by a rupee. A draft collects nothing, so deleting one
gives nothing back. A challan is voided as a TDS challan is, and its rows
wait for another.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.accounting.models import Account, JournalEntry, JournalLine, TcsDeclaration, TcsRate, TcsSection
from apps.accounting.tds import rupees
from apps.core.models import AuditModel, Company, serialised, to_date


class TcsCollection(AuditModel):
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.PROTECT, related_name="tcs_collected")
    section = models.ForeignKey(TcsSection, on_delete=models.PROTECT, related_name="+")
    rate = models.ForeignKey(TcsRate, null=True, blank=True, on_delete=models.PROTECT, related_name="collections")
    declaration = models.ForeignKey(TcsDeclaration, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="collections")
    gives_back = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="given_back", help_text="On a credit note: the collection it "
                                                                        "gives part of back.")
    # As collected, kept: a rate or PAN changed later does not change what was collected.
    base = models.DecimalField(max_digits=18, decimal_places=2)
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4)
    pan = models.CharField(max_length=10, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    challan = models.ForeignKey("sales.TcsChallan", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="collections", editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "TCS collection"
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0) & Q(base__gte=0), name="tcs_collection_not_negative"),
            models.UniqueConstraint(fields=["invoice", "section"], name="one_tcs_collection_per_invoice_and_section"),
        ]

    def __str__(self):
        return f"TCS {self.section.code} {self.amount} on {self.invoice.number}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = TcsCollection.objects.get(pk=self.pk)
            if any(getattr(before, name) != getattr(self, name)
                   for name in ("invoice_id", "section_id", "base", "rate_percent", "amount", "date")):
                raise ValidationError("What an invoice collected is a fact; a credit note gives it back.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("What a posted invoice collected stays; a credit note gives it back.")

    def signed(self):
        """As the challan nets it: collected adds, given back takes away."""
        return -self.amount if self.gives_back_id else self.amount

    def is_paid_over(self):
        return bool(self.challan_id and self.challan.voided_entry_id is None)


def _tcs_lines(invoice):
    """{section: base} over the lines whose item names a TCS section."""
    taxes = invoice.line_tax_amounts()
    bases = defaultdict(Decimal)
    for line in invoice.lines.select_related("item__tcs_section"):
        section = getattr(line.item, "tcs_section", None) if line.item_id else None
        if section is None:
            continue
        value = line.net_amount()
        if section.base_includes_gst:
            value += sum((amount for tax, amount in taxes.get(line, ()) if not tax.reverse_charge), Decimal("0"))
        bases[section] += value
    return bases


def _entry(invoice, section, amount, memo, give_back):
    entry = JournalEntry.objects.create(date=invoice.invoice_date, reference=invoice.number, memo=memo)
    JournalLine.objects.create(entry=entry, account=invoice.receivable_account, party=invoice.customer,
                               debit=Decimal("0") if give_back else amount,
                               credit=amount if give_back else Decimal("0"), description=memo)
    JournalLine.objects.create(entry=entry, account=section.payable_account, party=invoice.customer,
                               debit=amount if give_back else Decimal("0"),
                               credit=Decimal("0") if give_back else amount, description=memo)
    entry.post()
    return entry


def collect(invoice):
    """
    What the invoice collects, or its credit note gives back, as it posts; the amount, kept on it.
    Called inside Invoice.post(), under its lock, after its number and taxes are settled.
    """
    if invoice.is_down_payment or (invoice.credits_id and invoice.credits.is_down_payment):
        return Decimal("0")
    if invoice.is_credit_note():
        return _give_back(invoice)
    bases = _tcs_lines(invoice)
    if not bases:
        return Decimal("0")
    if invoice.currency_id not in (None, Company.get().base_currency_id):
        raise ValidationError(f"{invoice} sells goods TCS applies to in {invoice.currency.code}; TCS on a sale "
                              "in another currency is not built.")
    profile = getattr(invoice.customer, "tax_profile", None)
    pan = profile.pan_on_file() if profile else ""
    day = to_date(invoice.invoice_date)
    total = Decimal("0")
    for section, base in sorted(bases.items(), key=lambda pair: pair[0].code):
        if not section.is_active:
            raise ValidationError(f"{section} is no longer in use; the item should name the section in force.")
        declaration = TcsDeclaration.covering(invoice.customer, section, day)
        if declaration is not None:
            TcsCollection.objects.create(invoice=invoice, section=section, declaration=declaration, base=base,
                                         rate_percent=Decimal("0"), pan=pan, amount=Decimal("0"), date=day)
            continue
        rate = section.rate_on(day)
        if section.payable_account_id is None:
            raise ValidationError(f"{section} has no payable account to owe the tax collected into.")
        percent = rate.rate_percent if pan else rate.no_pan_rate_percent
        amount = rupees(base * percent / 100)
        entry = None
        if amount:
            entry = _entry(invoice, section, amount, f"TCS {section.code} on {invoice.number}", give_back=False)
        TcsCollection.objects.create(invoice=invoice, section=section, rate=rate, base=base, rate_percent=percent,
                                     pan=pan, amount=amount, date=day, journal_entry=entry)
        total += amount
    return total


def _give_back(note):
    original = note.credits
    collections = list(original.tcs_collected.filter(gives_back__isnull=True, amount__gt=0))
    if not collections:
        return Decimal("0")
    bases = _tcs_lines(note)
    total = Decimal("0")
    for collected in collections:
        base = bases.get(collected.section, Decimal("0"))
        if not base:
            continue
        before = list(TcsCollection.objects.filter(gives_back=collected))
        given = sum((row.amount for row in before), Decimal("0"))
        credited = sum((row.base for row in before), Decimal("0")) + base
        # The last of the invoice's value takes what is left: 266 then 797 would give back 1,063 of 1,062.
        amount = (collected.amount - given if credited >= collected.base
                  else min(rupees(base * collected.rate_percent / 100), collected.amount - given))
        if amount <= 0:
            continue
        entry = _entry(note, collected.section, amount,
                       f"TCS {collected.section.code} given back by {note.number} on {original.number}",
                       give_back=True)
        TcsCollection.objects.create(invoice=note, section=collected.section, rate=collected.rate,
                                     gives_back=collected, base=base, rate_percent=collected.rate_percent,
                                     pan=collected.pan, amount=amount, date=to_date(note.invoice_date),
                                     journal_entry=entry)
        total += amount
    return total


def _live(queryset):
    return queryset.exclude(amount=0)


class TcsChallan(AuditModel):
    """One payment over of what was collected under one section, to the month's end and not yet paid."""

    section = models.ForeignKey(TcsSection, on_delete=models.PROTECT, related_name="+")
    month = models.DateField(help_text="The month it pays for (its first day).")
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
        verbose_name = "TCS challan"
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="tcs_challan_positive")]

    def __str__(self):
        return f"{self.bsr_code}/{self.challan_number} {self.section.code}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = TcsChallan.objects.get(pk=self.pk)
            if any(getattr(before, field) != getattr(self, field)
                   for field in ("section_id", "month", "date", "bank_account_id", "amount")):
                raise ValidationError("A challan is not changed once paid; void it.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A challan paid is a fact; void it.")

    @classmethod
    def pay(cls, section, month, date, bank_account, challan_number, bsr_code):
        """
        Pay over what was collected under `section` up to `month`'s end and not yet paid, net of what
        credit notes gave back (challans.pay_over, the TDS challan's mechanism). Earlier months' rows
        still waiting are taken in: a give-back after its month was paid is set against the next.
        """
        from apps.accounting.challans import pay_over

        month = to_date(month).replace(day=1)
        following = (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        waiting = (TcsCollection.objects.filter(section=section, date__lt=following)
                   .filter(Q(challan__isnull=True) | Q(challan__voided_entry__isnull=False)))
        return pay_over(
            section, waiting, _live, bank_account, date, challan_number,
            what=f"collected under {section.code} to the end of {month:%B %Y}",
            memo=f"TCS {section.code} for {month:%B %Y}, challan {challan_number}",
            make_challan=lambda total, entry: cls.objects.create(
                section=section, month=month, date=to_date(date), bank_account=bank_account,
                challan_number=challan_number, bsr_code=bsr_code, amount=total, journal_entry=entry),
            signed=TcsCollection.signed)

    @serialised("voided_entry")
    def void(self, on_date=None):
        """The payment did not go through: the tax is owed again, and its rows wait for another challan."""
        from apps.accounting.challans import void_challan

        return void_challan(self, on_date)


def tcs_challans_paid():
    """The entries TCS challans paid over by, and voided them by (accounting.register_bank_movements)."""
    paid = TcsChallan.objects.values("journal_entry")
    voided = TcsChallan.objects.filter(voided_entry__isnull=False).values("voided_entry")
    return JournalEntry.objects.filter(Q(pk__in=paid) | Q(pk__in=voided))


# Not collected: the buyer's declaration that the goods are for its own manufacture (the return's
# code for a declaration under section 206C(1A), Form 27C, now Form 127).
DECLARED = "B"


def tcs_return(start, end):
    """
    The quarter's rows as the return (Form 143; 27EQ before April 2026) lists them: the buyer and
    its PAN, the section's code, the amount the tax was collected on, the rate and the tax, the
    challan that paid it over, and the code for a sale not collected on under a declaration.
    A credit note's give-back is a row of its own, taking the amount back.
    """
    start, end = to_date(start), to_date(end)
    rows = []
    for row in (TcsCollection.objects.filter(date__gte=start, date__lte=end)
                .select_related("invoice__customer", "section", "challan", "declaration", "gives_back__invoice")):
        sign = -1 if row.gives_back_id else 1
        paid = row.is_paid_over()
        rows.append({
            "collection": row.pk, "date": row.date, "section": row.section.code,
            "collection_code": row.section.form_code, "buyer": row.invoice.customer.name,
            "pan": row.pan or "PANNOTAVBL", "invoice": row.invoice.number, "base": sign * row.base,
            "rate_percent": row.rate_percent, "amount": sign * row.amount,
            "challan": f"{row.challan.bsr_code}/{row.challan.challan_number}" if paid else "",
            "paid_on": row.challan.date if paid else None,
            "not_collected": DECLARED if row.declaration_id else "",
            "declaration": str(row.declaration.reference or row.declaration.pk) if row.declaration_id else "",
            "gives_back": row.gives_back.invoice.number if row.gives_back_id else "",
        })
    return sorted(rows, key=lambda row: (row["section"], row["date"], row["collection"]))
