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

Runs once, in the release that brings O293: figures kept after O293 are
already in the base and would be converted twice.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

CENTS = Decimal("0.01")


def rate_on(apps, currency_id, day):
    currency = apps.get_model("core", "Currency").objects.get(pk=currency_id)
    if currency.is_base:
        return None
    found = (apps.get_model("core", "ExchangeRate").objects
             .filter(currency_id=currency_id, valid_from__lte=day).order_by("-valid_from").first())
    return found.rate if found else None


def convert(apps, forward):
    orders = apps.get_model("sales", "SalesOrder")
    for order in orders.objects.filter(approved_at__isnull=False, approved_figures__isnull=False,
                                       currency__isnull=False).order_by("pk"):
        rate = rate_on(apps, order.currency_id, order.order_date)
        if rate is None:
            continue
        figures = dict(order.approved_figures)
        if figures.get("total") is not None:
            total = Decimal(figures["total"])
            figures["total"] = money(total * rate if forward else total / rate)
        if figures.get("margin") is not None:
            margin = Decimal(figures["margin"])
            figures["margin"] = money(Decimal(100) - (Decimal(100) - margin) / rate if forward
                                      else Decimal(100) - (Decimal(100) - margin) * rate)
        orders.objects.filter(pk=order.pk).update(approved_figures=figures)


def money(amount):
    return str(amount.quantize(CENTS, rounding=ROUND_HALF_UP))


def in_base(apps, schema_editor):
    convert(apps, forward=True)


def back_in_its_currency(apps, schema_editor):
    """Undone, the figures go back to the order's currency, within a paisa."""
    convert(apps, forward=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_apikeyuse_prefix_apikeyuse_refused_and_more"),
        ("sales", "0077_one_opportunity_an_order"),
    ]

    operations = [
        migrations.RunPython(in_base, back_in_its_currency),
    ]
