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

from apps.accounting.models import AccountType, JournalEntry, JournalLine, round_money
from apps.core.models import Company


def stock_account_refusal(account):
    """
    Why `account` cannot hold stock's value, or "" when it can (the review of O324). An inventory account is an
    asset account kept for stock: not a bank or cash account, and none of the company's other accounts (the
    receivables control, a prepayment, a deposit). A Stores Manager who pointed an item at the payables posted an
    adjustment through it and debited them 50.00 with no vendor. One rule, asked wherever an inventory account is
    set (an item, an item template, the company default) and again where one is used.
    """
    if account is None:
        return ""
    if account.account_type != AccountType.ASSET or account.holds_money:
        kind = "money" if account.holds_money else account.get_account_type_display().lower()
        return (f"{account.code} is a {kind} account. Stock's value is held in an asset account kept for "
                "stock, never a control or money account.")
    company = Company.objects.order_by("pk").first()
    if company is not None:
        for field in type(company)._meta.get_fields():
            if (getattr(field, "related_model", None) is type(account) and field.concrete
                    and field.name != "default_inventory_account"
                    and getattr(company, f"{field.name}_id") == account.pk):
                return (f"{account.code} is the company's {field.verbose_name}. Stock's value is held in an "
                        "asset account kept for stock, never a control or money account.")
    return ""


def check_stock_account(account, field="inventory_account"):
    refusal = stock_account_refusal(account)
    if refusal:
        raise ValidationError({field: refusal})


def refuse_a_default_stock_account_that_is_not_one(company):
    """A company check: the default inventory account is an asset account kept for stock (review of O324)."""
    check_stock_account(company.default_inventory_account, "default_inventory_account")


def inventory_account_for(item):
    account = item.inventory_account or Company.get().default_inventory_account
    if account is None:
        raise ValidationError(
            f"{item} has no inventory account and the company has no default; "
            "stock cannot be valued."
        )
    # Asked again where it is used: one set before the rule, or retyped since, books nothing.
    check_stock_account(account)
    return account


def refuse_moving_the_default_stock_account(company):
    """
    A company check: the default inventory account is the account of every
    item that names none of its own, so moving it moves theirs. Refused
    while any of them has stock movements, as moving an item's own account
    is; naming that account on those items first leaves them where they are.
    """
    from .models import Item

    stored = type(company).objects.order_by("pk").values_list(
        "default_inventory_account_id", flat=True).first()
    if stored is None or stored == company.default_inventory_account_id:
        return
    held = list(Item.objects.filter(inventory_account__isnull=True, movements__isnull=False)
                .values_list("sku", flat=True).distinct().order_by("sku")[:3])
    if held:
        raise ValidationError({"default_inventory_account": [
            f"{', '.join(held)} hold their stock's value in the default inventory account "
            "and have stock movements booked to it; it cannot move from under them. Name "
            "the account on those items first."]})


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
                         quantities=None, shelf=None):
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

    `shelf` is {item: money}, what the item's movements moved, positive:
    under the standard it is what the inventory account takes, so the
    account moves by exactly what the shelf did (O269). Without it the
    standard is worked out from the quantity, once for the item.

    Returns the posted JournalEntry, or None when there is nothing to post
    (non-stocked items, or a zero-valued movement).
    """
    quantities = quantities or {}
    shelf = shelf or {}
    totals = defaultdict(Decimal)
    variances = defaultdict(Decimal)
    # One figure an item: two lines of one standard item each booked the
    # standard on both lines' quantity.
    by_item = {}
    for item, amount in entries:
        by_item[item] = by_item.get(item, Decimal("0")) + amount
    for item, amount in by_item.items():
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
            booked = (shelf[item] if item in shelf
                      else round_money(Decimal(quantity) * (item.standard_cost or Decimal("0"))))
            difference = round_money(amount) - booked
            if difference:
                variances[_variance_account()] += -difference if reverse else difference

        if reverse:
            debit_account, credit_account = credit_account, debit_account
        totals[(debit_account, credit_account)] += booked

    # A standard subcontract assembly whose components were worth more than its standard moves the account
    # down while the accrual still takes the vendor's charge (O322): its row is the other way round.
    rows = {}
    for (debit_account, credit_account), amount in totals.items():
        if amount < 0:
            debit_account, credit_account, amount = credit_account, debit_account, -amount
        if round_money(amount):
            key = (debit_account, credit_account)
            rows[key] = rows.get(key, Decimal("0")) + round_money(amount)
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
