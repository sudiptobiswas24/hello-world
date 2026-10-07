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

from apps.core.models import Company

from .models import JournalEntry, JournalLine, PaymentDirection, round_money


def settlement_difference(amount, document_rate, payment_rate):
    """
    The base-currency difference left on the control account.

    Positive means the document was booked at a higher rate than it
    settled at; which way that reads as a gain or a loss depends on
    whether the control account is a receivable or a payable.
    """
    document_rate = document_rate or Decimal("1")
    payment_rate = payment_rate or Decimal("1")
    if document_rate == payment_rate:
        return Decimal("0")
    return round_money(Decimal(amount) * (document_rate - payment_rate))


@transaction.atomic
def post_settlement_fx(
    *, party, control_account, amount, document_rate, payment_rate,
    date, reference, memo, is_receivable,
):
    """
    Clear the base-currency residue a settlement leaves behind, and book
    the other side to realised FX gain or loss. Returns the entry, or
    None when the rates agree and there is nothing to post.

    A receivable and a payable are mirrors. On a receivable, booking at a
    higher rate than you collect at leaves an unwanted debit — you
    expected more base currency than arrived, which is a loss. On a
    payable the same rate move leaves an unwanted credit — you owed more
    base currency than you had to pay, which is a gain.
    """
    difference = settlement_difference(amount, document_rate, payment_rate)
    return _post_exchange_difference(
        party=party, control_account=control_account, difference=difference,
        date=date, reference=reference, memo=memo, is_receivable=is_receivable,
    )


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


@transaction.atomic
def post_drawdown(
    *, party, held_account, control_account, amount, held_rate, document_rate,
    date, reference, memo, is_receivable, taxes=(),
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
    """
    held_base = round_money(Decimal(amount) * (held_rate or Decimal("1")))
    tax_base = [(account, round_money(Decimal(tax) * (held_rate or Decimal("1"))))
                for account, tax in taxes]
    booked_base = round_money(Decimal(amount) * (document_rate or Decimal("1")))

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
        debit=held_base - sum((tax for _, tax in tax_base), Decimal("0")), description=memo,
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

    note_rows, note_pointer = notes
    settled = (models.F("standing_paid") + models.F("corrected_total")
               + models.F("drawn_total"))
    for name in reductions:
        settled = settled + Coalesce(models.F(name), zero, output_field=money)
    drawn = Value(Decimal("0"), output_field=money)
    for rows, pointer in drawdowns:
        drawn = drawn + total_of(rows, pointer, "amount")
    return candidates.annotate(
        corrected_total=total_of(note_rows, note_pointer, "posted_total"),
        drawn_total=models.ExpressionWrapper(drawn, output_field=money),
    ).filter(posted_total__isnull=False, posted_total__gt=settled)


# What records a payment being applied to a document: sales registers
# InvoicePayment, purchasing BillPayment (each in its apps.py), so neither
# is imported here. A party can be a customer and a vendor at once, and
# its receipt could be applied in full to an invoice and again in full to
# a debit note while each side counted only its own applications.
ALLOCATION_MODELS = []


def register_allocation_model(model):
    if model not in ALLOCATION_MODELS:
        ALLOCATION_MODELS.append(model)


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
