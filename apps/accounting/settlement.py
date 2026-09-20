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

from .models import JournalEntry, JournalLine, round_money


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
    if not difference:
        return None

    company = Company.get()
    is_gain = (difference < 0) if is_receivable else (difference > 0)
    account = company.fx_gain_account if is_gain else company.fx_loss_account
    if account is None:
        side = "gain" if is_gain else "loss"
        raise ValidationError(
            f"Settling this at a different rate than it was booked at leaves "
            f"{abs(difference)} on {control_account.code}, which is a realised exchange "
            f"{side}, and the company has no FX {side} account configured."
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
