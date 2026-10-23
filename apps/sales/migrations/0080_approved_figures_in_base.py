"""
O337: before O293 an approval kept its figures in the order's own currency;
since, the policy weighs an order in the base currency and compares the kept
figures with base ones. A confirmed euro order approved at 50,000 EUR (at 90,
45,00,000 base) held 50,000 as covered, so a cut and then a restore up to what
was approved read as past the approval.

Put in the base here, at the rate in_base() uses: the currency's rate on the
order's own date (the most recent effective on or before it), rounded to the
paisa:
- the total: kept total x rate;
- the margin: kept as (revenue - cost) / revenue with the revenue in the
  order's currency and the cost in the base. With revenue r and cost c,
  kept m = 100 (r - c) / r, so c = r (1 - m / 100), and the base margin
  100 (r x rate - c) / (r x rate) = 100 - (100 - m) / rate: r cancels. From
  the kept figure, rounded to 2 places as policy_figures() keeps it.
Discounts are percentages and stay. An order in the base currency, or with
no rate on its date, is left as it is.

Figures kept from now on name the currency they are in (approved_figures
"currency", the base's code: ChangesWithinItsApproval.figures_as_kept). This
converts only figures that name none, and names it on each it converts, so
running it again converts nothing twice. An order in the base currency is named
too; one with no rate on its date is left unnamed and unconverted. Figures kept
by O293's code before this release name no currency and are already in the
base: no released database holds any, O293 and this ship together (g23).

Undone, converted figures go back by the same rate and lose the name. That is
lossy: the total comes back within a paisa of what it was, but the margin was
rounded to 2 places and is multiplied back up by the rate (at 95, -300.00 ->
95.79 -> -299.95), so it can be off by several hundredths of a point.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

CENTS = Decimal("0.01")
KEPT_IN = "currency"   # apps.core.approvals.KEPT_IN, as it stood at this migration


def rate_on(apps, currency_id, day):
    currency = apps.get_model("core", "Currency").objects.get(pk=currency_id)
    if currency.is_base:
        return None
    found = (apps.get_model("core", "ExchangeRate").objects
             .filter(currency_id=currency_id, valid_from__lte=day).order_by("-valid_from").first())
    return found.rate if found else None


def convert(apps, forward):
    orders = apps.get_model("sales", "SalesOrder")
    currencies = apps.get_model("core", "Currency").objects
    base = currencies.filter(is_base=True).values_list("code", flat=True).first() or "base"
    for order in orders.objects.filter(approved_at__isnull=False, approved_figures__isnull=False).order_by("pk"):
        if not isinstance(order.approved_figures, dict):
            continue
        figures = dict(order.approved_figures)
        if (KEPT_IN in figures) == forward:
            continue   # forward: already named, so in the base; back: not named here
        if order.currency_id is None or currencies.get(pk=order.currency_id).is_base:
            if forward:
                figures[KEPT_IN] = base
            else:
                figures.pop(KEPT_IN)
            orders.objects.filter(pk=order.pk).update(approved_figures=figures)
            continue
        rate = rate_on(apps, order.currency_id, order.order_date)
        if rate is None:
            continue
        if figures.get("total") is not None:
            total = Decimal(figures["total"])
            figures["total"] = money(total * rate if forward else total / rate)
        if figures.get("margin") is not None:
            margin = Decimal(figures["margin"])
            figures["margin"] = money(Decimal(100) - (Decimal(100) - margin) / rate if forward
                                      else Decimal(100) - (Decimal(100) - margin) * rate)
        if forward:
            figures[KEPT_IN] = base
        else:
            figures.pop(KEPT_IN, None)
        orders.objects.filter(pk=order.pk).update(approved_figures=figures)


def money(amount):
    return str(amount.quantize(CENTS, rounding=ROUND_HALF_UP))


def in_base(apps, schema_editor):
    convert(apps, forward=True)


def back_in_its_currency(apps, schema_editor):
    """Undone, the figures converted here go back to the order's currency: lossy, see the module's note."""
    convert(apps, forward=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_apikeyuse_prefix_apikeyuse_refused_and_more"),
        ("sales", "0079_b2cl_limit_filled"),
    ]

    operations = [
        migrations.RunPython(in_base, back_in_its_currency),
    ]
