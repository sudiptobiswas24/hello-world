"""
Realised exchange differences on settlement.

A foreign-currency document is booked to the ledger at the rate on its
own date and settled at the rate on the payment's date. In the document's
own currency it is square — the customer owes 1,000 EUR and paid 1,000
EUR — but in base currency the two sides do not match, and the control
account is left holding the difference forever.

That difference is not an error to be hidden. It is a realised gain or
loss: the company genuinely received more or fewer pounds than it
expected when it booked the sale, because the rate moved while the money
was outstanding. It belongs in the P&L.

Lives in Accounting because Sales and Purchasing both need it and
neither may import the other — the same reason the tax mixins and
ChargeType ended up here.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import Company, to_date

from .models import JournalEntry, JournalLine, PaymentDirection, round_money


def settlement_difference(amount, document_rate, payment_rate, *, document_base=None, payment_base=None):
    """
    The base-currency difference left on the control account.

    Positive means the document was booked at a higher rate than it
    settled at; which way that reads as a gain or a loss depends on
    whether the control account is a receivable or a payable.

    It is the base each side booked for `amount`, each rounded as it was
    booked, subtracted (O49). The rate difference times the amount,
    rounded, is a third figure neither side holds: 100.09 EUR booked at
    1.12345 (112.45) and paid at 1.09876 (109.97) cleared 2.47 of the
    2.48 between them and left a paisa on the receivable for good. Each
    side's base is its slice of what it booked when that is known
    (`document_base`, `payment_base`: slice_of), so the part that settles
    the last of either takes what is left of it (review F1).
    """
    document_rate = document_rate or Decimal("1")
    payment_rate = payment_rate or Decimal("1")
    if document_rate == payment_rate:
        return Decimal("0")
    amount = Decimal(amount)
    document_base = round_money(amount * document_rate) if document_base is None else document_base
    payment_base = round_money(amount * payment_rate) if payment_base is None else payment_base
    return document_base - payment_base


def booked_on(entry, control_account):
    """The base an entry put on its control account, whichever side: what the document or payment booked."""
    if entry is None:
        return None
    lines = [line for line in entry.lines.all() if line.account_id == control_account.pk]
    if not lines:
        return None
    return abs(sum((line.debit - line.credit for line in lines), Decimal("0")))


def slice_of(booked, total, before, amount):
    """
    The base of `amount` of a document (or a payment) worth `total` and
    booked at `booked`, `before` of it settled already: the base booked to
    what is settled after it, less the base booked to what was settled
    before, each rounded on its own. The slices telescope, so the part that
    settles the last of it takes exactly what is left of what it booked
    (O49, review F1): 100.09 EUR booked at 112.45 and settled 50 + 50.09
    cleared 56.17 + 56.27 and left a paisa that nothing explained.
    """
    total, before, amount = Decimal(total), Decimal(before), Decimal(amount)
    if not total:
        return Decimal("0")
    after = min(before + amount, total)
    before = max(min(before, total), Decimal("0"))
    return round_money(booked * after / total) - round_money(booked * before / total)


def _post_exchange_difference(
    *, party, control_account, difference, date, reference, memo, is_receivable,
    doing="Settling this at a different rate than it was booked at",
):
    """`difference`: base booked on the document less base cleared off it."""
    if not difference:
        return None

    company = Company.get()
    is_gain = (difference < 0) if is_receivable else (difference > 0)
    account = company.fx_gain_account if is_gain else company.fx_loss_account
    if account is None:
        side = "gain" if is_gain else "loss"
        raise ValidationError(
            f"{doing} leaves {abs(difference)} on {control_account.code}, which is a "
            f"realised exchange {side}, and the company has no FX {side} account configured."
        )

    size = abs(difference)
    # Clear the residue off the control account, whichever side it sits on.
    control_debit = size if (difference < 0) == is_receivable else Decimal("0")
    control_credit = size - control_debit

    entry = JournalEntry.objects.create(date=date, reference=reference, memo=memo)
    JournalLine.objects.create(
        entry=entry, account=control_account, party=party,
        debit=control_debit, credit=control_credit, description=memo,
    )
    JournalLine.objects.create(
        entry=entry, account=account, party=party,
        debit=control_credit, credit=control_debit, description=memo,
    )
    entry.post()
    return entry


class RealisedOnItsOwnDay:
    """
    What a payment applied to a document realises, on the day it is applied.
    InvoicePayment and BillPayment share it, each saying through
    `settles()` what it settles: (document, its date, party, control
    account, is_receivable), and each keeping `date`, `fx_entry` and
    `fx_released_entry`.

    The exchange difference was dated on the payment's own day. A foreign
    receipt of 15 February, applied in March once February had closed,
    could then never be applied at all: its entry fell in the closed month.
    The difference is realised when the payment is applied, so it is dated
    on the allocation's day: the day given, or today. Never before the
    payment or the document (the money cannot settle what it predates),
    and never a day to come (correction_date), and the period it falls in
    must be open, as for any entry.

    The edit and the delete are dated the same way: re-pointing or
    re-sizing an allocation re-states its difference on the day of the
    edit, the old one reversed and the new one posted that day; deleting
    it reverses the difference on the day it is deleted. Re-stated on the
    first day instead, an edit to a closed month's allocation could not be
    made either.
    """

    def settles(self):
        raise NotImplementedError

    def outstanding_for_it(self):
        """
        What the document this allocation now names leaves room for: its
        amount due, plus what this allocation as stored already takes off
        that same document. Re-pointed to another one, it took nothing off
        it yet (O54): 1,000 moved from X (1,000) to Y (500) was measured
        against X's 1,000 added back to Y's 500, and Y read -500 due.
        """
        document, *_ = self.settles()
        stored = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        here = stored.amount if stored is not None and stored.settles()[0].pk == document.pk else Decimal("0")
        return document.amount_due() + here

    def day_applied(self):
        """
        The day this allocation is made or re-stated: its `date` when one is
        given (on an edit, when it is changed); made afresh with none, today;
        edited with none, the day it was made.
        """
        from apps.core.models import correction_date, to_date

        document, document_date, *_ = self.settles()
        stored = (type(self).objects.filter(pk=self.pk).values_list("date", flat=True).first()
                  if self.pk else None)
        if stored is not None and self.date == stored:
            # An edit that gives no new day keeps the day it was made (O163);
            # its re-statement is posted there, so a closed month asks for one.
            return stored
        given = self.date if self.date != stored else None
        verb = "received" if self.payment.direction == PaymentDirection.RECEIPT else "paid"
        earliest, since = max(
            (to_date(self.payment.payment_date), f"it was {verb}"),
            (to_date(document_date), f"{document.number or document} is dated"),
            *([(stored, "it was last applied")] if stored else []),
        )
        return correction_date(given, earliest, f"{self.payment.number} is not applied to "
                                                f"{document.number or document} on", since)

    def realise_exchange_difference(self, previous):
        """
        Reverse what this allocation realised before (`previous`, its old
        entry) and realise what it realises now, both on its own day.
        """
        document, _, party, control_account, is_receivable = self.settles()
        if previous is not None:
            previous.create_reversal(entry_date=self.date, memo=f"Re-stating exchange difference on {self}")
        payment_base = self.payment_slice(control_account)
        difference = settlement_difference(
            self.amount, document.exchange_rate, self.payment.exchange_rate,
            document_base=self.document_slice(document, control_account),
            payment_base=payment_base)
        entry = _post_exchange_difference(
            party=party, control_account=control_account, difference=difference,
            date=self.date, reference=document.number,
            memo=f"Exchange difference settling {document.number}", is_receivable=is_receivable,
        )
        # What this allocation cleared off each side, kept as the fact the
        # next allocation of either reads (O209): the document's side is what
        # the payment put on the control account plus the difference posted.
        self.document_base = payment_base + difference
        self.payment_base = payment_base
        fields = ["document_base", "payment_base", "updated_at"]
        if entry is not None:
            self.fx_entry = entry
            fields.append("fx_entry")
        super(RealisedOnItsOwnDay, self).save(update_fields=fields)

    def document_slice(self, document, control_account):
        """
        The base this allocation clears off its document: what the document
        booked for all of it settled so far, less what the others still
        standing cleared, as each recorded it (O209). Asked once this
        allocation is saved, so amount_due() already counts it.

        Measured as a slice against the amount settled before it
        (slice_of), a middle allocation deleted or re-pointed took its own
        slice back and left the later ones' slices cut against a total that
        no longer held: 100.09 EUR booked at 112.45, applied 30 + 30 + 40.09,
        the middle 30 deleted and applied again, cleared 33.70 + 45.04 +
        33.70 = 112.44 and left a paisa on the receivable. What settled it
        other than an allocation (a credit note, a drawdown, a write-off) is
        still counted at its slice of the base.
        """
        booked = booked_on(document.journal_entry, control_account)
        if booked is None:
            return round_money(self.amount * (document.exchange_rate or Decimal("1")))
        total = document.total()
        after = total - document.amount_due()
        others = [row for row in standing_allocations(document) if row.pk != self.pk]
        if any(row.document_base is None for row in others):
            # Made before the base was kept: the slice, as it was then.
            return slice_of(booked, total, after - self.amount, self.amount)
        applied = sum((row.amount for row in others), Decimal("0"))
        cleared = (sum((row.document_base for row in others), Decimal("0"))
                   + slice_of(booked, total, applied, after - self.amount - applied))
        return round_money(booked * min(after, total) / total) - cleared if total else Decimal("0")

    def payment_slice(self, control_account):
        """
        The base this allocation takes of its payment: what the payment
        booked for all of it applied so far, less what its other allocations
        took, as each recorded it (O209, as document_slice).
        """
        payment = self.payment
        booked = booked_on(payment.journal_entry, control_account) if payment.journal_entry_id else None
        if booked is None:
            return round_money(self.amount * (payment.exchange_rate or Decimal("1")))
        after = allocated_on(payment)
        others = allocations_of(payment, excluding=self)
        if not payment.amount or any(row.payment_base is None for row in others):
            return slice_of(booked, payment.amount, after - self.amount, self.amount)
        taken = sum((row.payment_base for row in others), Decimal("0"))
        return round_money(booked * min(after, payment.amount) / payment.amount) - taken

    @transaction.atomic
    def delete(self, *args, **kwargs):
        from django.utils import timezone

        if self.fx_entry_id and not self.fx_released_entry_id:
            self.fx_entry.create_reversal(
                entry_date=max(timezone.localdate(), self.fx_entry.date),
                memo=f"Releasing exchange difference on {self}")
        return super().delete(*args, **kwargs)

    def release_exchange_difference(self, on_date):
        """
        Its payment returned, the difference it realised never was: reversed
        once, on the day the bank returned it (Payment.void asks that day of
        the rule), or on the day it was realised when that came later.
        Left standing, the control account kept it after the void, at a rate
        the money never came at, and the gain or loss stayed in the profit
        and loss.
        """
        if self.fx_entry_id and not self.fx_released_entry_id:
            document, *_ = self.settles()
            self.fx_released_entry = self.fx_entry.create_reversal(
                entry_date=max(on_date, self.fx_entry.date),
                memo=f"Exchange difference on {document.number} released: {self.payment.number} returned")
            super(RealisedOnItsOwnDay, self).save(update_fields=["fx_released_entry", "updated_at"])


def last_of_held(amount, held_left):
    """
    The base the last of money held leaves at, or None when `amount` is not
    the last of it. `held_left` is (amount left, base left: held_base_left).

    Every taker asks it: a drawdown (post_drawdown) and the note that gives
    back what is left (a deposit credited back, a prepayment debited back),
    which books its line at it (InvoiceLine / BillLine.held_base). A
    drawdown alone asked, and 100 EUR held at 1.12345 (112.35), drawn 50
    and the other 50 credited back, booked 56.17 twice and left 0.01 held
    with nothing left to take (O51, review F2).
    """
    if held_left is not None and Decimal(amount) == held_left[0]:
        return held_left[1]
    return None


def held_base_left(held_account, entries, *, is_receivable):
    """
    The base still held on `held_account` by `entries`: what a deposit or
    a prepayment put there less what its drawdowns and its notes took off,
    as each was booked. A deposit sits as a credit, a prepayment as a
    debit; either way the figure is positive while something is held.
    """
    from django.db.models import Sum

    rows = JournalLine.objects.filter(
        entry__in=[entry for entry in entries if entry is not None], entry__posted=True,
        account=held_account,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    debit, credit = rows["debit"] or Decimal("0"), rows["credit"] or Decimal("0")
    return credit - debit if is_receivable else debit - credit


@transaction.atomic
def post_drawdown(
    *, party, held_account, control_account, amount, held_rate, document_rate,
    date, reference, memo, is_receivable, taxes=(), held_left=None, document_slice=None,
):
    """
    Draw money held up front — a customer's deposit, a prepayment to a
    vendor — down against the document it pays for. Returns the entry
    and the exchange entry, or None for the second when there is none.

    Shared by Sales and Purchasing: the two were written apart, the same
    holes were found in both, and they were fixed twice.

    The held account is cleared at the rate the money came in at, which
    is what it holds it at; at the document's rate a deposit taken at 80
    and drawn at 83 stayed on the deposit account for good. What that
    leaves on the control account is the document settled at a moved
    rate: a realised exchange difference.

    The difference is the base the document booked for this amount less
    the base cleared, each rounded on its own. Rounding the difference
    of the rates instead can leave a paisa on the control account that
    no document explains.

    `taxes`, [(account, amount)], is tax the money held carried (an
    advance for a service bears it when received): that much is taken
    off the tax accounts rather than the held one, since the document it
    is drawn against charges tax on the whole.

    `held_left`, (amount, base), is what is still held, in the document's
    currency and in base as booked (`held_base_left`). The drawdown that
    takes the last of the amount takes the last of the base: each rounded
    on its own, 100 EUR held at 1.12345 (112.35) and drawn 50 + 50 cleared
    56.17 twice and left a paisa held with nothing left to draw (O51).
    """
    held_base = round_money(Decimal(amount) * (held_rate or Decimal("1")))
    tax_base = [(account, round_money(Decimal(tax) * (held_rate or Decimal("1"))))
                for account, tax in taxes]
    taxed = sum((tax for _, tax in tax_base), Decimal("0"))
    last = last_of_held(amount, held_left)
    if last is not None:
        held_base = last + taxed
    # `document_slice`, (booked, total, settled before): the document's side is
    # its slice of what it booked (slice_of), as a payment applied to it takes.
    booked_base = (slice_of(document_slice[0], document_slice[1], document_slice[2], amount)
                   if document_slice and document_slice[0] is not None
                   else round_money(Decimal(amount) * (document_rate or Decimal("1"))))

    entry = JournalEntry.objects.create(date=date, reference=reference, memo=memo)
    # A receivable: Dr the deposit held / Cr what the customer owes.
    # A payable: Dr what is owed the vendor / Cr the prepayment held.
    debit, credit = (
        (held_account, control_account) if is_receivable else (control_account, held_account)
    )
    if tax_base and not is_receivable:
        raise ValueError("Tax on money held is drawn down only from a customer's advance.")
    JournalLine.objects.create(
        entry=entry, account=debit, party=party,
        debit=held_base - taxed, description=memo,
    )
    for account, tax in tax_base:
        JournalLine.objects.create(entry=entry, account=account, party=party, debit=tax,
                                   description=f"Tax on the advance: {memo}"[:255])
    JournalLine.objects.create(
        entry=entry, account=credit, party=party, credit=held_base, description=memo,
    )
    entry.post()

    fx_entry = _post_exchange_difference(
        party=party, control_account=control_account, difference=booked_base - held_base,
        date=date, reference=reference,
        memo=f"Exchange difference on {memo[0].lower()}{memo[1:]}",
        is_receivable=is_receivable,
        doing=f"{memo}, at the rate it was taken at rather than the document's,",
    )
    return entry, fx_entry


def installment_schedule(*, terms, document_date, total, settled):
    """
    [{due_date, amount, settled, outstanding}] for a document, with money
    received applied to the earliest installment first.

    Oldest-first because that is what both sides of a trade assume when
    nobody says otherwise: a customer paying half of a 50/50 order has
    paid the deposit, not the balance. Anything else would need the payer
    to nominate which installment they meant, which they almost never do.

    Shared by Sales and Purchasing — a bill falls due in installments the
    same way an invoice does, and two implementations would drift.
    """
    from apps.core.models import to_date

    total = Decimal(total)
    if terms is None or not document_date:
        return [{
            "due_date": to_date(document_date),
            "amount": total,
            "settled": min(settled, total),
            "outstanding": max(total - settled, Decimal("0")),
        }]

    remaining = Decimal(settled)
    rows = []
    for due_date, amount in terms.schedule(to_date(document_date), total):
        applied = min(remaining, amount)
        remaining -= applied
        rows.append({
            "due_date": due_date,
            "amount": amount,
            "settled": applied,
            "outstanding": amount - applied,
        })
    return rows


def amount_overdue(schedule, as_of):
    """How much of a schedule is past its due date and still unsettled."""
    return sum(
        (row["outstanding"] for row in schedule if row["due_date"] < as_of),
        Decimal("0"),
    )


def oldest_overdue(schedule, as_of):
    """The earliest unsettled installment that is past due, or None."""
    for row in schedule:
        if row["outstanding"] > 0 and row["due_date"] < as_of:
            return row
    return None


def owed_beyond(candidates, *, notes, drawdowns, reductions=()):
    """
    Of `candidates`, the posted documents whose amount_due() is above
    nothing, decided in the database. Sales and purchasing both ask it.

    amount_due() is the posted total less what was settled otherwise
    (standing payments, drawdowns of a deposit or prepayment, and the
    document's own `reductions`: a discount, a write-off) less what its
    notes corrected, the correction taken only down to nothing. So it is
    above nothing exactly when the total is more than all of them
    together.

    `candidates` comes annotated with `standing_paid` (each side's
    not_paid_in_full); `notes` is (queryset, field pointing at the
    document), and `drawdowns` a list of them: a deposit or prepayment
    drawn down, tax withheld. Documents with no posted total recorded are
    left out: their side asks amount_due() of each itself.
    """
    from django.db import models
    from django.db.models import DecimalField, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    money = DecimalField(max_digits=18, decimal_places=2)
    zero = Value(Decimal("0"))

    def total_of(rows, pointer, field):
        summed = rows.filter(**{pointer: OuterRef("pk")}).values(pointer).annotate(
            total=models.Sum(field)).values("total")
        return Coalesce(Subquery(summed), zero, output_field=money)

    # A note's fields named after its pointer are what it spent undoing a
    # settlement that moved no money: that much of it corrected nothing owed.
    note_rows, note_pointer, *undoing = notes
    note_value = models.F("posted_total")
    for name in undoing:
        note_value = note_value - models.F(name)
    settled = (models.F("standing_paid") + models.F("corrected_total")
               + models.F("drawn_total"))
    for name in reductions:  # "-name" puts one back: a discount withdrawn
        amount = Coalesce(models.F(name.lstrip("-")), zero, output_field=money)
        settled = settled - amount if name.startswith("-") else settled + amount
    drawn = Value(Decimal("0"), output_field=money)
    for rows, pointer in drawdowns:
        drawn = drawn + total_of(rows, pointer, "amount")
    return candidates.annotate(
        corrected_total=total_of(note_rows, note_pointer,
                                 models.ExpressionWrapper(note_value, output_field=money)),
        drawn_total=models.ExpressionWrapper(drawn, output_field=money),
    ).filter(posted_total__isnull=False, posted_total__gt=settled)


def standing_allocations(document):
    """
    The payments applied to `document` (an invoice, a bill, either's note)
    that still stand. A voided payment is money that never arrived, or
    never left: a bounced cheque, a recalled transfer. Its allocation stays
    on record as history, but nothing counts it.

    The one rule both sides ask (O53): a debit note's refund counted every
    allocation, so a vendor's bounced 300 refund read the note settled
    while the payable held 300; the sales mirror had always skipped voided
    payments. Commission on collection counted the bounced cheque too
    (O74). Read through `document.payment_allocations`, so a caller's
    prefetch is used.
    """
    return [allocation for allocation in document.payment_allocations.all()
            if not allocation.payment.is_voided()]


def standing_paid(document):
    """What of the payments applied to `document` still stands."""
    return sum((allocation.amount for allocation in standing_allocations(document)), Decimal("0"))


# What records a payment being applied to a document: sales registers
# InvoicePayment, purchasing BillPayment (each in its apps.py), so neither
# is imported here. A party can be a customer and a vendor at once, and
# its receipt could be applied in full to an invoice and again in full to
# a debit note while each side counted only its own applications.
ALLOCATION_MODELS = []


def register_allocation_model(model):
    if model not in ALLOCATION_MODELS:
        ALLOCATION_MODELS.append(model)


def allocations_of(payment, excluding=None):
    """Every allocation of `payment`, on either side, less `excluding`."""
    rows = []
    for model in ALLOCATION_MODELS:
        found = model.objects.filter(payment=payment)
        if isinstance(excluding, model) and excluding.pk:
            found = found.exclude(pk=excluding.pk)
        rows.extend(found)
    return rows


def allocated_on(payment, excluding=None):
    """What of `payment` is applied to anything, on either side, less `excluding`."""
    from django.db.models import Sum

    total = Decimal("0")
    for model in ALLOCATION_MODELS:
        rows = model.objects.filter(payment=payment)
        if isinstance(excluding, model) and excluding.pk:
            rows = rows.exclude(pk=excluding.pk)
        total += rows.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return total


def unapplied(payments):
    """
    The `payments` with money applied to nothing yet, each annotated with
    `unallocated`. Asked in the database, through the allocation models
    each trading side registered, so a list over a year does not build
    every payment to find the few still open.
    """
    from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    money = DecimalField(max_digits=18, decimal_places=2)
    applied = Value(Decimal("0"), output_field=money)
    for model in ALLOCATION_MODELS:
        total = (model.objects.filter(payment=OuterRef("pk")).order_by().values("payment")
                 .annotate(total=Sum("amount")).values("total"))
        applied = applied + Coalesce(Subquery(total, output_field=money), Value(Decimal("0"), output_field=money))
    return payments.annotate(
        unallocated=ExpressionWrapper(F("amount") - applied, output_field=money)
    ).filter(unallocated__gt=0)


def standing_on_account(payments):
    """
    Of `payments`, the money no document has taken and still stands, as
    (received, paid out): a customer's receipt not yet matched to its
    invoices, a payment to a vendor ahead of the bill. It sits in the
    control account all the same, so a balance that leaves it out tells the
    party they owe what they have paid.
    """
    received = paid_out = Decimal("0")
    for payment in unapplied(payments.filter(posted=True, voided_entry__isnull=True)):
        if payment.direction == PaymentDirection.RECEIPT:
            received += payment.unallocated
        else:
            paid_out += payment.unallocated
    return received, paid_out


def settlement_discount_to_take(document, on_date, force=False):
    """
    The early-settlement discount an invoice or a bill may take now: once,
    whatever `force` says (forcing waives the deadline, not the once),
    something, and no more than is still owed.

    Each side kept its own copy and each lacked a rule the other had: a
    forced second call took a discount twice, and an invoice already paid
    in full was discounted into credit the customer was never owed.
    """
    if document.settlement_discount_entry_id is not None or document.settlement_discount_amount:
        raise ValidationError(f"{document.number}'s settlement discount is taken already.")
    if not force and not document.discount_is_available(on_date):
        raise ValidationError(f"No settlement discount is available on {document.number} at that date.")
    amount = document.settlement_discount()
    if amount <= 0:
        raise ValidationError("These payment terms offer no settlement discount.")
    due = document.amount_due()
    if amount > due:
        raise ValidationError(
            f"Only {due} is outstanding on {document.number}; a discount of {amount} would take it "
            "below zero. Settle it without the discount."
        )
    return amount


def take_settlement_discount(document, *, control_account, party, discount_account, account_label,
                             is_receivable, on_date, payment=None, force=False):
    """
    Book the early-settlement discount on an invoice or a bill as a financial
    discount, with no change to GST (O109): Dr discount allowed / Cr receivable
    on an invoice, Dr payable / Cr discount received on a bill, at the
    document's rate. The one function both sides call; each kept its own
    posting and neither was ever called.

    Taken on a payment (`payment`): standing, applied to the document, made
    within the discount days unless `force`, and the discount is dated that
    day when no other is given. The caller saves the two fields it sets. Terms that reduce the GST value instead
    (section 15(3)(b): agreed before the supply, the accountant's choice) are
    refused here: that discount is a credit note, not this entry.
    """
    terms = document.payment_terms
    if terms is not None and terms.discount_reduces_gst:
        raise ValidationError(
            f"{terms} gives its discount as a reduction of the value, GST included (section 15(3)(b)): "
            f"issue a credit note for it rather than a settlement discount.")
    if payment is not None:
        if not payment.posted or payment.is_voided():
            raise ValidationError(f"{payment.number} does not stand: a discount is earned by money that arrived.")
        if not any(row.payment_id == payment.pk for row in standing_allocations(document)):
            raise ValidationError(f"{payment.number} is not applied to {document.number}: apply it first.")
        deadline = document.discount_due_date()
        if not force and (deadline is None or to_date(payment.payment_date) > deadline):
            raise ValidationError(f"{payment.number} was made on {to_date(payment.payment_date)}, after the "
                                  f"discount days on {document.number} ended ({deadline}).")
        on_date = on_date or max(to_date(payment.payment_date), document.date_for_discount())
    amount = settlement_discount_to_take(document, on_date, force)
    if discount_account is None:
        raise ValidationError(f"The company has no {account_label} account configured.")
    base_amount = round_money(amount * (document.exchange_rate or Decimal("1")))
    memo = f"Settlement discount on {document.number}"
    entry = JournalEntry.objects.create(date=on_date, reference=document.number, memo=memo)
    JournalLine.objects.create(entry=entry, account=discount_account if is_receivable else control_account,
                               party=party, debit=base_amount, description=memo)
    JournalLine.objects.create(entry=entry, account=control_account if is_receivable else discount_account,
                               party=party, credit=base_amount, description=memo)
    entry.post()
    document.settlement_discount_amount = amount
    document.settlement_discount_entry = entry
    return entry


def undone_by_note(note_total, absorbed, write_off, discount, discount_taken, document_total, whole):
    """
    What a credit or debit note undoes of the settlements on its document
    that moved no money: (write-off, discount).

    A note first clears what is still owed (absorbed). Past that it undoes
    a write-off standing, which is the unpaid part given up; then the
    settlement discount, in proportion to what it gives back of the
    document, since the discount was a price for paying early, and all of
    what stands once the notes give back the whole (`whole`). Only the
    rest is owed back as cash. Counted as paid, a written-off invoice
    credited in full owed the customer the 1,000 they never paid, and a
    discount came back as cash on top of what was paid, on either side.
    """
    rest = max(note_total - absorbed, Decimal("0"))
    undo_write_off = min(rest, max(write_off, Decimal("0")))
    rest -= undo_write_off
    undo_discount = Decimal("0")
    if discount > 0 and rest > 0:
        share = discount if whole else round_money(rest * discount_taken / document_total)
        undo_discount = min(share, discount, rest)
    return undo_write_off, undo_discount


def spent_undoing(note):
    """
    What a credit or debit note spent undoing a settlement that moved no money,
    as it recorded it (undone_by_note): a write-off, a discount. That part of
    it cleared nothing owed, so it is not also credit against its document,
    and not absorbed by it either. One rule for both sides (O80, review F3):
    sales subtracted it and purchasing did not, and a debit note that undid
    0.10 of a discount read -0.10 refund due once its bill's payment bounced.
    """
    return (getattr(note, "reversed_write_off", None) or Decimal("0")) + (note.reversed_discount or Decimal("0"))


def withdraw_discount(document, control_account, party, amount, on_date, memo):
    """
    Put `amount` of `document`'s settlement discount back on it: the
    discount's own entry turned round, at the document's rate, on the day
    the payment that earned it came back. Returns the entry.
    """
    taken = document.settlement_discount_entry
    credited = any(line.credit for line in taken.lines.all() if line.account_id == control_account.pk)
    base = round_money(amount * (document.exchange_rate or Decimal("1")))
    to_control, to_other = (base, Decimal("0")) if credited else (Decimal("0"), base)
    entry = JournalEntry.objects.create(date=on_date, reference=document.number, memo=memo)
    JournalLine.objects.create(entry=entry, account=control_account, party=party, debit=to_control,
                               credit=to_other, description=memo[:255])
    JournalLine.objects.create(entry=entry, account=booked_beside(taken, control_account), party=party,
                               debit=to_other, credit=to_control, description=memo[:255])
    entry.post()
    return entry


def booked_beside(entry, control_account):
    """Where an entry put its other side: the account a write-off or a discount went to, as it was booked."""
    return next(line.account for line in entry.lines.select_related("account") if line.account_id != control_account.pk)


def refuse_other_control_account(payment, account, document):
    """
    A payment settles a document only through the account the document
    was booked to. Applied across accounts, the document reads settled
    while its control account keeps the balance and the payment's account
    the opposite one, and nothing ever matches them again.
    """
    if payment.counterpart_account_id != account.pk:
        raise ValidationError(
            f"{payment} was booked against {payment.counterpart_account}, and {document} "
            f"against {account}: applied to it, both accounts would be left wrong. Record "
            f"the money against {account}.")
