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


def applicable_prices(item, vendor, quantity=None, currency=None, on_date=None):
    """
    Agreed prices that cover this item for this vendor, best first.

    "Best" is the most specific: a price with a quantity break that the
    order actually reaches beats a general one, and a larger break beats
    a smaller. A buyer ordering 1,000 should get the 1,000 price without
    having to remember it exists.
    """
    from .models import VendorPrice

    prices = VendorPrice.objects.filter(item=item, vendor=vendor, is_active=True)
    if currency is not None:
        prices = prices.filter(currency=currency)
    candidates = [
        price for price in prices.select_related("currency")
        if price.covers(on_date) and price.covers_quantity(quantity)
    ]
    return sorted(candidates, key=lambda price: (-price.min_quantity, price.pk))


def resolve_purchase_price(item, vendor, quantity=None, currency=None, on_date=None):
    """The unit price to use, or None if nothing has been agreed."""
    prices = applicable_prices(item, vendor, quantity, currency, on_date)
    return prices[0].unit_price if prices else None


def resolve_lead_time(item, vendor, quantity=None, currency=None, on_date=None):
    """Days this vendor has agreed to take, or None."""
    prices = applicable_prices(item, vendor, quantity, currency, on_date)
    for price in prices:
        if price.lead_time_days:
            return price.lead_time_days
    return None


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
    return min(prices, key=lambda price: price.unit_price).vendor
