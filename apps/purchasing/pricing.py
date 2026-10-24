"""
What a vendor has agreed to charge.

Purchase order lines used to require a price typed in by hand, on the
reasoning that "the price is whatever the vendor quoted". That is true
of a one-off, and wrong of everything else: vendors quote agreed prices
with quantity breaks and validity dates, and without them the price leg
of the three-way match has nothing to check against but whatever someone
typed on the order.

Mirrors sales/pricing.py deliberately. The selection rules are the same
question read from the other side — which agreed price applies to this
party, this item, this quantity, on this date.
"""

from decimal import Decimal


def applicable_prices(item, vendor, quantity=None, currency=None, on_date=None, uom=None):
    """
    Agreed prices that cover this item for this vendor, best first.

    "Best" is the most specific: a price with a quantity break that the
    order actually reaches beats a general one, and a larger break beats
    a smaller. A buyer ordering 1,000 should get the 1,000 price without
    having to remember it exists.

    A break is reached by the quantity in the price's own unit (O98): 2
    tonnes reach a break of 1,000 kg. `quantity` is in `uom`, blank for the
    item's stocking unit.
    """
    from apps.accounting.pricing import quantity_in_price_unit

    from .models import VendorPrice

    prices = VendorPrice.objects.filter(item=item, vendor=vendor, is_active=True)
    if currency is not None:
        prices = prices.filter(currency=currency)
    candidates = [
        price for price in prices.select_related("currency", "uom")
        if price.covers(on_date) and price.covers_quantity(
            quantity_in_price_unit(quantity, item=item, uom=uom, price_uom=price.uom, on_date=on_date))
    ]
    # The biggest break the order reaches wins, its size read in one unit (review F4): a kg
    # price from 1,000 kg and a tonne price from 2 t, 3 t ordered, took the kg price because
    # 1,000 is more than 2.
    return sorted(candidates, key=lambda price: (
        -quantity_in_price_unit(price.min_quantity, item=item, uom=price.uom, on_date=on_date), price.pk))


def resolve_purchase_price(item, vendor, quantity=None, currency=None, on_date=None, uom=None):
    """
    The unit price to use, per `uom` (blank: the item's stocking unit), or
    None if nothing has been agreed: 100 a kg agreed prices a tonne at
    100,000 (accounting.pricing, the rule sales prices by too).
    """
    from apps.accounting.pricing import per_line_unit

    prices = applicable_prices(item, vendor, quantity, currency, on_date, uom=uom)
    if not prices:
        return None
    # Asked with no currency (an estimate, a reorder row), the price is restated in base (review F5):
    # 10 EUR at 1.1 is 11.00, not 10.
    return per_line_unit(prices[0].unit_price, item=item, price_uom=prices[0].uom, uom=uom,
                         price_currency=prices[0].currency, currency=currency, on_date=on_date)


def resolve_lead_time(item, vendor, quantity=None, currency=None, on_date=None):
    """Days this vendor has agreed to take for this item, else what they usually take, or None."""
    from .models import VendorProfile

    prices = applicable_prices(item, vendor, quantity, currency, on_date)
    for price in prices:
        if price.lead_time_days:
            return price.lead_time_days
    return VendorProfile.objects.filter(party=vendor).values_list("lead_time_days", flat=True).first()


def preferred_vendor(item, on_date=None):
    """
    Who to buy this from by default.

    A flagged preferred vendor wins; otherwise the cheapest agreed price.
    Cheapest-by-default is a deliberate tiebreak, not a recommendation —
    it is visible and arguable, where picking arbitrarily is neither.
    """
    from .models import VendorPrice

    prices = [
        price for price in VendorPrice.objects.filter(item=item, is_active=True)
        .select_related("vendor", "currency")
        if price.covers(on_date)
    ]
    if not prices:
        return None
    preferred = [price for price in prices if price.is_preferred]
    if preferred:
        return preferred[0].vendor
    # Cheapest in one currency and one unit (O100, review F4): the base price per the item's own
    # unit, by the rule every price is restated by. 90,000 a tonne is 90 a kg, below 100 a kg.
    from apps.accounting.pricing import per_line_unit

    return min(prices, key=lambda price: (per_line_unit(
        price.unit_price, item=item, price_uom=price.uom, price_currency=price.currency, on_date=on_date),
        price.pk)).vendor
