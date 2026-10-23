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

from apps.accounting.models import (Account, JournalEntry, JournalLine, TcsDeclaration, TcsRate, TcsSection,
                                     round_money)
from apps.accounting.tds import rupees
from apps.core.models import AuditModel, Company, serialised, to_date


from apps.core.models import takes_back  # noqa: E402  (O354: a correction step)

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
    section_code = models.CharField(
        max_length=16, blank=True, editable=False,
        help_text="The section the law named on its day (TcsSection.code_on), as the print and the return say "
                  "it: 206C(1) before April 2026, 394(1) after.")
    collection_code = models.CharField(
        max_length=16, blank=True, editable=False,
        help_text="The return's collection code (the section's form code) as it stood when collected (O245): "
                  "an edit to the section later does not re-code a quarter already filed.")
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    posted_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="The TCS payable account it posted to, as the section named it that day: where its give-back "
                  "and the challan that pays it over go, whatever the section says later.")
    challan = models.ForeignKey("sales.TcsChallan", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="collections", editable=False)

    # Read at the event, kept, and never through the section again (audit_invariants).
    KEPT_FROM = {"section": ("payable_account", "form_code")}

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "TCS collection"
        constraints = [
            models.CheckConstraint(check=Q(amount__gte=0) & Q(base__gte=0), name="tcs_collection_not_negative"),
            models.CheckConstraint(check=Q(amount=0) | Q(posted_account__isnull=False),
                                   name="tcs_collection_keeps_its_account"),
            models.UniqueConstraint(fields=["invoice", "section"], condition=Q(gives_back__isnull=True),
                                    name="one_tcs_collection_per_invoice_and_section"),
            # A credit note gives back what its invoice collected and, where the invoice drew down an
            # advance, what the advance collected on that value (O243): once each.
            models.UniqueConstraint(fields=["invoice", "gives_back"], condition=Q(gives_back__isnull=False),
                                    name="one_give_back_per_note_and_collection"),
        ]

    def __str__(self):
        return f"TCS {self.section.code} {self.amount} on {self.invoice.number}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = TcsCollection.objects.get(pk=self.pk)
            if any(getattr(before, name) != getattr(self, name)
                   for name in ("invoice_id", "section_id", "base", "rate_percent", "amount", "date", "pan",
                                "declaration_id", "gives_back_id", "posted_account_id", "section_code",
                                "collection_code", "challan_id")):
                # The challan too: a plain save released a paid row from its challan (review F16).
                raise ValidationError("What an invoice collected is a fact, and so is the challan that paid it "
                                      "over; a credit note gives it back, and a void frees the challan's rows.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("What a posted invoice collected stays; a credit note gives it back.")

    def law_section(self):
        """
        The section it was issued under, as the print and the return say it. Empty only on a row the
        upgrade could not tell (sales 0072): it collected nothing, before each row kept its section,
        and its section has been edited since. Refused in words rather than printed or filed blank.
        """
        if not self.section_code:
            raise ValidationError(
                f"TCS row {self.pk} on {self.invoice.number} of {self.date:%d %b %Y} does not say which section "
                f"it was issued under: it was made before each row kept its section, and {self.section.code} "
                "has been edited since, so the books cannot tell whether it was then called so; it is not "
                "printed or filed under a guess.")
        return self.section_code

    def signed(self):
        """As the challan nets it: collected adds, given back takes away."""
        return -self.amount if self.gives_back_id else self.amount

    def is_paid_over(self):
        return bool(self.challan_id and self.challan.voided_entry_id is None)


def _tcs_lines(invoice):
    """
    {section: base} over the lines whose item names a TCS section, and their share of the charges.

    Freight or packing billed on the invoice is part of what the buyer pays for the goods, which is what
    section 206C(1) collects on (review U2): on an invoice of scrap alone it all joins the base; beside
    other goods, its share by the goods' value before tax. Total first, divided last.
    """
    taxes = invoice.line_tax_amounts()

    def charged(line):
        return sum((amount for tax, amount in taxes.get(line, ()) if not tax.reverse_charge), Decimal("0"))

    bases, goods, charges = defaultdict(Decimal), defaultdict(Decimal), []
    for line in invoice.lines.select_related("item__tcs_section", "charge", "debits_line__item__tcs_section",
                                             "credits_line__debits_line__item__tcs_section"):
        if line.is_charge():
            charges.append(line)
            continue
        item = _goods_of(line)
        section = getattr(item, "tcs_section", None) if item is not None else None
        goods[section] += line.net_amount()
        if section is None:
            continue
        bases[section] += line.net_amount() + (charged(line) if section.base_includes_gst else Decimal("0"))
    all_goods = sum(goods.values(), Decimal("0"))
    for section in list(bases):
        for line in charges:
            value = line.net_amount() + (charged(line) if section.base_includes_gst else Decimal("0"))
            bases[section] += round_money(value * goods[section] / all_goods) if all_goods else Decimal("0")
    return bases


def _goods_of(line):
    """
    The item a line sells: its own, or on a debit note's line (and a credit note's line on one) the
    invoice line it bills more for. A price rise billed on scrap is TCS's as the scrap was.
    """
    if line.item_id:
        return line.item
    debited = line.debits_line or (line.credits_line.debits_line if line.credits_line_id else None)
    return debited.item if debited is not None and debited.item_id else None


def _entry(invoice, account, amount, memo, give_back):
    entry = JournalEntry.objects.create(date=invoice.invoice_date, reference=invoice.number, memo=memo)
    JournalLine.objects.create(entry=entry, account=invoice.receivable_account, party=invoice.customer,
                               debit=Decimal("0") if give_back else amount,
                               credit=amount if give_back else Decimal("0"), description=memo)
    JournalLine.objects.create(entry=entry, account=account, party=invoice.customer,
                               debit=amount if give_back else Decimal("0"),
                               credit=Decimal("0") if give_back else amount, description=memo)
    entry.post()
    return entry


def collect(invoice):
    """
    What the invoice collects, or its credit note gives back, as it posts; the amount, kept on it.
    Called inside Invoice.post(), under its lock, after its number and taxes are settled.
    """
    if invoice.credits_id and invoice.credits.is_down_payment:
        return _give_back_advance(invoice)
    if invoice.is_credit_note():
        return _give_back(invoice)
    # Section 206C(1) (394 from April 2026) collects at the debit or the receipt, whichever is earlier: an
    # advance on a scrap order is due TCS as it is billed to the buyer (O243). The invoice for the goods
    # then collects only on what the advance did not (_count_advances).
    bases = _advance_bases(invoice) if invoice.is_down_payment else _tcs_lines(invoice)
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
                                         rate_percent=Decimal("0"), pan=declaration.pan, amount=Decimal("0"), date=day,
                                         section_code=section.code_on(day),
                                         collection_code=section.form_code)
            continue
        rate = section.rate_on(day)
        if section.payable_account_id is None:
            raise ValidationError(f"{section} has no payable account to owe the tax collected into.")
        percent = rate.rate_percent if pan else rate.no_pan_rate_percent
        counts = [] if invoice.is_down_payment else _count_advances(invoice, section, base)
        base -= sum((counted for _, counted in counts), Decimal("0"))
        amount = rupees(base * percent / 100)
        # The account the section names today, read once at the event and kept with the collection.
        account = section.payable_account
        entry = None
        if amount:
            entry = _entry(invoice, account, amount, f"TCS {rate.section_code or section.code} on {invoice.number}",
                           give_back=False)
        row = TcsCollection.objects.create(invoice=invoice, section=section, rate=rate, base=base,
                                           rate_percent=percent, pan=pan, amount=amount, date=day,
                                           journal_entry=entry, posted_account=account,
                                           section_code=rate.section_code or section.code,
                                           # Read once at the event, as the account is (O245).
                                           collection_code=section.form_code)
        for advance, counted in counts:
            TcsAdvanceCount.objects.create(collection=row, advance=advance, base=counted, date=day)
        total += amount
    return total


def _advance_bases(deposit):
    """
    {section: base} of a down payment on an order of goods TCS applies to: its value, less TCS, in the
    share of the order those goods are (their value with GST where the section counts it). A down
    payment on no order, or on goods TCS does not apply to, collects nothing.
    """
    order = deposit.sales_order
    if order is None:
        return {}
    lines = list(order.lines.select_related("item__tcs_section"))
    whole = sum((line.total() for line in lines), Decimal("0"))
    value = deposit.total() - deposit.tcs_amount
    shares = defaultdict(Decimal)
    for line in lines:
        section = getattr(line.item, "tcs_section", None) if line.item_id else None
        if section is not None:
            shares[section] += line.total() if section.base_includes_gst else line.net_amount()
    return {section: round_money(value * share / whole) for section, share in shares.items() if whole}


def advance_left(advance):
    """What of an advance's base no invoice counts and no refund gave back (TcsAdvanceCount, signed)."""
    counted = advance.counted_by.aggregate(total=models.Sum("base"))["total"] or Decimal("0")
    given = advance.given_back.aggregate(total=models.Sum("base"))["total"] or Decimal("0")
    return advance.base - counted - given


def _count_advances(invoice, section, base):
    """
    [(advance collection, base)]: the advances on this invoice's order that already collected under
    `section`, oldest first, counted against its `base` as far as they go, so the value is never collected
    on twice. An advance larger than one invoice is counted across the next (O243).
    """
    order = invoice.sales_order
    if order is None or not base:
        return []
    advances = TcsCollection.objects.filter(
        invoice__sales_order=order, invoice__is_down_payment=True, invoice__credits__isnull=True,
        invoice__posted=True, section=section, gives_back__isnull=True, declaration__isnull=True,
    ).order_by("date", "pk")
    counts, wanted = [], base
    for advance in advances:
        left = advance_left(advance)
        take = min(left, wanted)
        if take > 0:
            counts.append((advance, take))
            wanted -= take
        if not wanted:
            break
    return counts


def _give_back_advance(note):
    """
    An advance credited back (refunded): the TCS on its part no invoice has counted, at the rate it was
    collected at, the last of it taking what is left. Counted against an invoice, it was the invoice's
    collection, and that invoice's credit note releases it first (_give_back).
    """
    deposit = note.credits
    value = deposit.total() - deposit.tcs_amount
    if not value:
        return Decimal("0")
    total = Decimal("0")
    for collected in deposit.tcs_collected.filter(gives_back__isnull=True, amount__gt=0):
        left = advance_left(collected)
        base = min(round_money((note.total() - note.tcs_amount) * collected.base / value), left)
        if base <= 0:
            continue
        given = sum((row.amount for row in TcsCollection.objects.filter(gives_back=collected)), Decimal("0"))
        counted = collected.counted_by.aggregate(total=models.Sum("base"))["total"] or Decimal("0")
        last = base >= left and not counted
        amount = collected.amount - given if last else min(rupees(base * collected.rate_percent / 100),
                                                            collected.amount - given)
        if amount <= 0:
            continue
        entry = _entry(note, collected.posted_account, amount,
                       f"TCS {collected.section_code} given back by {note.number} on {deposit.number}",
                       give_back=True)
        TcsCollection.objects.create(invoice=note, section=collected.section, rate=collected.rate,
                                     gives_back=collected, base=base, rate_percent=collected.rate_percent,
                                     pan=collected.pan, amount=amount, date=to_date(note.invoice_date),
                                     journal_entry=entry, posted_account=collected.posted_account,
                                     section_code=collected.section_code,
                                     collection_code=collected.collection_code)
        total += amount
    return total


def _give_back(note):
    original = note.credits
    collections = [row for row in original.tcs_collected.filter(gives_back__isnull=True)
                   if row.amount > 0 or row.counts.exists()]
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
        own_left = collected.base - sum((row.base for row in before), Decimal("0"))
        # What it collected on itself first; past that, the value an advance collected on, which it
        # releases back to the advance (O243): its refund gives that back, or another invoice counts it.
        total += _release_counts(collected, note, base - max(min(base, own_left), Decimal("0")))
        base = max(min(base, own_left), Decimal("0"))
        if not base:
            continue
        # The last of the invoice's value takes what is left: 266 then 797 would give back 1,063 of 1,062.
        amount = (collected.amount - given if base >= own_left
                  else min(rupees(base * collected.rate_percent / 100), collected.amount - given))
        if amount <= 0:
            continue
        entry = _entry(note, collected.posted_account, amount,
                       f"TCS {collected.section_code} given back by {note.number} on {original.number}",
                       give_back=True)
        TcsCollection.objects.create(invoice=note, section=collected.section, rate=collected.rate,
                                     gives_back=collected, base=base, rate_percent=collected.rate_percent,
                                     pan=collected.pan, amount=amount, date=to_date(note.invoice_date),
                                     journal_entry=entry, posted_account=collected.posted_account,
                                     section_code=collected.section_code,
                                     collection_code=collected.collection_code)
        total += amount
    return total


def _release_counts(collected, note, excess):
    """
    Undo, newest first, `excess` of what `collected` counted against advances: a reversing row each. Where
    the invoice drew that advance down, the money is now the note's to give back, and the advance can no
    longer be refunded (deposits are credited through the invoice then): the note gives back the advance's
    TCS on that value too. Returns what it gave back.
    """
    from .models import DepositApplication

    given_back = Decimal("0")
    for count in collected.counts.filter(base__gt=0).order_by("-date", "-pk"):
        if excess <= 0:
            break
        standing = count.base + (count.released.aggregate(total=models.Sum("base"))["total"] or Decimal("0"))
        take = min(standing, excess)
        if take <= 0:
            continue
        TcsAdvanceCount.objects.create(collection=collected, advance=count.advance, base=-take,
                                       date=to_date(note.invoice_date), releases=count, note=note)
        excess -= take
        advance = count.advance
        if not DepositApplication.objects.filter(invoice=collected.invoice, deposit=advance.invoice).exists():
            continue
        given = sum((row.amount for row in TcsCollection.objects.filter(gives_back=advance)), Decimal("0"))
        last = take >= advance_left(advance) and not advance.counted_by.aggregate(
            total=models.Sum("base"))["total"]
        amount = advance.amount - given if last else min(rupees(take * advance.rate_percent / 100),
                                                         advance.amount - given)
        if amount <= 0:
            continue
        entry = _entry(note, advance.posted_account, amount,
                       f"TCS {advance.section_code} given back by {note.number} on {advance.invoice.number}",
                       give_back=True)
        TcsCollection.objects.create(invoice=note, section=advance.section, rate=advance.rate, gives_back=advance,
                                     base=take, rate_percent=advance.rate_percent, pan=advance.pan, amount=amount,
                                     date=to_date(note.invoice_date), journal_entry=entry,
                                     posted_account=advance.posted_account, section_code=advance.section_code,
                                     collection_code=advance.collection_code)
        given_back += amount
    return given_back


class TcsAdvanceCount(AuditModel):
    """
    Part of an advance's TCS base counted against the invoice for its goods (O243): that much of the
    invoice's value was collected on when the advance was billed, and is not collected on again. A
    credit note on the invoice releases it with a row the other way (negative `base`), never an edit.
    """

    collection = models.ForeignKey(TcsCollection, on_delete=models.PROTECT, related_name="counts",
                                   help_text="The invoice's collection it counts against.")
    advance = models.ForeignKey(TcsCollection, on_delete=models.PROTECT, related_name="counted_by",
                                help_text="The advance's collection it counts.")
    base = models.DecimalField(max_digits=18, decimal_places=2,
                               help_text="Counted; negative on a row that releases what another counted.")
    date = models.DateField()
    releases = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="released")
    note = models.ForeignKey("sales.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                             help_text="The credit note that released it.")

    class Meta:
        ordering = ["date", "id"]
        verbose_name = "TCS advance counted"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("What an invoice counted of an advance is a fact; a credit note releases it.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("What an invoice counted of an advance is a fact; a credit note releases it.")


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
    section_code = models.CharField(
        max_length=16, blank=True, editable=False,
        help_text="The section it was paid under, as the law named it on the day (O245), kept.")
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False)
    voided_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "TCS challan"
        constraints = [models.CheckConstraint(check=Q(amount__gt=0), name="tcs_challan_positive")]

    def __str__(self):
        return f"{self.bsr_code}/{self.challan_number} {self.section_code}"

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
                challan_number=challan_number, bsr_code=bsr_code, amount=total, journal_entry=entry,
                section_code=section.code_on(to_date(date))),
            signed=TcsCollection.signed)

    @serialised("voided_entry")
    @takes_back
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
            "collection": row.pk, "date": row.date, "section": row.law_section(),
            "collection_code": row.collection_code, "buyer": row.invoice.customer.name,
            "pan": row.pan or "PANNOTAVBL", "invoice": row.invoice.number, "base": sign * row.base,
            "rate_percent": row.rate_percent, "amount": sign * row.amount,
            "challan": f"{row.challan.bsr_code}/{row.challan.challan_number}" if paid else "",
            "paid_on": row.challan.date if paid else None,
            "not_collected": DECLARED if row.declaration_id else "",
            "declaration": str(row.declaration.reference or row.declaration.pk) if row.declaration_id else "",
            "gives_back": row.gives_back.invoice.number if row.gives_back_id else "",
        })
    return sorted(rows, key=lambda row: (row["section"], row["date"], row["collection"]))
