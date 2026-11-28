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

A lower-deduction certificate's rate applies up to the amount it states
(O598). A bill that crosses what is left on it is deducted in two rows,
as the return lists them: the part up to the amount at the certificate's
rate, the rest at the section's. The two are one deduction: reversed
together, paid over together.

A certificate at a nil rate deducts nothing, and the return still lists
each payment it covered, under it, with no tax (O618). So a part at a nil
rate is a row of its own with nothing deducted and no entry: what the
certificate has used is counted against its amount like any other's, and
past the amount the section's rate applies. Such a row moves no money, so
no challan pays it over; reversed, it takes its day and nothing else.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.accounting.models import Account, AccountingPeriod, JournalEntry, JournalLine, TdsSection
from apps.accounting.restatement import AsksTheRestatement
from apps.accounting.statutory_names import period_names
from apps.accounting.tds import deduction_terms, financial_year, rupees
from apps.core.models import AuditModel, Company, correction_date, lock_rows, serialised, to_date


from apps.core.models import takes_back  # noqa: E402  (O354: a correction step)

def taxable_net(bill):
    """The bill's taxable value, less its posted debit notes'."""
    return bill.subtotal() - sum(
        (note.subtotal() for note in bill.debit_notes.all() if note.posted), Decimal("0"))


def _live(queryset):
    return queryset.filter(reversed_on__isnull=True)


class TdsDeduction(AsksTheRestatement, AuditModel):
    RESTATES = ("bill",)
    bill = models.ForeignKey("purchasing.Bill", on_delete=models.PROTECT, related_name="tds_deductions")
    section = models.ForeignKey(TdsSection, on_delete=models.PROTECT, related_name="+")
    # What the deduction was made at, kept: a rate or PAN changed on the
    # vendor later does not change what was deducted.
    base = models.DecimalField(max_digits=18, decimal_places=2)
    rate_percent = models.DecimalField(max_digits=7, decimal_places=4)
    rate = models.ForeignKey(
        "accounting.TdsRate", null=True, blank=True, on_delete=models.PROTECT, related_name="deductions",
        editable=False,
        help_text="The section's rate row it was deducted at, as it stood on its day (O233); empty for a "
                  "lower-deduction certificate's rate, and for deductions made before rates were dated.")
    pan = models.CharField(max_length=10, blank=True)
    certificate = models.CharField(
        max_length=64, blank=True, default="", db_default="", editable=False,
        help_text="The lower-deduction certificate (section 197) its rate was, as the vendor's profile named it on "
                  "the day: the return lists the row as a lower deduction under it (O597). Empty at the section's "
                  "rate and at a deductee's own class, and on deductions made before it was kept.")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    date = models.DateField()
    covered_bills = models.ManyToManyField(
        "purchasing.Bill", related_name="+",
        help_text="The bills its base is made of: this one, and under 194C the year's earlier "
                  "ones untaxed until the threshold was passed.")
    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="What it posted; none on a row at a certificate's nil rate, which deducts nothing (O618).")
    reversed_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)
    reversed_on = models.DateField(
        null=True, blank=True, editable=False,
        help_text="The day it was taken back: its reversal entry's, or, on a row at a nil rate, which has no "
                  "entry to reverse, the day it was reversed on (O618). Empty while it stands.")
    posted_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
        help_text="The TDS payable account it posted to, as the section named it that day: where the challan "
                  "that pays it over goes, whatever the section says later.")
    challan = models.ForeignKey("purchasing.TdsChallan", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="deductions", editable=False)

    # Read at the event, kept, and never through the section again (audit_invariants).
    KEPT_FROM = {"section": ("payable_account",)}

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            # An amount over nothing, posted; or, at a certificate's nil rate, nothing and no entry (O618).
            models.CheckConstraint(
                check=Q(amount__gt=0, journal_entry__isnull=False)
                | (Q(amount=0, rate_percent=0, journal_entry__isnull=True) & ~Q(certificate="")),
                name="tds_deduction_positive_or_nil_at_a_certificate"),
            # Standing; or taken back on a day, by a reversal entry where it posted one and by none where not.
            models.CheckConstraint(
                check=Q(reversed_on__isnull=True, reversed_entry__isnull=True)
                | Q(reversed_on__isnull=False, journal_entry__isnull=False, reversed_entry__isnull=False)
                | Q(reversed_on__isnull=False, journal_entry__isnull=True, reversed_entry__isnull=True),
                name="tds_deduction_reversed_whole"),
            models.CheckConstraint(check=Q(posted_account__isnull=False), name="tds_deduction_keeps_its_account"),
            # One standing deduction per bill and section, which a certificate's amount splits in two at most
            # (O598): the part at the certificate, and the part past it at the section's rate (no certificate).
            models.UniqueConstraint(fields=["bill", "section", "certificate"],
                                    condition=Q(reversed_on__isnull=True),
                                    name="one_standing_tds_per_bill_section_and_certificate"),
        ]

    def __str__(self):
        return f"{self.section.code} {self.amount} on {self.bill}"

    def is_paid_over(self):
        return bool(self.challan_id and self.challan.voided_entry_id is None)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            # The mirror of TcsCollection.save (review F16): what was deducted, into which account, and the
            # challan that paid it, are facts; reverse() takes a deduction back.
            before = TdsDeduction.objects.get(pk=self.pk)
            if any(getattr(before, name) != getattr(self, name)
                   for name in ("bill_id", "section_id", "base", "rate_percent", "rate_id", "pan", "certificate",
                                "amount", "date", "journal_entry_id", "posted_account_id", "challan_id")) or (
                    before.reversed_on is not None and (before.reversed_on, before.reversed_entry_id)
                    != (self.reversed_on, self.reversed_entry_id)):
                raise ValidationError("What was deducted is a fact, and so is the challan that paid it over; "
                                      "reverse the deduction instead.")
        super().save(*args, **kwargs)

    def parts(self):
        """
        The rows of the one deduction this row is part of (O598): itself, and the other part where a certificate's
        amount split the bill's base in two, in key order. A bill holds one standing deduction per section, so
        its standing rows under the section are the parts.
        """
        if self.reversed_on:
            return [self]
        return list(_live(TdsDeduction.objects.filter(bill_id=self.bill_id, section_id=self.section_id))
                    .order_by("pk"))

    def parts_before_it(self):
        """The part of its deduction made before it, held before it (rows of a model are locked in key order)."""
        return [part for part in self.parts() if part.pk < self.pk]

    @serialised("reversed_on", "reversed_entry", "challan", held_first="parts_before_it")
    @takes_back
    def reverse(self, on_date=None):
        """
        Take a deduction back: made under the wrong section, or the bill
        corrected. On its own date by default, so the month it was owed in
        nets to what is still owed. A deduction split at a certificate's
        amount is taken back whole, both rows (O598): one part alone would
        leave the bill deducted on part of its base, and the certificate's
        amount counting the wrong part as used. A row at a nil rate posted
        nothing, so it takes the day alone (O618).
        """
        if self.reversed_on:
            raise ValidationError("This deduction has already been reversed.")
        later = [part for part in self.parts() if part.pk > self.pk]
        if later:
            lock_rows(*later)
        parts = [part for part in self.parts() if part.pk < self.pk] + [self] + later
        for part in parts:
            if part.reversed_on:
                raise ValidationError("This deduction has already been reversed.")
            if part.is_paid_over():
                raise ValidationError(f"Challan {part.challan} has paid this over; void the challan first.")
        day = correction_date(to_date(on_date) or self.date, self.date, f"{self} is not reversed on",
                              "it was deducted")
        for part in parts:
            if part.journal_entry_id:
                part.reversed_entry = part.journal_entry.create_reversal(
                    entry_date=day, memo=f"Reversed: {part.journal_entry.memo}")
            else:
                # Posted nothing, so nothing posts to take it back; its day is asked as an entry's is (O618).
                AccountingPeriod.refuse_closed(day, f"a deduction at a nil rate is not reversed on {day}")
            part.reversed_on = day
            part.save(update_fields=["reversed_entry", "reversed_on", "updated_at"])
        return self.reversed_entry


def due_on(bill, on_date=None):
    """
    The day tax on `bill` falls due, and is deducted at the rate standing
    on: the bill's own day (the credit), or a later one given (the
    payment's). Never before the bill, nor a day still to come.
    """
    if on_date is None:
        return to_date(bill.bill_date)
    return correction_date(on_date, bill.bill_date, f"TDS is not deducted from {bill.number or bill} on",
                           "it falls due on the bill's own day, or on its payment after it, and the bill is dated")


def _owed(bill, section, rate):
    """(base, tax, caught up, the earlier bills it takes in) on `bill` under `section` at `rate`."""
    from .models import Bill

    start, end = financial_year(to_date(bill.bill_date))
    year = Bill.objects.filter(
        vendor=bill.vendor, posted=True, debits__isnull=True, bill_date__gte=start, bill_date__lte=end,
    ).exclude(is_prepayment=True).prefetch_related("lines__taxes", "debit_notes__lines__taxes")
    earlier = [other for other in year
               if (to_date(other.bill_date), other.pk) < (to_date(bill.bill_date), bill.pk or 0)]
    covered = set(_live(TdsDeduction.objects.filter(section=section, covered_bills__in=earlier))
                  .values_list("covered_bills", flat=True))
    untaxed = [other for other in earlier if other.pk not in covered]
    nets = {other.pk: taxable_net(other) for other in earlier}
    base, amount, caught_up = section.owed(
        rate, taxable_net(bill), sum(nets.values(), Decimal("0")),
        sum((nets[other.pk] for other in untaxed), Decimal("0")))
    return base, amount, caught_up, untaxed


def certificate_left(party, section, day):
    """
    What is left on `party`'s lower-deduction certificate under `section` on `day` (O598): the amount it states,
    less the base deducted at it between its days and still standing. None where no amount is followed: no
    certificate standing that day, a nil-rate one (O618), or one entered before its amount was asked (Health).
    """
    profile = getattr(party, "tax_profile", None)
    if (profile is None or profile.tds_rate_basis != "certificate" or profile.tds_rate_limit is None
            or profile.own_tds_rate_on(section, day) is None):
        return None
    used = _live(TdsDeduction.objects.filter(
        bill__vendor=party, section=section, certificate=profile.tds_rate_reference,
        date__gte=profile.tds_rate_from, date__lte=profile.tds_rate_until)).aggregate(total=Sum("base"))["total"]
    return max(Decimal("0"), profile.tds_rate_limit - (used or Decimal("0")))


def deducted_under(party, reference):
    """
    The days tax stands deducted under `party`'s certificate `reference`, a row at a nil rate included (O618): what
    certificate_left counts, under any section and on any day, so a certificate once used keeps its terms (O631).
    """
    return list(_live(TdsDeduction.objects.filter(bill__vendor=party, certificate=reference))
                .values_list("date", flat=True))


def at_the_certificate(base, rate, left):
    """
    How much of `base` is deducted at a certificate's `rate` with `left` on its amount (O598); the rest goes at the
    section's. A part whose tax rounds to nothing at a rate above nil goes at the section's with the rest: it
    cannot be recorded (a deduction at a rate is more than nothing), so its use would never be counted. At a nil
    rate the part is recorded at nothing, as the certificate says (O618).
    """
    if left is None:
        return base
    part = min(base, left)
    return part if Decimal(rate) == 0 or rupees(part * Decimal(rate) / 100) > 0 else Decimal("0")


def refuse_posting_without_a_rate(bill):
    """
    A bill its vendor's section taxes, posted with no confirmed rate standing
    on its day, is refused in words: deducted at nothing, the plant loses the
    expense under section 40(a)(ia) (integrator's decision, g22). Asked of
    the vendor's own section; a rate of its own standing on the bill's day needs
    no row (a certificate's only between its dates, O247, and only up to its
    amount, O598); a bill under the section's thresholds needs none either.
    """
    profile = getattr(bill.vendor, "tax_profile", None)
    section = profile.tds_section if profile else None
    if section is None or not section.is_active or bill.is_debit_note() or bill.is_prepayment:
        return
    if bill.currency_id not in (None, Company.get().base_currency_id):
        return
    day = to_date(bill.bill_date)
    own = profile.own_tds_rate_on(section, day)
    base, _, _, _ = _owed(bill, section, Decimal("0"))
    if base <= 0:
        return
    if own is not None and at_the_certificate(base, own, certificate_left(bill.vendor, section, day)) == base:
        return
    try:
        section.rate_on(day)
    except ValidationError:
        raise ValidationError(
            f"{bill.vendor} is deducted under {section.code} and this bill is taxed under it, but no confirmed "
            f"{section.code} rate stands on {day:%d %b %Y}: nothing would be deducted. The accountant confirms it "
            "under Settings > TDS rates, then the bill posts.") from None


def deduct(bill, section=None, on_date=None):
    """
    Deduct tax from a posted bill under `section`, or its vendor's.
    Locks the vendor: the year's other bills are read, and two deductions
    at once could both catch up the same untaxed ones.
    """

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
        # The day it falls due, which is the day its rate is read for (O233): the bill's own day,
        # or its payment's when deducted as it is paid. Never a day before the bill: dated earlier,
        # it picked the rate before a change and landed in the quarter before (review F1).
        day = due_on(bill, on_date)
        section, rate, pan, rate_row = deduction_terms(bill.vendor, section, day)
        # Read at the event and kept (O597): the return names the certificate a lower deduction rested on, and
        # the profile's reference may name the next one by the time the return is filed.
        profile = getattr(bill.vendor, "tax_profile", None)
        certificate = (profile.tds_rate_reference
                       if rate_row is None and profile is not None and profile.tds_rate_basis == "certificate" else "")
        if rate_row is not None:
            rate = _still_confirmed(section, rate_row).percent_for(pan)
        if section.payable_account_id is None:
            raise ValidationError(f"{section} has no payable account to owe the tax into.")
        if _live(bill.tds_deductions.filter(section=section)).exists() or _live(
                TdsDeduction.objects.filter(section=section, covered_bills=bill)).exists():
            raise ValidationError(f"Tax under {section.code} has already been deducted on {bill}.")

        base, amount, caught_up, untaxed = _owed(bill, section, rate)
        # (base, rate, rate row, certificate) of each row. Past what is left on a certificate's amount, the rest of
        # the base goes at the section's confirmed rate (O598); read under the vendor's lock, so two bills
        # deducted at once cannot both take what is left.
        parts = [(base, rate, rate_row, certificate)]
        if certificate:
            at_certificate = at_the_certificate(base, rate, certificate_left(bill.vendor, section, day))
            if at_certificate < base:
                section_row = _still_confirmed(section, section.rate_on(day))
                # A part whose tax rounds to nothing is no row: a deduction is more than nothing. The part at the
                # certificate never does (at_the_certificate); the rest can, past a certificate a few rupees short
                # of the base (10 at 2%), and goes untaxed as the rounding leaves it. A part at a nil rate is a row
                # at nothing (O618).
                parts = [part for part in [(at_certificate, rate, None, certificate),
                                           (base - at_certificate, section_row.percent_for(pan), section_row, "")]
                         if _at_nil(part, base) or rupees(part[0] * part[1] / 100) > 0]
                amount = sum((rupees(part_base * part_rate / 100) for part_base, part_rate, _, _ in parts),
                             Decimal("0"))
        if amount <= 0 and not any(_at_nil(part, base) for part in parts):
            raise ValidationError(
                f"Nothing to deduct under {section.code}: {bill.vendor}'s bills this year have not "
                "passed its threshold.")
        if amount > bill.amount_due():
            raise ValidationError(
                f"{amount} is due under {section.code} but only {bill.amount_due()} is left to pay on "
                f"{bill}; deduct it on the vendor's next bill instead.")

        # Deducted when the bill is booked, which is when the law says it is
        # owed (on credit or payment, whichever is first): the challan for
        # the bill's month is the one that pays it. An entry for each row, as
        # each is a row of its own in the bill's history and the return.
        account = section.payable_account  # read once at the event, kept with the deduction
        made = []
        for part in parts:
            part_base, part_rate, part_row, part_certificate = part
            part_amount = amount if len(parts) == 1 else rupees(part_base * part_rate / 100)
            entry = None
            if not _at_nil(part, base):
                memo = f"TDS {section.code} on {bill.number}"
                if len(parts) > 1:
                    memo += (f", at certificate {part_certificate}" if part_certificate
                             else ", past the certificate's amount")
                entry = JournalEntry.objects.create(date=day, reference=bill.number, memo=memo)
                JournalLine.objects.create(entry=entry, account=bill.payable_account, party=bill.vendor,
                                           debit=part_amount, description=memo)
                JournalLine.objects.create(entry=entry, account=account, party=bill.vendor,
                                           credit=part_amount, description=memo)
                entry.post()
            else:
                # Nothing deducted and nothing posted, in a period still open for what the row records (O618).
                AccountingPeriod.refuse_closed(day, f"a deduction at a nil rate is not recorded on {day}")
            deduction = TdsDeduction.objects.create(
                bill=bill, section=section, base=part_base, rate_percent=part_rate, rate=part_row, pan=pan,
                certificate=part_certificate, amount=part_amount, date=day, journal_entry=entry,
                posted_account=account)
            # The earlier bills only when this deduction took them in: taxed alone
            # past the single limit, they stay untaxed for the year's limit to catch.
            deduction.covered_bills.set([bill, *untaxed] if caught_up else [bill])
            made.append(deduction)
        # Split at a certificate's amount, the part at the certificate; the other is beside it (parts()).
        return made[0]


def _at_nil(part, base):
    """A part (base, rate, rate row, certificate) at a certificate's nil rate, on some of the base (O618)."""
    part_base, part_rate, _, part_certificate = part
    return bool(part_certificate) and Decimal(part_rate) == 0 and 0 < part_base <= base


def _still_confirmed(section, rate_row):
    """
    `rate_row` read again under its lock, which an edit of the row takes too (review F4): changed as this
    deducted, the row waits for the accountant again and nothing is taken at it.
    """
    lock_rows(rate_row)
    if rate_row.confirmed_on is None:
        raise ValidationError(f"The {section.code} rate from {rate_row.applies_from:%d %b %Y} was changed "
                              "as this deducted; it waits for the accountant's confirmation again.")
    return rate_row


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
    # Facts of the payment, kept as they stood on it: the company's TAN, the challan's name and the section of
    # the Act the deductions were made under, on their month (accounting.statutory_names).
    tan = models.CharField(max_length=10, blank=True, default="", db_default="", editable=False, verbose_name="TAN")
    form = models.CharField(max_length=32, blank=True, default="", db_default="", editable=False, help_text="The challan's name on its day.")
    section_code = models.CharField(max_length=32, blank=True, default="", db_default="", editable=False,
                                    help_text="The section of the Act the deductions were made under.")

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

    def delete(self, *args, **kwargs):
        # As TcsChallan: a payment over to the government happened; a wrong one is voided (review F16).
        raise ValidationError("A challan paid is a fact; void it.")

    @classmethod
    def pay(cls, section, month, date, bank_account, challan_number, bsr_code):
        """Pay over everything deducted under `section` in `month` and not yet paid (challans.pay_over, shared with TCS)."""
        from apps.accounting.challans import pay_over

        month = to_date(month).replace(day=1)
        following = (month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        # A row at a nil rate deducted nothing, so there is nothing of it to pay over (O618).
        waiting = (TdsDeduction.objects.filter(section=section, date__gte=month, date__lt=following, amount__gt=0)
                   .filter(Q(challan__isnull=True) | Q(challan__voided_entry__isnull=False)))
        return pay_over(
            section, waiting, _live, bank_account, date, challan_number,
            what=f"deducted under {section.code} in {month:%B %Y}",
            memo=f"TDS {section.code} for {month:%B %Y}, challan {challan_number}",
            make_challan=lambda total, entry, **kept: cls.objects.create(
                **kept, section=section, month=month, date=to_date(date), bank_account=bank_account,
                challan_number=challan_number, bsr_code=bsr_code, amount=total, journal_entry=entry),
            period=month)

    @serialised("voided_entry")
    @takes_back
    def void(self, on_date=None):
        """The payment did not go through: the tax is owed again, and its deductions wait for another."""
        from apps.accounting.challans import void_challan

        return void_challan(self, on_date)


def challans_paid():
    """
    The entries challans paid tax over by, and those that voided them: money
    through the bank that no Payment carries (accounting.register_bank_movements).
    """
    paid = TdsChallan.objects.values("journal_entry")
    voided = TdsChallan.objects.filter(voided_entry__isnull=False).values("voided_entry")
    return JournalEntry.objects.filter(Q(pk__in=paid) | Q(pk__in=voided))


def tds_return(start, end):
    """
    The quarter's deductions as the return lists them: who, their PAN,
    under what, how much was credited and deducted, and the challan that
    paid it over.

    As the books stood at the quarter's end: a deduction of the quarter
    reversed afterwards is still listed, and its reversal is a row of the
    quarter it was made in, taking the amount back. Listed only while it
    stood today, 700 deducted on 10 June and reversed on 5 July dropped out
    of the April-June return while the ledger held 700 that quarter, and
    the July-September return never said it went.
    """
    start, end = to_date(start), to_date(end)
    deducted = TdsDeduction.objects.filter(date__gte=start, date__lte=end).filter(
        Q(reversed_on__isnull=True) | Q(reversed_on__gt=end))
    taken_back = TdsDeduction.objects.filter(date__lt=start, reversed_on__gte=start, reversed_on__lte=end)
    rows = []
    for row, sign in [(row, 1) for row in deducted.select_related("bill__vendor", "section", "challan")] + \
            [(row, -1) for row in taken_back.select_related("bill__vendor", "section", "challan")]:
        paid = sign > 0 and row.is_paid_over()
        rows.append({
            "deduction": row.pk, "date": row.date if sign > 0 else row.reversed_on,
            "section": period_names("tds", row.date, row.section)["section"], "vendor": row.bill.vendor.name,
            "pan": row.pan or "PANNOTAVBL", "bill": row.bill.number, "base": sign * row.base,
            # + 0: a row at nil taken back is nothing, not -0.00 (O618).
            "rate_percent": row.rate_percent, "amount": sign * row.amount + 0,
            # A lower deduction under section 197 names its certificate (O597); at a nil rate, with nothing
            # deducted, it is listed all the same (O618).
            "certificate": row.certificate,
            "challan": f"{row.challan.bsr_code}/{row.challan.challan_number}" if paid else "",
            "paid_on": row.challan.date if paid else None,
            "reverses": row.date if sign < 0 else None,
        })
    return sorted(rows, key=lambda row: (row["section"], row["date"], row["deduction"], row["amount"] < 0))
