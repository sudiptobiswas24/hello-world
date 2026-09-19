"""
Inventory valuation postings.

Stock is valued at weighted average cost, derived by replaying the movement
ledger (see Item.average_cost_at) rather than stored as a running field —
a stored average drifts the moment a movement is corrected.

These helpers turn stock movements into ledger entries so the accounts
reflect inventory. The flow is standard perpetual inventory:

    receive goods   Dr Inventory        Cr GRNI
    vendor bill     Dr GRNI             Cr Accounts Payable
    ship goods      Dr Cost of Sales    Cr Inventory

Without the GRNI accrual in the middle, a bill that expensed goods directly
would double-count cost: once on purchase and again on sale.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import JournalEntry, JournalLine, round_money
from apps.core.models import Company


def inventory_account_for(item):
    account = item.inventory_account or Company.get().default_inventory_account
    if account is None:
        raise ValidationError(
            f"{item} has no inventory account and the company has no default; "
            "stock cannot be valued."
        )
    return account


def cogs_account_for(item):
    account = item.cogs_account or Company.get().default_cogs_account
    if account is None:
        raise ValidationError(
            f"{item} has no cost-of-sales account and the company has no default."
        )
    return account


def grni_account():
    account = Company.get().grni_account
    if account is None:
        raise ValidationError(
            "The company has no goods-received-not-invoiced account configured; "
            "received stock cannot be accrued."
        )
    return account


def post_inventory_entry(entries, date, reference, memo, direction, reverse=False):
    """
    Post the ledger side of a stock movement.

    `entries` is [(item, amount)] with positive amounts. `direction` is
    "in" for receiving (Inventory/GRNI) or "out" for shipping
    (Cost of sales/Inventory). `reverse` swaps the sides for returns.

    Returns the posted JournalEntry, or None when there is nothing to post
    (non-stocked items, or a zero-valued movement).
    """
    totals = defaultdict(Decimal)
    for item, amount in entries:
        if not item.track_inventory or not amount:
            continue
        if direction == "in":
            debit_account, credit_account = inventory_account_for(item), grni_account()
        else:
            debit_account, credit_account = cogs_account_for(item), inventory_account_for(item)
        if reverse:
            debit_account, credit_account = credit_account, debit_account
        totals[(debit_account, credit_account)] += amount

    totals = {pair: round_money(amount) for pair, amount in totals.items() if round_money(amount)}
    if not totals:
        return None

    entry = JournalEntry.objects.create(date=date, reference=reference, memo=memo)
    for (debit_account, credit_account), amount in totals.items():
        JournalLine.objects.create(
            entry=entry, account=debit_account, debit=amount, description=memo[:255]
        )
        JournalLine.objects.create(
            entry=entry, account=credit_account, credit=amount, description=memo[:255]
        )
    entry.post()
    return entry
