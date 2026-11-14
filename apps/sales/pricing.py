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

    on_date = on_date or timezone.localdate()
    candidates = []

    if customer is not None:
        profile = getattr(customer, "customer_profile", None)
        if profile is not None and profile.price_list_id:
            # A list priced in another currency must not price this document:
            # 5 EUR is not 5 USD. A list with no currency is in the base, converted.
            chosen = profile.price_list
            if currency is None or chosen.currency_id in (None, currency.pk):
                candidates.append(chosen)

    defaults = PriceList.objects.filter(is_active=True, is_default=True)
    if currency is not None:
        # In the document's currency first; then one in none, which is in the base and is
        # converted (a decision: the integrator's, review item 5).
        candidates.extend(defaults.filter(currency=currency))
        defaults = defaults.filter(currency__isnull=True)
    candidates.extend(defaults)

    return [
        price_list for price_list in candidates
        if price_list.is_active and price_list.covers(on_date)
    ]


def resolve_price(item, customer=None, quantity=Decimal("1"), currency=None, on_date=None, uom=None):
    """
    The unit price for this item, per `uom` (blank: its stocking unit) in
    `currency`, or None if nothing sets one.

    A list's entries and the item's list price are per its stocking unit,
    and a break is reached by the quantity in that unit: 2 boxes of 12 reach
    a break at 24 (O79). The item's list price is in the base currency and
    is converted (O78); a list chosen for the document's currency is in it.
    """
    from apps.accounting.pricing import per_line_unit, quantity_in_price_unit

    in_stock = quantity_in_price_unit(quantity, item=item, uom=uom, on_date=on_date)
    for price_list in applicable_price_lists(customer, currency, on_date):
        entry = _entry_for(price_list, item, in_stock)
        if entry is not None:
            # A list in no currency is in the base, and is converted as any base price is
            # (a decision, review item 5): 5 on it was 5 EUR on a EUR line and 5 USD on a USD one.
            return per_line_unit(entry.unit_price, item=item, uom=uom, price_currency=price_list.currency,
                                 currency=currency if price_list.currency_id is None else price_list.currency,
                                 on_date=on_date)
    return per_line_unit(item.sale_price, item=item, uom=uom, currency=currency, on_date=on_date)
