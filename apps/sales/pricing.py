"""
Price resolution.

Until now every unit price was typed by hand on every line, which is both
the most-used field in the system and the easiest one to get wrong. A price
comes from the first of these that answers:

    1. the customer's own price list
    2. the default price list for the document's currency
    3. the item's list price
    4. nothing — the caller must supply one

Within a list, the matching entry is the one with the highest `min_quantity`
that the ordered quantity reaches, which is how volume breaks work.
"""

from decimal import Decimal

from django.utils import timezone


def _entry_for(price_list, item, quantity):
    return (
        price_list.entries.filter(item=item, min_quantity__lte=quantity)
        .order_by("-min_quantity")
        .first()
    )


def applicable_price_lists(customer=None, currency=None, on_date=None):
    """The price lists that could apply, most specific first."""
    from .models import PriceList

    on_date = on_date or timezone.now().date()
    candidates = []

    if customer is not None:
        profile = getattr(customer, "customer_profile", None)
        if profile is not None and profile.price_list_id:
            candidates.append(profile.price_list)

    defaults = PriceList.objects.filter(is_active=True, is_default=True)
    if currency is not None:
        defaults = defaults.filter(currency=currency)
    candidates.extend(defaults)

    return [
        price_list for price_list in candidates
        if price_list.is_active and price_list.covers(on_date)
    ]


def resolve_price(item, customer=None, quantity=Decimal("1"), currency=None, on_date=None):
    """The unit price for this item, or None if nothing sets one."""
    for price_list in applicable_price_lists(customer, currency, on_date):
        entry = _entry_for(price_list, item, quantity)
        if entry is not None:
            return entry.unit_price
    return item.sale_price
