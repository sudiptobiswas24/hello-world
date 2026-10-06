"""
The stock statement a plant on a cash-credit limit sends its bank each
month, for drawing power: stock by class at cost, work in progress from
the ledger, creditors, debtors within and beyond ninety days, and the
power the bank's margins leave.

Every figure is read from where it lives at the date asked (the shelf,
the two agings, the ledger) and nothing is stored: a statement is a
reading, and the next month's is read again. It sits here, in the
office's own layer, because it is the one report that needs sales,
purchasing, inventory and accounting at once.
"""

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import JournalLine
from apps.core.models import to_date
from apps.inventory.models import StockClass
from apps.inventory.reports import stock_valuation
from apps.purchasing.models import ap_aging
from apps.sales.models import ar_aging

ZERO = Decimal("0")
PAISA = Decimal("0.01")
HUNDRED = Decimal("100")
# The aging's buckets a bank counts: ninety days is where every bank's
# schedule cuts, and the buckets are built on it.
WITHIN_NINETY = ("current", "1-30", "31-60", "61-90")


def work_in_progress(as_of):
    """The balance on the work-in-progress account at the date, or nothing where no account is set."""
    from apps.manufacturing.orders import ManufacturingSettings

    settings = ManufacturingSettings.objects.first()
    if settings is None or settings.wip_account_id is None:
        return ZERO
    totals = JournalLine.objects.filter(account_id=settings.wip_account_id, entry__posted=True,
                                        entry__date__lte=as_of).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (totals["debit"] or ZERO) - (totals["credit"] or ZERO)


def _after(amount, margin_percent):
    return (amount * (HUNDRED - margin_percent) / HUNDRED).quantize(PAISA)


def stock_statement(as_of=None, stock_margin=Decimal("25"), debtor_margin=Decimal("40")):
    as_of = to_date(as_of) if as_of else timezone.localdate()
    by_class = {code: ZERO for code, _ in StockClass.choices}
    by_class[""] = ZERO
    for row in stock_valuation(as_of=as_of)["rows"]:
        by_class[row["item"].stock_class] += row["value"]
    stock = [{"stock_class": code, "label": label, "value": by_class[code].quantize(PAISA)}
             for code, label in [*StockClass.choices, ("", "Unclassified")]]
    stock_total = sum((line["value"] for line in stock), ZERO)
    wip = work_in_progress(as_of).quantize(PAISA)

    debtors = ar_aging(as_of=as_of)
    within = sum((debtors[key]["total"] for key in WITHIN_NINETY), ZERO).quantize(PAISA)
    beyond = debtors["90+"]["total"].quantize(PAISA)
    creditors = sum((bucket["total"] for bucket in ap_aging(as_of=as_of).values()), ZERO).quantize(PAISA)

    # What the plant has paid for: stock and work in progress less what it still owes on them.
    paid_stock = max(stock_total + wip - creditors, ZERO)
    stock_after = _after(paid_stock, stock_margin)
    debtors_after = _after(within, debtor_margin)
    return {
        "as_of": as_of,
        "stock": stock,
        "stock_total": stock_total,
        "work_in_progress": wip,
        "creditors": creditors,
        "paid_stock": paid_stock,
        "stock_margin_percent": stock_margin,
        "paid_stock_after_margin": stock_after,
        "debtors_within_90": within,
        "debtors_beyond_90": beyond,
        "debtors_total": within + beyond,
        "debtor_margin_percent": debtor_margin,
        "debtors_after_margin": debtors_after,
        "drawing_power": stock_after + debtors_after,
    }
