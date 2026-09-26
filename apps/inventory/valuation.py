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


def post_inventory_entry(entries, date, reference, memo, direction, reverse=False,
                         quantities=None):
    """
    Post the ledger side of a stock movement.

    `entries` is [(item, amount)] with positive amounts. `direction` is
    "in" for receiving (Inventory/GRNI) or "out" for shipping
    (Cost of sales/Inventory). `reverse` swaps the sides for returns.

    `quantities` is {item: quantity} and is needed only when an item is
    costed at standard. Under that method the shelf takes the standard
    and nothing else — that is the whole point of it — so the inventory
    account takes quantity times standard, the accrual takes what the
    vendor charged, and the difference is a purchase price variance for
    somebody to explain rather than a number quietly absorbed into an
    asset. Without the quantity there is no way to work out the standard
    amount, so an item costed at standard with no quantity supplied is
    refused rather than posted at actual.

    Returns the posted JournalEntry, or None when there is nothing to post
    (non-stocked items, or a zero-valued movement).
    """
    quantities = quantities or {}
    totals = defaultdict(Decimal)
    variances = defaultdict(Decimal)
    for item, amount in entries:
        if not item.track_inventory or not amount:
            continue
        if direction == "in":
            debit_account, credit_account = inventory_account_for(item), grni_account()
        else:
            debit_account, credit_account = cogs_account_for(item), inventory_account_for(item)

        booked = amount
        if direction == "in" and item.costing_method == "standard":
            quantity = quantities.get(item)
            if quantity is None:
                raise ValidationError(
                    f"{item} is costed at standard, so what the shelf takes depends "
                    "on how much arrived, and no quantity was supplied."
                )
            booked = round_money(Decimal(quantity) * (item.standard_cost or Decimal("0")))
            difference = round_money(amount) - booked
            if difference:
                variances[_variance_account()] += -difference if reverse else difference

        if reverse:
            debit_account, credit_account = credit_account, debit_account
        totals[(debit_account, credit_account)] += booked

    rows = {pair: round_money(amount) for pair, amount in totals.items() if round_money(amount)}
    variance_rows = {
        account: round_money(amount) for account, amount in variances.items()
        if round_money(amount)
    }
    if not rows and not variance_rows:
        return None

    entry = JournalEntry.objects.create(date=date, reference=reference, memo=memo)
    for (debit_account, credit_account), amount in rows.items():
        JournalLine.objects.create(
            entry=entry, account=debit_account, debit=amount, description=memo[:255]
        )
        JournalLine.objects.create(
            entry=entry, account=credit_account, credit=amount, description=memo[:255]
        )
    for account, amount in variance_rows.items():
        # The accrual is what the vendor charged; the shelf took the
        # standard. The variance is the rest of the same entry, so it is
        # posted against the accrual rather than balanced on its own.
        if amount > 0:
            JournalLine.objects.create(
                entry=entry, account=account, debit=amount, description="Price variance"
            )
            JournalLine.objects.create(
                entry=entry, account=grni_account(), credit=amount, description="Price variance"
            )
        else:
            JournalLine.objects.create(
                entry=entry, account=account, credit=-amount, description="Price variance"
            )
            JournalLine.objects.create(
                entry=entry, account=grni_account(), debit=-amount, description="Price variance"
            )
    entry.post()
    return entry


def _variance_account():
    account = Company.get().purchase_price_variance_account
    if account is None:
        raise ValidationError(
            "An item costed at standard was received at a different price and the "
            "company has no purchase price variance account configured."
        )
    return account
