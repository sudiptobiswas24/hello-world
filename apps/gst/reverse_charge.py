"""
Reverse charge after the books: the self-invoice, the payment voucher, the
tax paid in cash, and the electronic cash ledger it is paid through.

The law, as it stood on 26 October 2026 (the report to the tax adviser lists
what was not confirmed from the Gazette text):

- Self-invoice. CGST Act s.31(3)(f), as amended by the Finance (No. 2) Act
  2024 (s.117, in force 1 November 2024, notification 17/2024-CT): a
  registered person liable under s.9(3) or 9(4) issues an invoice for what
  it receives from a supplier "who is not registered on the date of receipt".
  CGST Rule 47A (notification 20/2024-CT, 8 October 2024, from 1 November
  2024): within thirty days from the date of receipt of the supply. Rule 46
  sets its contents and a consecutive serial number in one or more series.
  Circular 211/5/2024-GST: the credit's s.16(4) year is the year the
  self-invoice is issued.
- Payment voucher. s.31(3)(g) and Rule 52: at the time of paying the supplier
  of any supply under reverse charge, registered or not.
- Paid in cash. s.2(82): output tax excludes tax payable on reverse charge;
  s.49(4): the electronic credit ledger pays only output tax. So reverse
  charge is paid from the electronic cash ledger (s.49(1), deposited by
  challan, Rule 87 and form PMT-06, identified by its CPIN), and GSTR-3B
  table 6.1 shows it in the cash column.
- Credit after payment. Rule 36(1)(b): credit on a self-invoice is "subject
  to the payment of tax"; s.16(2)(c) generally. So 4(A)(3) reads what was paid
  (gst.returns), never what was owed.

Decided here (g24 brief): a self-invoice is numbered from its own series
(gst.self_invoice), made from the bill, refused past the thirty days, and
taken back only by a cancellation; the reverse-charge tax is cleared only by
a GstCashPayment; a debit note that takes the whole supply back cancels its
self-invoice; a void of the supplier's payment cancels its voucher.
"""

import datetime
import re
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine, Payment, round_money
from apps.core.models import (
    AuditModel,
    DocumentSequence,
    correction_date,
    day_that_has_come,
    lock_rows,
    numbering_first,
    serialised,
    takes_back,
    to_date,
)
from apps.purchasing.models import Bill, BillLineTax, BillPayment

ZERO = Decimal("0")
HEADS = ("igst", "cgst", "sgst", "cess")
# CGST Rule 47A: within thirty days from the date of receipt of the supply.
SELF_INVOICE_DAYS = 30
CPIN = re.compile(r"^\d{14}$")


def reported_bills():
    """The bills a return reports: posted, no prepayment, nothing brought in at go-live (as returns._bills)."""
    return Bill.objects.filter(posted=True, is_prepayment=False, is_opening_balance=False).exclude(
        debits__is_prepayment=True).exclude(debits__is_opening_balance=True, corrects_old_supply=False)


def _base(row, bill):
    return round_money(row.amount * (bill.exchange_rate or Decimal("1")))


def reverse_charge_figures(bill):
    """{taxable, igst, cgst, sgst, cess} the bill bore under reverse charge, in base, as it posted."""
    figures = {head: ZERO for head in HEADS} | {"taxable": ZERO}
    for line in bill.lines.all():
        rows = [row for row in line.recorded_taxes.all() if row.reverse_charge and row.gst_head in HEADS]
        if rows:
            figures["taxable"] += round_money(line.net_amount() * (bill.exchange_rate or Decimal("1")))
        for row in rows:
            figures[row.gst_head] += _base(row, bill)
    return figures


def _left_after_notes(bill):
    """The bill's reverse-charge tax less what its posted debit notes took back, in base."""
    left = sum((amount for head, amount in reverse_charge_figures(bill).items() if head != "taxable"), ZERO)
    for note in bill.debit_notes.filter(posted=True):
        left -= sum((amount for head, amount in reverse_charge_figures(note).items() if head != "taxable"), ZERO)
    return left


def received_on(bill):
    """
    The day the supply was received, which Rule 47A's thirty days count from: the first goods receipt of the
    order lines the bill bills, when there is one; the bill's own date only when there is none (a service, or
    goods billed with no receipt). Never the earlier of the two: a bill dated 1 September for goods received on
    the 20th ran the limit out on 1 October instead of 20 October (review_rcm F3).
    """
    from apps.purchasing.models import _receipt_dates

    received = [day for line in bill.lines.all() if line.order_line_id
                for day, quantity in _receipt_dates(line.order_line) if quantity > 0]
    return min(received) if received else to_date(bill.bill_date)


class SelfInvoice(AuditModel):
    """
    The invoice the company issues itself for a supply under reverse charge from a supplier not registered
    on the date of receipt (s.31(3)(f), Rule 47A). Posts nothing: the bill booked the tax. Cancelled, never
    edited or deleted.
    """

    number = models.CharField(max_length=32, unique=True, editable=False)
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="self_invoices")
    date = models.DateField(help_text="The day it was issued: within thirty days of receiving the supply.")
    received_on = models.DateField(editable=False, help_text="The day the supply was received, as it was issued.")
    supplier_name = models.CharField(max_length=255, editable=False)
    place_of_supply = models.CharField(max_length=2, blank=True, editable=False)
    taxable = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    igst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    sgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cess = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cancelled_on = models.DateField(null=True, blank=True, editable=False)
    cancellation_reason = models.CharField(max_length=255, blank=True, editable=False)

    FIXED = ("number", "bill_id", "date", "received_on", "supplier_name", "place_of_supply", "taxable",
             *HEADS)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["bill"], condition=Q(cancelled_on__isnull=True),
                                               name="one_standing_self_invoice_per_bill")]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = SelfInvoice.objects.get(pk=self.pk)
            if any(getattr(before, field) != getattr(self, field) for field in self.FIXED):
                raise ValidationError("A self-invoice issued is not changed; cancel it and issue another.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A self-invoice issued is a document of the series; cancel it.")

    def tax(self):
        return sum((getattr(self, head) for head in HEADS), ZERO)

    @classmethod
    def issue(cls, bill, on_date=None):
        with transaction.atomic():
            lock_rows(bill)  # one self-invoice per bill: a second issue waits, then finds the first
            day = day_that_has_come(on_date or timezone.localdate(), f"A self-invoice for {bill} is not issued on")
            if not bill.posted or bill.is_debit_note() or bill.is_prepayment or bill.is_opening_balance:
                raise ValidationError(f"{bill} is not a posted bill for a supply received; a self-invoice is "
                                      "made from one.")
            figures = reverse_charge_figures(bill)
            if not any(figures[head] for head in HEADS):
                raise ValidationError(f"{bill.number} bears no tax under reverse charge; its supplier's invoice is "
                                      "the document.")
            if bill.party_gstin:
                raise ValidationError(
                    f"{bill.vendor} was registered ({bill.party_gstin}) when {bill.number} posted: the supplier's "
                    "own tax invoice is the document, and no self-invoice is issued (s.31(3)(f)).")
            if cls.objects.filter(bill=bill, cancelled_on__isnull=True).exists():
                raise ValidationError(f"{bill.number} has a self-invoice standing; cancel it before issuing another.")
            if _left_after_notes(bill) <= 0:
                raise ValidationError(f"Debit notes took back all of {bill.number}'s supply; there is nothing "
                                      "received to invoice.")
            received = received_on(bill)
            if day < received:
                raise ValidationError(f"A self-invoice for {bill.number} is not issued on {day}: the supply was "
                                      f"received on {received}.")
            last = received + datetime.timedelta(days=SELF_INVOICE_DAYS)
            if day > last:
                raise ValidationError(
                    f"The self-invoice for {bill.number} was due within {SELF_INVOICE_DAYS} days of receiving the "
                    f"supply on {received:%d %b %Y}, by {last:%d %b %Y} (CGST Rule 47A); it is not issued on "
                    f"{day:%d %b %Y} here. Take it to the tax adviser: a late one carries interest and may "
                    "carry a penalty.")
            number = DocumentSequence.next_for("gst.self_invoice", day, name="Self-invoices (reverse charge)",
                                               prefix="SI-")
            return cls.objects.create(
                number=number, bill=bill, date=day, received_on=received, supplier_name=bill.vendor.name,
                place_of_supply=bill.place_of_supply, taxable=figures["taxable"],
                **{head: figures[head] for head in HEADS})

    @serialised("cancelled_on")
    @takes_back
    def cancel(self, reason="", on_date=None):
        if self.cancelled_on:
            raise ValidationError(f"{self.number} was cancelled on {self.cancelled_on}.")
        reason = str(reason or "").strip()
        if not reason:
            raise ValidationError({"reason": "Say why the self-invoice is cancelled."})
        self.cancelled_on = correction_date(on_date, self.date, f"{self.number} is not cancelled on", "it was issued")
        self.cancellation_reason = reason[:255]
        super().save(update_fields=["cancelled_on", "cancellation_reason", "updated_at"])
        return self


def cancel_with_a_whole_debit_note(note):
    """A debit note that leaves its bill no reverse-charge tax cancels the bill's self-invoice, on its date."""
    bill = note.debits
    if bill is None:
        return
    standing = SelfInvoice.objects.filter(bill=bill, cancelled_on__isnull=True).first()
    if standing is not None and _left_after_notes(bill) <= 0:
        standing.cancel(f"Supply wholly taken back by debit note {note.number}",
                        on_date=max(to_date(note.bill_date), standing.date))


def awaiting_self_invoice(day=None):
    """
    [(bill, received, last day)] for every reverse-charge supply from a supplier unregistered when its bill
    posted that has no self-invoice standing and was not wholly debited back, oldest first.
    """
    standing = SelfInvoice.objects.filter(cancelled_on__isnull=True).values("bill")
    bills = (reported_bills().filter(debits__isnull=True, party_gstin="",
                                     lines__recorded_taxes__reverse_charge=True)
             .exclude(pk__in=standing).distinct().order_by("bill_date", "id"))
    found = []
    for bill in bills:
        if _left_after_notes(bill) <= 0:
            continue
        received = received_on(bill)
        found.append((bill, received, received + datetime.timedelta(days=SELF_INVOICE_DAYS)))
    return found


# -- The payment voucher -------------------------------------------------------

class PaymentVoucher(AuditModel):
    """
    The voucher issued at the time of paying the supplier of a supply under reverse charge (s.31(3)(g),
    Rule 52): one per payment and bill it settles. Cancelled with the payment's void, never edited.
    """

    number = models.CharField(max_length=32, unique=True, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="+")
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="payment_vouchers")
    date = models.DateField(editable=False, help_text="The payment's date: issued at the time of paying.")
    amount = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                                 help_text="What the payment settled of the bill, in base.")
    igst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    sgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cess = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cancelled_on = models.DateField(null=True, blank=True, editable=False)

    FIXED = ("number", "payment_id", "bill_id", "date", "amount", *HEADS)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["payment", "bill"], condition=Q(cancelled_on__isnull=True),
                                               name="one_standing_voucher_per_payment_and_bill"),
                       models.CheckConstraint(check=Q(amount__gt=0), name="payment_voucher_positive")]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = PaymentVoucher.objects.get(pk=self.pk)
            if any(getattr(before, field) != getattr(self, field) for field in self.FIXED):
                raise ValidationError("A payment voucher issued is not changed; it is cancelled with its payment.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A payment voucher issued is a document of the series; void its payment.")

    @classmethod
    def issue_for(cls, payment):
        """A voucher for each reverse-charge bill the payment settles that has none standing."""
        with transaction.atomic():
            lock_rows(payment)
            if not payment.posted or payment.is_voided():
                raise ValidationError(f"Payment {payment.number or payment.pk} is not a posted payment standing.")
            made = []
            for allocation in _unvouchered(BillPayment.objects.filter(payment=payment)):
                bill = allocation.bill
                figures = reverse_charge_figures(bill)
                whole = bill.posted_total or bill.total()
                share = {head: round_money(figures[head] * allocation.amount / whole) if whole else ZERO
                         for head in HEADS}
                number = DocumentSequence.next_for("gst.payment_voucher", payment.payment_date,
                                                   name="Payment vouchers (reverse charge)", prefix="PV-")
                made.append(cls.objects.create(
                    number=number, payment=payment, bill=bill, date=payment.payment_date,
                    amount=round_money(allocation.amount * (payment.exchange_rate or Decimal("1"))), **share))
            if not made:
                raise ValidationError(f"Payment {payment.number} settles no reverse-charge bill without a voucher.")
            return made


def _unvouchered(allocations):
    standing = PaymentVoucher.objects.filter(cancelled_on__isnull=True)
    return [allocation for allocation in allocations.select_related("bill", "payment").order_by("id")
            if allocation.amount > 0 and not allocation.bill.is_debit_note()
            and any(reverse_charge_figures(allocation.bill)[head] for head in HEADS)
            and not standing.filter(payment_id=allocation.payment_id, bill_id=allocation.bill_id).exists()]


def awaiting_payment_voucher():
    """Allocations of standing payments to reverse-charge bills with no voucher standing."""
    allocations = BillPayment.objects.filter(
        payment__posted=True, payment__voided_entry__isnull=True,
        bill__lines__recorded_taxes__reverse_charge=True).distinct()
    return [allocation for allocation in _unvouchered(allocations) if not allocation.payment.is_voided()]


def cancel_vouchers_of(payment, on_date):
    """A void follow-up (accounting.register_void_follow_up): the payment did not happen, nor its voucher."""
    for voucher in PaymentVoucher.objects.filter(payment=payment, cancelled_on__isnull=True).order_by("id"):
        voucher.cancelled_on = max(to_date(on_date), voucher.date)
        voucher.save(update_fields=["cancelled_on", "updated_at"])


# -- The tax paid in cash ------------------------------------------------------

class GstCashPayment(AuditModel):
    """
    One challan (PMT-06) paying a month's reverse-charge tax in cash: deposited into the electronic cash
    ledger from the bank, and set off from it against the tax the bills owed. Dr cash ledger / Cr bank, and
    Dr each kept reverse-charge account / Cr cash ledger, by head. Voided, never edited.
    """

    month = models.DateField(help_text="The month whose reverse-charge tax it pays (its first day).")
    date = models.DateField(help_text="The day it was paid.")
    cpin = models.CharField(max_length=14, unique=True, help_text="The challan's CPIN, 14 digits.")
    bank_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    cash_ledger_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="+",
        help_text="The account that stands for the electronic cash ledger.")
    igst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    sgst = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    cess = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", editable=False)
    voided_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", editable=False)

    FIXED = ("month", "date", "cpin", "bank_account_id", "cash_ledger_account_id", *HEADS, "journal_entry_id")

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"CPIN {self.cpin}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            before = GstCashPayment.objects.get(pk=self.pk)
            if any(getattr(before, field) != getattr(self, field) for field in self.FIXED):
                raise ValidationError("A challan is not changed once paid; void it.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A challan paid is a fact; void it.")

    def total(self):
        return sum((getattr(self, head) for head in HEADS), ZERO)

    @classmethod
    def pay(cls, month, date, bank_account, cash_ledger_account, cpin):
        """Pay in cash everything owed under reverse charge in `month` and not yet paid."""
        from apps.accounting.money import refuse_as_money_account

        from .returns import month as month_range

        start, end = month_range(f"{to_date(month):%Y-%m}") if not isinstance(month, str) else month_range(month[:7])
        cpin = str(cpin or "").strip()
        if not CPIN.match(cpin):
            raise ValidationError({"cpin": "A CPIN is the challan's 14 digits."})
        if cash_ledger_account.account_type != AccountType.ASSET or cash_ledger_account.pk == bank_account.pk:
            raise ValidationError({"cash_ledger_account": "The electronic cash ledger is money the government "
                                                          "holds for the company: an asset account of its own."})
        day = day_that_has_come(date, "A challan is not paid on")
        with transaction.atomic():
            refuse_as_money_account(bank_account)
            numbering_first()  # a raw row lock, after the company's numbering row (O257)
            waiting = _waiting(start, end)
            list(BillLineTax.objects.select_for_update(no_key=True).filter(pk__in=list(
                waiting.values_list("pk", flat=True))).order_by("pk").values_list("pk", flat=True))
            rows = list(_waiting(start, end).select_related("line__bill").order_by("pk"))  # read again, held
            if not rows:
                raise ValidationError(f"No reverse-charge tax of {start:%B %Y} is waiting to be paid.")
            latest = max(to_date(row.line.bill.bill_date) for row in rows)
            if day < latest:
                raise ValidationError(f"A challan pays tax already owed: the last of it is dated {latest:%d %b %Y}, "
                                      f"after {day:%d %b %Y}.")
            by_head, by_account, signed = defaultdict(Decimal), defaultdict(Decimal), []
            for row in rows:
                bill = row.line.bill
                amount = _base(row, bill) * (-1 if bill.is_debit_note() else 1)
                if row.owed_account_id is None:
                    raise ValidationError(f"{bill.number} kept no account its reverse charge was owed into.")
                by_head[row.gst_head] += amount
                by_account[row.owed_account_id] += amount
                signed.append((row, amount))
            if any(amount < 0 for amount in by_head.values()) or sum(by_head.values(), ZERO) <= 0:
                raise ValidationError(
                    f"Reverse-charge tax of {start:%B %Y} nets to "
                    + ", ".join(f"{head.upper()} {amount}" for head, amount in sorted(by_head.items()))
                    + ": more was taken back than owed, and nothing is paid in cash. Take it to the tax adviser.")
            memo = f"GST reverse charge for {start:%B %Y}, CPIN {cpin}"
            entry = JournalEntry.objects.create(date=day, reference=cpin, memo=memo)
            total = sum(by_head.values(), ZERO)
            for head in HEADS:
                if by_head.get(head):
                    JournalLine.objects.create(entry=entry, account=cash_ledger_account, debit=by_head[head],
                                               description=f"{head.upper()} deposited, CPIN {cpin}")
            JournalLine.objects.create(entry=entry, account=bank_account, credit=total, description=memo)
            for account_id, amount in sorted(by_account.items()):
                if amount:
                    JournalLine.objects.create(entry=entry, account_id=account_id, debit=max(amount, ZERO),
                                               credit=max(-amount, ZERO), description=memo)
            for head in HEADS:
                if by_head.get(head):
                    JournalLine.objects.create(entry=entry, account=cash_ledger_account, credit=by_head[head],
                                               description=f"{head.upper()} set off against reverse charge")
            entry.post()
            payment = cls.objects.create(month=start, date=day, cpin=cpin, bank_account=bank_account,
                                         cash_ledger_account=cash_ledger_account, journal_entry=entry,
                                         **{head: by_head.get(head, ZERO) for head in HEADS})
            GstCashPaymentRow.objects.bulk_create([
                GstCashPaymentRow(payment=payment, row=row, head=row.gst_head, account_id=row.owed_account_id,
                                  amount=amount) for row, amount in signed])
            return payment

    @serialised("voided_entry")
    @takes_back
    def void(self, on_date=None):
        """The challan did not go through: the tax is owed again, and its rows wait for another."""
        from apps.accounting.challans import void_challan

        return void_challan(self, on_date)


class GstCashPaymentRow(models.Model):
    """One reverse-charge tax a challan paid, at what it paid it, in base: a fact of the payment."""

    payment = models.ForeignKey(GstCashPayment, on_delete=models.PROTECT, related_name="rows")
    row = models.ForeignKey(BillLineTax, on_delete=models.PROTECT, related_name="+")
    head = models.CharField(max_length=8)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.payment} {self.head} {self.amount}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("What a challan paid is a fact; void the challan.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("What a challan paid is a fact; void the challan.")


def _waiting(start, end):
    paid = GstCashPaymentRow.objects.filter(payment__voided_entry__isnull=True).values("row")
    return BillLineTax.objects.filter(
        reverse_charge=True, gst_head__in=HEADS, line__bill__in=reported_bills().filter(
            bill_date__gte=start, bill_date__lte=end)).exclude(pk__in=paid)


def paid_in_cash(start, end):
    """{head: amount} paid by challans standing for the months from `start` to `end`: what 6.1 and 4(A)(3) read."""
    paid = {head: ZERO for head in HEADS}
    for payment in GstCashPayment.objects.filter(month__gte=start.replace(day=1), month__lte=end,
                                                 voided_entry__isnull=True):
        for head in HEADS:
            paid[head] += getattr(payment, head)
    return paid


def cash_ledger(account):
    """
    The electronic cash ledger as the books hold it: by head, what was deposited (credit to the ledger) and
    what was set off from it (debit), the voids taking both back, against the account's balance.
    """
    heads = {head: {"deposited": ZERO, "set_off": ZERO} for head in HEADS}
    for payment in GstCashPayment.objects.filter(cash_ledger_account=account):
        sign = 0 if payment.voided_entry_id else 1
        for head in HEADS:
            heads[head]["deposited"] += sign * getattr(payment, head)
            heads[head]["set_off"] += sign * getattr(payment, head)
    for totals in heads.values():
        totals["balance"] = totals["deposited"] - totals["set_off"]
    lines = JournalLine.objects.filter(account=account, entry__posted=True)
    ledger = sum((line.debit - line.credit for line in lines), ZERO)
    balance = sum((totals["balance"] for totals in heads.values()), ZERO)
    return {"account": account, "heads": heads, "balance": balance, "ledger": ledger,
            "difference": ledger - balance}


def ledger_accounts():
    """What the cash-ledger view may be asked about: the cash ledger accounts and the reverse-charge payables."""
    owed = BillLineTax.objects.filter(reverse_charge=True).values("owed_account")
    return Account.objects.filter(Q(pk__in=GstCashPayment.objects.values("cash_ledger_account")) | Q(pk__in=owed))


def cash_ledger_accounts():
    return Account.objects.filter(pk__in=GstCashPayment.objects.values("cash_ledger_account")).order_by("code")


def challans_paid():
    """The entries GST challans moved money by, and the voids (accounting.register_bank_movements)."""
    paid = GstCashPayment.objects.values("journal_entry")
    voided = GstCashPayment.objects.filter(voided_entry__isnull=False).values("voided_entry")
    return JournalEntry.objects.filter(Q(pk__in=paid) | Q(pk__in=voided))
