"""
What parties owe and are owed, and how late, in the base currency (O416).

Both trading sides ask the same questions of their documents: what a party owes now (a customer's
outstanding balance, a vendor's), and the aging, what is open by how late it is. Added in each
document's own currency, an unpaid USD 50,000 bill counted 50,000 beside rupees where the payable held
41,62,500.00, the aging no longer footed to its control account, and a USD customer owing 50,000 read
50,000 against a rupee credit limit. Sales had it first and purchasing copied it (mistake 5), so the
conversion and the summing live here once, and each side registers what it has (`register_balance_side`):
accounting imports neither.

The rule. Every figure returned is in the base currency, and each document's due is taken at the base its
control account still holds for it (settlement.held_base: what it booked, line by line, less what its
settlements cleared; O423), never at today's rate: that is what the control account holds for it, since
each settlement realised its own difference on its own day. Money on account the same, at the rate its
payment posted at. A down payment or a prepayment the same: an advance stays at the rate it came in at
(restatement.py says why).

Each aging row keeps the document's own currency and its due in it beside the base figure, so a USD bill
reads "USD 25,000.00" and "20,81,250.00"; the bucket totals are base.

A closing-rate restatement moves the control account on its own day and reverses the next. On any other day
the ledger is at the booked rates and the aging foots to it as it stands. On a restatement's own day the
aging adds the restatement as one more line per control account ("Restated at the closing rate of <day>"),
so it still foots: the restatement posted, for each item, the restated base less the base carried, and the
carried base is the booked base this reads.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from django.utils import timezone

from apps.core.models import to_date

from .models import Payment, PaymentDirection
from .settlement import in_base, standing_on_account, unapplied

AGING_BUCKETS = ((1, 30), (31, 60), (61, 90))
OVERDUE = tuple(f"{lo}-{hi}" for lo, hi in AGING_BUCKETS) + ("90+",)
RESTATED = "restated"


@dataclass(frozen=True)
class BalanceSide:
    """What a trading side registers: its documents and how to read them."""

    name: str  # "receivable" or "payable": what its control account is
    model: type  # Invoice, Bill
    row_key: str  # "invoice", "bill": the key a row carries its document under
    rows_key: str  # "invoices", "bills": the key a bucket carries its rows under
    party_field: str  # "customer", "vendor"
    date_field: str  # "invoice_date", "bill_date"
    notes_field: str  # "credits", "debits": set on a note, naming what it corrects
    figures: tuple  # the prefetches amount_due() reads
    not_paid_in_full: Callable  # narrows documents in the database to those that may still owe
    control_accounts: Callable  # () -> the accounts its documents and money on account sit on
    settles: str  # the PaymentDirection that is the party settling it (a receipt, our disbursement)
    sign: int  # +1 where the control account's balance is a debit (receivable), -1 a credit (payable)
    control_field: str = ""  # "receivable_account", "payable_account": the document's own control account


SIDES = {}


def register_balance_side(side):
    SIDES[side.name] = side


def side(name):
    return SIDES[name]


def in_base_as_booked(holder, amount):
    """
    `amount` of `holder`'s currency (an invoice, bill, note or payment) in the base currency at the rate it
    posted at, as the control account holds it. One posted with no rate kept is weighed on its own day.
    """
    on_date = getattr(holder, "payment_date", None) or getattr(holder, "invoice_date", None) \
        or getattr(holder, "bill_date", None)
    return in_base(amount, holder.currency, on_date, rate=holder.exchange_rate)


def held_for(side, document, due):
    """
    `document`'s `due` in the base currency as its control account holds it (O423): a foreign document's base
    still held, what it booked line by line less what its settlements cleared (settlement.held_base), as the
    restatement carries it; never its due at its rate rounded once, which read a paisa off a multi-line
    invoice. A base-currency document, or one with nothing booked to read, its due as booked.
    """
    from .restatement import is_foreign
    from .settlement import held_base

    if side.control_field and is_foreign(document.currency):
        held = held_base(document, getattr(document, side.control_field))
        if held is not None:
            return held
    return in_base_as_booked(document, due)


def spread(base_total, parts):
    """`base_total` shared over `parts` (in the document's currency) in proportion: total first, divide last."""
    whole = sum(parts, Decimal("0"))
    shares, given = [], Decimal("0")
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            shares.append(base_total - given)
        else:
            share = (base_total * part / whole).quantize(Decimal("0.01")) if whole else Decimal("0")
            shares.append(share)
            given += share
    return shares


def _bucket(as_of, due_date):
    days = (as_of - due_date).days
    if days <= 0:
        return "current", 0
    if days > 90:
        return "90+", days
    return next(f"{lo}-{hi}" for lo, hi in AGING_BUCKETS if lo <= days <= hi), days


def _row(side, holder, key, due_date, days, in_currency, base):
    currency = getattr(holder, "currency", None)
    return {key: holder, "due_date": due_date, "days_overdue": days, "currency": currency.code if currency else "",
            "due_in_currency": in_currency, "amount_due": base}


def _open_documents(side, documents):
    return (side.not_paid_in_full(documents.filter(posted=True, **{f"{side.notes_field}__isnull": True}))
            .select_related(side.party_field, "currency", "payment_terms").prefetch_related(*side.figures))


def _open_notes(side, documents, as_of=None):
    notes = documents.filter(posted=True, **{f"{side.notes_field}__isnull": False})
    if as_of is not None:
        notes = notes.filter(**{f"{side.date_field}__lte": as_of})
    return notes.select_related(side.party_field, "currency").prefetch_related(
        *side.figures, *(f"{side.notes_field}__{name}" for name in side.figures))


def _on_account(side, payments):
    """(payment, signed base) for each of `payments` with money no document has taken."""
    for payment in unapplied(payments.filter(posted=True, voided_entry__isnull=True).select_related("currency")):
        if payment.unallocated:
            sign = -1 if payment.direction == side.settles else 1
            yield payment, sign


def party_balance(side, party):
    """
    What `party` owes on this side now, in the base currency, signed: owed on documents, less what notes owe
    back to them, less their money no document has taken (or plus ours to them). Each at its posted rate.
    """
    documents = side.model.objects.filter(**{side.party_field: party})
    owed = Decimal("0")
    for document in _open_documents(side, documents):
        due = document.amount_due()
        if due > 0:
            owed += held_for(side, document, due)
    for note in _open_notes(side, documents):
        refund = note.refund_due()
        if refund:
            owed -= in_base_as_booked(note, refund)
    received, paid_out = standing_on_account(
        Payment.objects.filter(party=party, counterpart_account__in=side.control_accounts()))
    # The party's own money settles what they owe; ours to them on this account adds to it.
    theirs, ours = (received, paid_out) if side.settles == PaymentDirection.RECEIPT else (paid_out, received)
    return owed - theirs + ours


def aging(side, as_of=None, documents=None):
    """
    What is open on this side by how late it is, as at `as_of`, in the base currency. `documents`, the
    caller's scope (a rep's own customers' invoices); money on account and the restatement line are kept to
    the same parties. Buckets: current, 1-30, 31-60, 61-90, 90+, credits (what the party is owed back or has
    paid ahead, negative) and, on a restatement's own day, restated.
    """
    as_of = to_date(as_of) or timezone.localdate()
    scoped = documents is not None
    documents = side.model.objects.all() if documents is None else documents
    buckets = {"current": [], **{key: [] for key in OVERDUE}, "credits": []}

    for document in _open_documents(side, documents):
        due = document.amount_due()
        if due <= 0:
            continue
        # A row per outstanding installment, not per document: on a 50/50 term the deposit can be badly
        # overdue while the balance is not due for another month. The base is the document's, shared over
        # its installments, so the rows add to what the control account holds for it.
        schedule = [row for row in document.installments() if row["outstanding"] > 0]
        shares = spread(held_for(side, document, due), [row["outstanding"] for row in schedule])
        for row, base in zip(schedule, shares):
            key, days = _bucket(as_of, row["due_date"])
            buckets[key].append(_row(side, document, side.row_key, row["due_date"], days, row["outstanding"], base))

    # O412: what parties are owed back and have paid ahead, as party_balance counts it, so the report foots
    # to the ledger. Left out, an open credit note of 27,187.20 put the aging that much above the receivables.
    credits = buckets["credits"]
    for note in _open_notes(side, documents, as_of):
        refund = note.refund_due()
        if refund > 0:
            credits.append(_row(side, note, side.row_key, getattr(note, side.date_field), 0, -refund,
                                -in_base_as_booked(note, refund)))
    payments = Payment.objects.filter(counterpart_account__in=side.control_accounts(), payment_date__lte=as_of)
    if scoped:
        payments = payments.filter(party__in=documents.values(side.party_field))
    for payment, sign in _on_account(side, payments):
        credits.append(_row(side, payment, "payment", payment.payment_date, 0, sign * payment.unallocated,
                            sign * in_base_as_booked(payment, payment.unallocated)))

    restated = restatement_rows(side, as_of, documents.values(side.party_field) if scoped else None)
    if restated:
        buckets[RESTATED] = restated

    return {
        key: {
            "count": len(entries),
            "total": sum((entry["amount_due"] for entry in entries), Decimal("0")),
            side.rows_key: entries,
        }
        for key, entries in buckets.items()
    }


def restatement_rows(side, as_of, parties=None):
    """
    On a closing-rate restatement's own day, what it moved each of this side's control accounts by, as one
    row per account; nothing on any other day, when it has reversed (or not yet been made). A restatement
    voided is voided on its own day, so one standing then is the one in the ledger at the close of `as_of`.
    """
    from .restatement import Restatement

    restatement = Restatement.objects.filter(as_of=as_of, voided_on__isnull=True).first()
    if restatement is None or not restatement.journal_entry_id:
        return []
    lines = restatement.lines.filter(account__in=side.control_accounts()).select_related("account")
    if parties is not None:
        lines = lines.filter(party__in=parties)
    by_account = {}
    for line in lines.order_by("account__code", "pk"):
        by_account[line.account] = by_account.get(line.account, Decimal("0")) + side.sign * line.difference
    return [{"restatement": restatement, "account": account, "due_date": as_of, "days_overdue": 0, "currency": "",
             "due_in_currency": None, "amount_due": moved,
             "label": f"Restated at the closing rate of {as_of:%d-%m-%Y} ({account.code} {account.name})"}
            for account, moved in by_account.items() if moved]


def restated_row(entry):
    """A restatement row of an aging as the API gives it; the side's own rows carry the same money keys."""
    restatement = entry["restatement"]
    return {"id": restatement.pk, "number": restatement.number, "description": entry["label"],
            "account": entry["account"].code, **money_of(entry), "kind": "restatement"}


def money_of(entry):
    """What every aging row says of its money: when due, how late, in base, and in its own currency."""
    return {"due_date": entry["due_date"], "days_overdue": entry["days_overdue"], "amount_due": entry["amount_due"],
            "currency": entry["currency"], "due_in_currency": entry["due_in_currency"]}
