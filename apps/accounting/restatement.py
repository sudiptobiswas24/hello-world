"""
Foreign balances restated at the closing rate (g24, gap A13).

At a balance-sheet date the plant's foreign-currency monetary items are reported at the closing
rate, and the difference to the rate each was booked at goes to the profit and loss:
  - AS 11 (revised 2003), The Effects of Changes in Foreign Exchange Rates, paragraph 11(a) and
    paragraph 13, as notified in the Companies (Accounting Standards) Rules, 2021: what a company
    outside Ind AS reports under. Source: ICAI, https://kb.icai.org/pdfs/69239asb55316-as11.pdf
  - Ind AS 21, paragraphs 23(a) and 28, for a company under the Companies (Indian Accounting
    Standards) Rules, 2015 (listed, or net worth of Rs 250 crore or more, rule 4): the same rule.
Which the plant reports under is for its accountant to confirm; an unlisted plant below the Ind AS
threshold reports under AS 11. Both treat the item the same way, so the code does not ask.

What is restated (`Restatement.make`), each item as it stood at the close of `as_of`:
  - an invoice, bill or note with something due on it (the trading sides register their documents,
    `register_restatement_source`: accounting imports neither);
  - a receipt or payment with money applied to nothing (on the control account it sits on);
  - a bank account kept in a foreign currency (an EEFC account), at its balance in that currency.
Not restated: a customer's down payment or a prepayment to a vendor. An advance for goods or services
is a non-monetary item (AS 11 para 7 and 11(b); Ind AS 21 para 16 and Appendix B, Foreign Currency
Transactions and Advance Consideration): it stays at the rate it came in at, and when it is drawn down
the drawdown leaves it at that rate (settlement.post_drawdown). The brief listed advances; the
standard says otherwise, so they are left out and the accountant is told.

Each item is one line on the restatement and on its entry: the base carried less the base restated,
each rounded, subtracted (O49). The entry reverses on the next day, so the realised
difference when the item is later settled is worked, as it always was, against the base the document
booked (settlement.booked_by reads the document's own entries; this entry is not one of them). The
year's books read the restated figure; the next year's P&L takes the reversal and the realised
difference, which together are what the money moved by after the year end.

A restatement is a fact about what was open on its day: an allocation, a void or a deletion that would
change what was open on or before it is refused while it stands (`refuse_moving_restated`). Its own
reverse is a void, dated under correction_date(), before the restatement has reversed itself.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum

from apps.core.models import (
    AuditModel,
    Company,
    Currency,
    DocumentSequence,
    Party,
    correction_date,
    lock_rows,
    serialised,
    to_date,
)

from .models import Account, AccountingPeriod, JournalEntry, JournalLine, Payment, PaymentDirection, round_money


# What a trading side has that may be open in a foreign currency on a day: `source(day)` gives the
# candidates, each with `restatement_item(day)`. Sales registers its invoices, purchasing its bills.
RESTATEMENT_SOURCES = []


def register_restatement_source(source):
    if source not in RESTATEMENT_SOURCES:
        RESTATEMENT_SOURCES.append(source)


@dataclass
class OpenItem:
    """One foreign item as it stood on a day. `side` is 1 for a debit balance, -1 for a credit."""

    item: models.Model
    label: str  # carried: the base on the account for it, as a positive balance on its side
    party: object
    account: Account
    currency: Currency
    open_amount: Decimal
    carried: Decimal
    side: int


def item_key(instance):
    return instance._meta.label_lower


def is_foreign(currency):
    if currency is None or currency.is_base:
        return False
    return currency.pk != Company.get().base_currency_id


def _voided_on(payment):
    """The day `payment` was voided, or None while it stands."""
    if payment.voided_entry_id:
        return to_date(payment.voided_entry.date)
    if payment.journal_entry_id:
        reversal = payment.journal_entry.reversed_by.order_by("date").first()
        if reversal is not None:
            return to_date(reversal.date)
    return None


def _stood_on(payment, day):
    """Whether `payment` was posted and not yet voided at the close of `day`."""
    voided = _voided_on(payment)
    return bool(payment.posted) and to_date(payment.payment_date) <= day and (voided is None or voided > day)


def document_open_on(document, day, later):
    """
    What of `document` was open at the close of `day`, in its own currency: what is due now, with what has
    settled it replayed by its day. Payments by the day each was applied and the day each was voided; `later`,
    [(day, amount, entries, words)], the rest (a note, a drawdown, a write-off, tax deducted, a discount), each
    signed as it settled the document (what took one back is negative), added back when dated after `day`.
    Refused instead, a credit note dated 2 April kept a USD invoice open on 31 March from being restated at all.
    """
    open_amount = document.amount_due()
    for allocation in document.payment_allocations.select_related("payment__voided_entry", "payment__journal_entry"):
        payment = allocation.payment
        standing_now = not payment.is_voided()
        standing_then = to_date(allocation.date) <= day and _stood_on(payment, day)
        if standing_now and not standing_then:
            open_amount += allocation.amount
        elif standing_then and not standing_now:
            open_amount -= allocation.amount
    for when, amount, _entries, _words in later:
        if when is not None and to_date(when) > day:
            open_amount += amount
    return open_amount


def document_item(document, day, *, party, account, side, later):
    """
    The OpenItem for an invoice, bill or note open on `day`, or None. Its base carried is what it booked
    less the slice of that settled (settlement.slice_of), as the next payment applied to it would read.
    """
    from .settlement import booked_by

    if not document.posted or not document.journal_entry_id or not is_foreign(document.currency):
        return None
    open_amount = document_open_on(document, day, later)
    if open_amount <= 0:
        return None
    total = document.total()
    booked = booked_by(document, account) or Decimal("0")
    settled = max(total - open_amount, Decimal("0"))
    carried = booked - round_money(booked * settled / total) if total else Decimal("0")
    return OpenItem(document, document.number, party, account, document.currency, open_amount, carried, side)


def payment_item(payment, day):
    """The OpenItem for a payment with money applied to nothing at the close of `day`, or None."""
    from .settlement import allocations_of, booked_on

    if not is_foreign(payment.currency) or not _stood_on(payment, day):
        return None
    applied = sum((row.amount for row in allocations_of(payment) if to_date(row.date) <= day), Decimal("0"))
    open_amount = payment.amount - applied
    if open_amount <= 0:
        return None
    account = payment.counterpart_account
    booked = booked_on(payment.journal_entry, account) or Decimal("0")
    carried = booked - round_money(booked * applied / payment.amount)
    # Money in on account is owed back to the customer until applied: a credit on the receivable.
    side = -1 if payment.direction == PaymentDirection.RECEIPT else 1
    return OpenItem(payment, payment.number, payment.party, account, payment.currency, open_amount, carried, side)


def bank_item(account, day):
    """The OpenItem for a bank account kept in a foreign currency, at its balance on `day`, or None."""
    payments = Payment.objects.filter(bank_account=account, posted=True, payment_date__lte=day).select_related(
        "voided_entry", "journal_entry")
    held = sum((payment.amount if payment.is_receipt() else -payment.amount
                for payment in payments if _stood_on(payment, day)), Decimal("0"))
    rows = JournalLine.objects.filter(account=account, entry__posted=True, entry__date__lte=day).aggregate(
        debit=Sum("debit"), credit=Sum("credit"))
    carried = (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))
    if not held and not carried:
        return None
    # Signed as the bank holds it: an overdrawn account is a credit, side and all.
    side = 1 if held >= 0 else -1
    return OpenItem(account, account.code, None, account, account.currency, abs(held), side * carried, side)


def standing_on(instance, day):
    """What standing restatements put on the account for `instance` and had not reversed by the close of `day`."""
    rows = RestatementLine.objects.filter(
        item_type=item_key(instance), item_id=instance.pk, restatement__voided_on__isnull=True,
        restatement__as_of__lt=day, restatement__reverses_on__gt=day).aggregate(total=Sum("difference"))
    return rows["total"] or Decimal("0")


def refuse_posting_behind_a_restatement(document, day):
    """
    Refuse posting a foreign `document` dated on or before a standing restatement's day: it would be open that
    day at its own rate while the restatement valued everything else at the closing one, and the year end would
    miss it. A correction dated anywhere (audit shape): void the restatement and restate, or date it after.
    """
    if not is_foreign(document.currency):
        return
    day = to_date(day)
    standing = (Restatement.objects.filter(voided_on__isnull=True, as_of__gte=day).order_by("as_of").first()
                if day is not None else None)
    if standing is not None:
        raise ValidationError(
            f"{standing.number} restated the foreign balances as they stood on {standing.as_of:%d-%m-%Y}; a "
            f"{document.currency.code} document dated {day:%d-%m-%Y} is not posted behind it. Void "
            f"{standing.number} and restate once this is posted, or date it after {standing.as_of:%d-%m-%Y}.")


def refuse_moving_restated(instances, day, doing):
    """
    Refuse `doing` on `day` to any of `instances` (a document, a payment) a standing restatement restated
    as open on `day` or later: it would change what was open on a day whose figure is posted. Void the
    restatement first (and restate again), or date it after the restatement's day.
    """
    day = to_date(day)
    for instance in instances:
        if instance is None or instance.pk is None:
            continue
        line = (RestatementLine.objects.filter(
            item_type=item_key(instance), item_id=instance.pk, restatement__voided_on__isnull=True,
            restatement__as_of__gte=day).select_related("restatement").order_by("restatement__as_of").first())
        if line is not None:
            raise ValidationError(
                f"{doing}: {line.restatement.number} restated {line.label} as it stood on "
                f"{line.restatement.as_of:%d-%m-%Y}, and that would change it. Date it after "
                f"{line.restatement.as_of:%d-%m-%Y}, or void {line.restatement.number} first.")


class Restatement(AuditModel):
    """Foreign balances restated at the closing rate on a day; reversed on the next day."""

    number = models.CharField(max_length=32, blank=True, editable=False)
    as_of = models.DateField(help_text="The day restated: normally the last day of a period.")
    reverses_on = models.DateField(editable=False, help_text="The day after it, when it reverses.")
    gain_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", editable=False)
    loss_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", editable=False)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", editable=False)
    reversal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+", editable=False)
    voided_on = models.DateField(null=True, blank=True, editable=False)
    void_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", editable=False)
    void_reversal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="+", editable=False)
    memo = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-as_of", "-id"]
        permissions = [("post_restatement", "Can restate foreign balances at the closing rate, and void it")]
        constraints = [
            models.UniqueConstraint(fields=["as_of"], condition=Q(voided_on__isnull=True),
                                    name="one_standing_restatement_a_day"),
            models.UniqueConstraint(fields=["number"], condition=~Q(number=""), name="unique_restatement_number"),
        ]

    def __str__(self):
        return f"{self.number} at {self.as_of}"

    def save(self, *args, **kwargs):
        if self.pk is None and not getattr(self, "_made", False):
            raise ValidationError("A restatement is made with Restatement.make(), which works out what it restates.")
        super().save(*args, **kwargs)

    @serialised("voided_on")
    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self.number} is posted; void it instead.")

    @classmethod
    def candidates(cls, day):
        """What may have been open in a foreign currency on `day`: payments, each side's documents, banks."""
        base = Company.get().base_currency_id
        payments = list(Payment.objects.filter(posted=True, payment_date__lte=day, currency__isnull=False)
                        .exclude(currency_id=base).order_by("pk"))
        documents = [row for source in RESTATEMENT_SOURCES for row in source(day)]
        banks = list(Account.objects.filter(holds_money=True, currency__isnull=False).exclude(currency_id=base)
                     .order_by("pk"))
        return payments, documents, banks

    @classmethod
    def preview(cls, as_of):
        """The items open on `as_of`, as make() would restate them at the rates standing that day (nothing posted)."""
        as_of = to_date(as_of)
        payments, documents, banks = cls.candidates(as_of)
        return cls._items(as_of, payments, documents, banks)

    @staticmethod
    def _items(as_of, payments, documents, banks):
        items = [payment_item(payment, as_of) for payment in payments]
        items += [document.restatement_item(as_of) for document in documents]
        items += [bank_item(bank, as_of) for bank in banks]
        return [item for item in items if item is not None]

    @classmethod
    @transaction.atomic
    def make(cls, as_of, by=None, rates=None, memo=""):
        """
        Restate what was open on `as_of`. `rates`, {currency code: rate}, are the closing rates where the
        rate table's for that day is not the one to use; every rate used is kept on the restatement.
        Refused for a day still to come, for a day a standing restatement already restates, and when
        nothing foreign was open.
        """
        as_of = correction_date(as_of, None, "Foreign balances are not restated as at", "")
        company = Company.get()
        gain, loss = company.fx_gain_account, company.fx_loss_account
        if gain is None or loss is None:
            raise ValidationError("The company has no FX gain or loss account; a restatement has nowhere to post.")
        number = DocumentSequence.next_for("accounting.restatement", as_of, name="Restatements", prefix="FXR-")
        standing = cls.objects.select_for_update(no_key=True).filter(as_of=as_of, voided_on__isnull=True).first()
        if standing is not None:
            raise ValidationError(f"{standing.number} already restates {as_of:%d-%m-%Y}; void it to restate again.")
        payments, documents, banks = cls.candidates(as_of)
        # Held before what each was is read: a payment applied while this reads would be counted open (or
        # not) by a figure that no longer holds. The allocation holds the payment and the document too.
        lock_rows(*payments, *documents)
        items = cls._items(as_of, payments, documents, banks)
        if not items:
            raise ValidationError(f"Nothing in a foreign currency was open on {as_of:%d-%m-%Y}; there is nothing to restate.")
        given = {str(code).upper(): Decimal(str(rate)) for code, rate in (rates or {}).items()}
        used = {}
        for item in items:
            code = item.currency.code
            if code in used:
                continue
            if code in given:
                if given[code] <= 0:
                    raise ValidationError(f"The closing rate for {code} must be above nothing.")
                used[code] = (item.currency, given[code], None)
            else:
                row = item.currency.rates.filter(valid_from__lte=as_of).order_by("-valid_from").first()
                if row is None:
                    raise ValidationError(f"No exchange rate for {code} on {as_of:%d-%m-%Y}: enter the closing rate.")
                used[code] = (item.currency, row.rate, row.valid_from)
        # A valuation at its own day: it stands that day alone (review F1, F2). Reversed at the period's end
        # instead, a second restatement in the period stacked on the first, and an invoice paid between the
        # restatement and the period end left its unrealised difference on the receivable with nothing open.
        reverses_on = as_of + datetime.timedelta(days=1)

        restatement = cls(number=number, as_of=as_of, reverses_on=reverses_on, gain_account=gain,
                          loss_account=loss, memo=memo[:255], created_by=by, updated_by=by)
        restatement._made = True
        restatement.save()
        for currency, rate, valid_from in used.values():
            RestatementRate.objects.create(restatement=restatement, currency=currency, rate=rate, rate_from=valid_from)
        entry = None
        lines = []
        for item in items:
            rate = used[item.currency.code][1]
            signed_open = item.side * item.open_amount
            restated = round_money(signed_open * rate)
            carried = item.side * item.carried
            if item.account is not item.item:
                # Worked against the base it is carried at that day: what it booked, and any restatement still
                # standing then (a bank's ledger balance already holds it).
                carried += standing_on(item.item, as_of)
            difference = restated - carried
            lines.append(RestatementLine(
                restatement=restatement, item_type=item_key(item.item), item_id=item.item.pk, label=item.label,
                party=item.party, account=item.account, currency=item.currency, side=item.side,
                open_amount=item.open_amount, rate=rate, carried_base=carried, restated_base=restated,
                difference=difference))
        RestatementLine.objects.bulk_create(lines)
        moved = [line for line in lines if line.difference]
        if moved:
            entry = JournalEntry.objects.create(date=as_of, reference=number,
                                                memo=f"{number}: foreign balances restated at the closing rate")
            for line in moved:
                words = f"{number} {line.label}: {line.open_amount} {line.currency.code} at {line.rate.normalize():f}"
                size = abs(line.difference)
                to_account = line.difference > 0  # the item's base rises: debit it, a gain
                JournalLine.objects.create(entry=entry, account=line.account, party=line.party,
                                           debit=size if to_account else Decimal("0"),
                                           credit=Decimal("0") if to_account else size, description=words[:255])
                JournalLine.objects.create(entry=entry, account=gain if to_account else loss, party=line.party,
                                           debit=Decimal("0") if to_account else size,
                                           credit=size if to_account else Decimal("0"), description=words[:255])
            entry.post()
            restatement.journal_entry = entry
            restatement.reversal_entry = entry.create_reversal(
                entry_date=reverses_on, memo=f"{number} reversed: the next period opens at the booked rates")
            restatement.save(update_fields=["journal_entry", "reversal_entry", "updated_at"])
        else:
            AccountingPeriod.refuse_closed(as_of, f"foreign balances are not restated as at {as_of}")
        return restatement

    @serialised("voided_on")
    def void(self, on_date=None, by=None):
        """
        Take the restatement back: its entry negated on `on_date` (by default its own day), and its reversal
        taken back on the reversal's day, so from `on_date` the books read as if it had not been made. Only
        before it reversed itself: after that nothing of it stands to take back, and its period's figure is
        changed by reopening the period and voiding it on its own day.
        """
        if self.voided_on is not None:
            raise ValidationError(f"{self.number} is already void.")
        day = correction_date(on_date, self.as_of, f"{self.number} is not voided on", "it restates that day")
        if day >= self.reverses_on:
            raise ValidationError(
                f"{self.number} is not voided on {day:%d-%m-%Y}: it reversed itself on {self.reverses_on:%d-%m-%Y}, "
                f"so nothing of it stands then. Reopen the period and void it on {self.as_of:%d-%m-%Y}.")
        if self.journal_entry_id:
            negation = JournalEntry.objects.create(date=day, reference=self.number, memo=f"{self.number} void")
            for line in self.journal_entry.lines.all():
                JournalLine.objects.create(entry=negation, account=line.account, party=line.party, debit=line.credit,
                                           credit=line.debit, description=line.description)
            negation.post(correcting=True)
            self.void_entry = negation
            self.void_reversal_entry = self.reversal_entry.create_reversal(
                entry_date=self.reversal_entry.date, memo=f"{self.number} void: its reversal taken back")
        self.voided_on = day
        self.updated_by = by or self.updated_by
        super().save(update_fields=["voided_on", "void_entry", "void_reversal_entry", "updated_by", "updated_at"])
        return self


class RestatementRate(AuditModel):
    """A closing rate a restatement used, kept as it was used."""

    restatement = models.ForeignKey(Restatement, on_delete=models.PROTECT, related_name="rates", editable=False)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+", editable=False)
    rate = models.DecimalField(max_digits=18, decimal_places=8, editable=False)
    rate_from = models.DateField(null=True, blank=True, editable=False,
                                 help_text="The rate table's row it was read from; empty when given by hand.")

    class Meta:
        ordering = ["restatement", "currency"]
        constraints = [models.UniqueConstraint(fields=["restatement", "currency"], name="one_rate_per_restatement")]


class RestatementLine(AuditModel):
    """One item restated: what was open, the base carried, the base restated and the difference posted."""

    restatement = models.ForeignKey(Restatement, on_delete=models.PROTECT, related_name="lines", editable=False)
    item_type = models.CharField(max_length=64, editable=False, help_text="sales.invoice, accounting.payment, ...")
    item_id = models.PositiveBigIntegerField(editable=False)
    label = models.CharField(max_length=64, editable=False)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", editable=False)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+", editable=False)
    side = models.SmallIntegerField(editable=False, help_text="1: a debit balance; -1: a credit balance.")
    open_amount = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    rate = models.DecimalField(max_digits=18, decimal_places=8, editable=False)
    carried_base = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                                       help_text="The base on the account for it, signed as a balance (debit +).")
    restated_base = models.DecimalField(max_digits=18, decimal_places=2, editable=False)
    difference = models.DecimalField(max_digits=18, decimal_places=2, editable=False,
                                     help_text="Restated less carried: + is posted as a gain, - as a loss.")

    class Meta:
        ordering = ["restatement", "id"]
        indexes = [models.Index(fields=["item_type", "item_id"], name="restatement_line_item")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("A restatement's lines are what it posted; void it instead.")
        super().save(*args, **kwargs)
