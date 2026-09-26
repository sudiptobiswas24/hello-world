"""
Financial statements.

Every module here posts to the ledger and, until now, nothing read it
back. A double-entry system that cannot produce a trial balance can
record a year of trading and answer nothing about it.

All three statements derive from JournalLine rather than from any stored
summary, for the reason on-hand quantity and weighted average cost are
derived: a stored total drifts the moment an entry is corrected, and
corrections are how this codebase fixes everything.

Sign convention: a balance is debits less credits, so assets and
expenses are positive and liabilities, equity and income are negative.
The statements present each in the sign a reader expects and keep the
raw balance alongside, because the arithmetic that has to add up is the
raw one.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from apps.core.models import Company, to_date

from .models import Account, AccountType, JournalLine, round_money

# Which side of the balance sheet, and which way round it reads.
DEBIT_TYPES = (AccountType.ASSET, AccountType.EXPENSE)
CREDIT_TYPES = (AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME)
PROFIT_TYPES = (AccountType.INCOME, AccountType.EXPENSE)


def _movements(start=None, end=None, accounts=None):
    """Posted debit and credit totals per account over a window."""
    lines = JournalLine.objects.filter(entry__posted=True)
    if start:
        lines = lines.filter(entry__date__gte=to_date(start))
    if end:
        lines = lines.filter(entry__date__lte=to_date(end))
    if accounts is not None:
        lines = lines.filter(account__in=accounts)
    rows = lines.values("account").annotate(debit=Sum("debit"), credit=Sum("credit"))
    return {
        row["account"]: (row["debit"] or Decimal("0"), row["credit"] or Decimal("0"))
        for row in rows
    }


def natural_balance(account_type, balance):
    """The figure a reader expects, given which side the account sits."""
    return balance if account_type in DEBIT_TYPES else -balance


def trial_balance(as_of=None, start=None, include_zero=False):
    """
    Every account's opening balance, movement and closing balance.

    The one report that must be self-proving: if total debits do not
    equal total credits the ledger is broken, not the report, and
    `balanced` says so rather than leaving it to be noticed.
    """
    as_of = to_date(as_of)
    start = to_date(start)

    opening = _movements(end=start) if start else {}
    movement = _movements(start=start, end=as_of)
    closing = _movements(end=as_of)

    rows = []
    for account in Account.objects.all().order_by("code"):
        open_debit, open_credit = opening.get(account.pk, (Decimal("0"), Decimal("0")))
        move_debit, move_credit = movement.get(account.pk, (Decimal("0"), Decimal("0")))
        close_debit, close_credit = closing.get(account.pk, (Decimal("0"), Decimal("0")))
        balance = close_debit - close_credit
        if not include_zero and not (balance or move_debit or move_credit):
            continue
        rows.append({
            "account": account,
            "account_type": account.account_type,
            "opening": open_debit - open_credit,
            "debit": move_debit,
            "credit": move_credit,
            "balance": balance,
            "natural": natural_balance(account.account_type, balance),
        })

    total_debit = sum((row["debit"] for row in rows), Decimal("0"))
    total_credit = sum((row["credit"] for row in rows), Decimal("0"))
    closing_debit = sum((row["balance"] for row in rows if row["balance"] > 0), Decimal("0"))
    closing_credit = sum((-row["balance"] for row in rows if row["balance"] < 0), Decimal("0"))

    return {
        "as_of": as_of,
        "start": start,
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "closing_debit": closing_debit,
        "closing_credit": closing_credit,
        "balanced": closing_debit == closing_credit,
    }


def _grouped(types, start, end):
    """Accounts of these types with a balance, rolled up under their parents."""
    accounts = Account.objects.filter(account_type__in=types).order_by("code")
    balances = _movements(start=start, end=end, accounts=accounts)

    rows, total = [], Decimal("0")
    for account in accounts:
        debit, credit = balances.get(account.pk, (Decimal("0"), Decimal("0")))
        balance = debit - credit
        if not balance:
            continue
        natural = natural_balance(account.account_type, balance)
        rows.append({
            "account": account,
            "parent": account.parent,
            "balance": balance,
            "natural": natural,
        })
        total += natural
    return rows, total


def profit_and_loss(start=None, end=None):
    """
    Income less expenses for a period.

    Dated by period, not cumulative: income and expense accounts measure
    a span of time, which is the whole reason they do not appear on the
    balance sheet.
    """
    start, end = to_date(start), to_date(end)
    income, income_total = _grouped([AccountType.INCOME], start, end)
    expense, expense_total = _grouped([AccountType.EXPENSE], start, end)

    return {
        "start": start,
        "end": end,
        "income": income,
        "income_total": income_total,
        "expenses": expense,
        "expense_total": expense_total,
        "net_profit": income_total - expense_total,
    }


def retained_earnings(as_of=None, since=None):
    """
    Accumulated profit, derived rather than posted.

    Most systems close the year by journaling income and expense into an
    equity account. Deriving it instead means there is no closing entry
    to forget, to run twice, or to disagree with the accounts it came
    from — and reopening a prior year cannot silently invalidate it.
    """
    result = profit_and_loss(start=since, end=as_of)
    return result["net_profit"]


def balance_sheet(as_of=None, year_start=None):
    """
    What the company owns, owes and is worth at a date.

    Retained earnings is the accumulated profit of every period up to the
    date, split at `year_start` into brought-forward and this year's, so
    the statement shows the same two lines an accountant expects.

    `balanced` is the check that matters: assets must equal liabilities
    plus equity, and if they do not, something posted that should not
    have.
    """
    as_of = to_date(as_of)
    year_start = to_date(year_start)
    if year_start is None:
        company = Company.get()
        if as_of and company.fiscal_year_start_month:
            import datetime

            year = as_of.year if as_of.month >= company.fiscal_year_start_month else as_of.year - 1
            year_start = datetime.date(year, company.fiscal_year_start_month, 1)

    assets, asset_total = _grouped([AccountType.ASSET], None, as_of)
    liabilities, liability_total = _grouped([AccountType.LIABILITY], None, as_of)
    equity, equity_total = _grouped([AccountType.EQUITY], None, as_of)

    this_year = retained_earnings(as_of=as_of, since=year_start)
    brought_forward = retained_earnings(as_of=as_of) - this_year

    equity_with_earnings = equity_total + brought_forward + this_year

    return {
        "as_of": as_of,
        "year_start": year_start,
        "assets": assets,
        "asset_total": asset_total,
        "liabilities": liabilities,
        "liability_total": liability_total,
        "equity": equity,
        "equity_total": equity_total,
        "retained_brought_forward": brought_forward,
        "profit_for_year": this_year,
        "equity_and_earnings": equity_with_earnings,
        "total_liabilities_and_equity": liability_total + equity_with_earnings,
        "balanced": asset_total == liability_total + equity_with_earnings,
    }
