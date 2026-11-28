"""
A note with GST on a document the old system issued.

At go-live an invoice or bill the old system issued comes in only as
what was still owed on it (import_csv open_invoices, open_bills): one
line, no tax, and no return here counts it, because the old system
reported the supply. A correction to that supply afterwards (a rate
difference, a return, short weight) is a credit or debit note under GST
all the same: it carries its own lines and tax, moves this month's tax,
and is reported against the old document's number and date. The
document cannot be credited line for line, since its lines are not
here, so the note's lines are given.

Shared by sales and purchasing, so a rule fixed in one is fixed in both.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

ZERO = Decimal("0")


def checked_lines(lines, account_field, verb):
    """[(fields, taxes)] for a note's own lines, or a sentence naming the line."""
    if not lines:
        raise ValidationError(f"Nothing to {verb}.")
    checked = []
    for number, line in enumerate(lines, start=1):
        quantity, price = line.get("quantity"), line.get("unit_price")
        if quantity is None or quantity <= 0:
            raise ValidationError(f"Line {number}: the quantity is more than nothing.")
        if price is None or price < 0:
            raise ValidationError(f"Line {number}: the price is nothing or more.")
        if not line.get("description") and not line.get("item"):
            raise ValidationError(f"Line {number}: say what it is, an item or in words.")
        if not line.get(account_field):
            raise ValidationError(f"Line {number}: which account it books to.")
        fields = {"item": line.get("item"), "description": line.get("description") or "",
                  "quantity": quantity, "unit_price": price,
                  account_field: line[account_field]}
        checked.append((fields, list(line.get("taxes") or [])))
    return checked


def check_room(value, already, adding, document_word, verb):
    """A note takes no more off the old document than it was for in all."""
    if value is None:
        return
    room = value - already
    if adding > room:
        raise ValidationError(
            f"The old {document_word} was for {value}; {already} has been {verb} against it "
            f"already, so no more than {room} can be."
        )


def from_request(data, account_field, item_model, default_account=None):
    """
    (lines, memo, on_date, old_value) from a request body, each problem a
    400 in words rather than a 500. `item_model` is passed in: this module
    sits in accounting, which knows nothing of items.
    """
    from rest_framework.exceptions import ValidationError as DRFValidationError

    from apps.core.api import money_amount
    from apps.core.models import to_date

    from .models import Account, Tax

    def number(row, name, index):
        try:
            value = Decimal(str(row.get(name, "")).strip())
        except (InvalidOperation, ValueError):
            raise DRFValidationError([f"Line {index}: {name.replace('_', ' ')} is a number."])
        if not value.is_finite():
            raise DRFValidationError([f"Line {index}: {name.replace('_', ' ')} is a number."])
        return value

    rows = data.get("lines")
    if not isinstance(rows, list):
        raise DRFValidationError({"lines": ["A list of the note's lines."]})
    lines = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise DRFValidationError({"lines": [f"Line {index} is not a line."]})
        for name in ("item", account_field):
            if row.get(name) and not str(row[name]).isdigit():
                raise DRFValidationError([f"Line {index}: no such {name.replace('_', ' ')}."])
        if not all(str(tax).isdigit() for tax in row.get("taxes") or []):
            raise DRFValidationError([f"Line {index}: no such tax."])
        item = None
        if row.get("item"):
            item = item_model.objects.filter(pk=row["item"]).first()
            if item is None:
                raise DRFValidationError([f"Line {index}: no such item."])
        account = default_account
        if row.get(account_field):
            account = Account.objects.filter(pk=row[account_field]).first()
            if account is None:
                raise DRFValidationError([f"Line {index}: no such account."])
        wanted = list(row.get("taxes") or [])
        taxes = list(Tax.objects.filter(pk__in=wanted))
        if len(taxes) != len(set(wanted)):
            raise DRFValidationError([f"Line {index}: no such tax."])
        lines.append({"item": item, "description": str(row.get("description") or "").strip(),
                      "quantity": number(row, "quantity", index),
                      "unit_price": number(row, "unit_price", index),
                      "taxes": taxes, account_field: account})
    try:
        on_date = to_date(data.get("on_date")) if data.get("on_date") else None
    except (ValueError, TypeError):
        raise DRFValidationError({"on_date": ["A date, YYYY-MM-DD."]})
    return lines, str(data.get("memo") or ""), on_date, money_amount(data, "old_value")
