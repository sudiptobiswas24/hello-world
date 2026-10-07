"""
A bank statement's lines read in from the file the bank's site exports,
instead of keyed one at a time.

The banks' columns differ (SBI: "Txn Date, Description, Ref No./Cheque
No., Debit, Credit"; HDFC: "Date, Narration, Chq./Ref.No., Withdrawal
Amt., Deposit Amt."), so each is found by what its heading says rather
than by position. Checked whole before anything is kept: a row outside
the statement's dates, with no amount, or with both a debit and a credit
stops the file with its row number. A row already on the statement is
passed over, so the same file twice adds nothing; two identical rows in
one file are two transactions, as the bank says.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.core import csvrows
from apps.core.csvrows import RowError

from .models import BankStatementLine, round_money

# What a heading says, in the order looked for. "amount" alone is signed as
# the bank signs it; a debit/credit (withdrawal/deposit) pair is unsigned.
ROLES = {
    "date": ("date",),
    "description": ("narration", "description", "particular", "remark", "detail"),
    "reference": ("ref", "cheque", "chq", "utr"),
    "money_in": ("deposit", "credit", "cr"),
    "money_out": ("withdrawal", "debit", "dr"),
    "amount": ("amount",),
}


def _says(key, word):
    """Whether a heading says `word`: as a word of its own ("Ref No."), or inside a longer one ("Particulars")."""
    tokens = re.split(r"[^a-z0-9]+", key)
    return word in tokens or (len(word) > 3 and word in key)


def columns_of(keys):
    """Which heading plays which role; the date is the one that must be there."""
    found = {}
    for role, words in ROLES.items():
        for key in keys:
            # "balance" columns never play a part, and an "amount" heading
            # that says deposit or withdrawal is that side, not the signed one.
            if "balance" in key or (role == "amount" and any(_says(key, w) for w in ROLES["money_in"] + ROLES["money_out"])):
                continue
            if any(_says(key, word) for word in words):
                found[role] = key
                break
    if "date" not in found:
        raise ValidationError({"text": [f"No column names the date; the headings are {', '.join(keys) or 'missing'}."]})
    if "amount" not in found and "money_in" not in found and "money_out" not in found:
        raise ValidationError({"text": ["No column holds the amount: an amount, or a debit and a credit."]})
    return found


def _amount(row, columns):
    """Signed as the bank sees it: money in positive, money out negative."""
    came_in = csvrows.decimal(row, columns["money_in"], places=2) if "money_in" in columns else None
    went_out = csvrows.decimal(row, columns["money_out"], places=2) if "money_out" in columns else None
    # Some exports write 0.00 in the empty side; a zero is an empty side.
    if came_in and went_out:
        raise RowError(columns["money_in"], f"has both {columns['money_in']} and {columns['money_out']}; "
                       "a line is one or the other.")
    if came_in or went_out:
        return came_in if came_in else -went_out
    if "amount" in columns:
        amount = csvrows.decimal(row, columns["amount"], places=2, required_=True)
        if not amount:
            raise RowError(columns["amount"], "is zero; the bank moved nothing.")
        return amount
    side = columns.get("money_in") or columns.get("money_out")
    if came_in is not None or went_out is not None:
        raise RowError(side, "is zero; the bank moved nothing.")
    raise RowError(side, "is required: a line moves money.")


def read_rows(statement, text):
    """[{date, description, reference, amount}] and [(row, column, message)] of the file's rows."""
    rows = csvrows.read(text)
    if not rows:
        raise ValidationError({"text": ["The file has no rows under its headings."]})
    columns = columns_of(list(rows[0].keys()))
    read, errors = [], []
    for number, row in enumerate(rows, start=2):  # row 1 is the headings
        if not any(row.values()):
            continue
        try:
            day = csvrows.date(row, columns["date"])
            if not statement.start_date <= day <= statement.end_date:
                raise RowError(columns["date"], f"{day:%d-%m-%Y} is outside the statement "
                               f"({statement.start_date:%d-%m-%Y} to {statement.end_date:%d-%m-%Y}).")
            read.append({
                "date": day,
                "description": row.get(columns.get("description", ""), "")[:255],
                "reference": row.get(columns.get("reference", ""), "")[:64],
                "amount": _amount(row, columns),
            })
        except RowError as error:
            errors.append((number, error.column, error.message))
    return read, errors


def import_lines(statement, text, *, commit=False):
    """
    What the file would add to the statement, added when `commit` and
    every row is clean: {rows, added, already_there, errors, lines_total,
    difference (closing less opening less every line: zero when the
    statement is whole), committed}.
    """
    if statement.closed:
        raise ValidationError("This statement is closed. Reopen it to import into it.")
    read, errors = read_rows(statement, text)
    report = {"rows": len(read) + len(errors), "added": 0, "already_there": 0, "errors": errors,
              "lines_total": None, "difference": None, "committed": False}
    if errors:
        return report
    # Each line already on the statement explains at most one row of the file.
    standing = {}
    for line in statement.lines.all():
        standing.setdefault((line.date, line.reference, line.description, line.amount), []).append(line.pk)
    new = []
    for row in read:
        key = (row["date"], row["reference"], row["description"], row["amount"])
        if standing.get(key):
            standing[key].pop()
            report["already_there"] += 1
        else:
            new.append(row)
    with transaction.atomic():
        for row in new:
            BankStatementLine.objects.create(statement=statement, **row)
        total = statement.lines.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        report["added"] = len(new)
        report["lines_total"] = round_money(total)
        report["difference"] = round_money(statement.closing_balance - statement.opening_balance - total)
        if not commit:
            transaction.set_rollback(True)
    report["committed"] = commit
    return report
