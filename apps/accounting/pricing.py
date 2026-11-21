"""
A price restated in the terms of the line it prices.

Sales and purchasing each resolved a price from a list, an agreement or
the item, and each took the number at face value: an order in euros
priced 10 from the item's 10 in base (O78), a line in boxes of 12 priced
at 10 a box from 10 an each, and its volume breaks compared in boxes
(O79), and 100 a kg agreed with a vendor priced an order in tonnes at
100 a tonne (O98). One rule for both, so a fix to either is a fix to both
(CLAUDE.md, mistake 5). It lives here because neither trading module may
import the other.

A price is stated per a unit (blank: the item's stocking unit) in a
currency (blank: the base). Restated per the line's unit in the line's
currency on the document's day, worked whole and rounded once to the
places a unit price holds.
"""

from decimal import ROUND_HALF_UP, Decimal

PRICE_PLACES = Decimal("0.01")


def per_line_unit(price, *, item, price_uom=None, uom=None, price_currency=None, currency=None, on_date=None):
    """`price`, stated per `price_uom` in `price_currency`, per one `uom` of `item` in `currency`."""
    if price is None:
        return None
    value = Decimal(price) * item.unit_factor(uom, on_date) / item.unit_factor(price_uom, on_date)
    if price_currency is not None and currency is not None and price_currency.pk != currency.pk:
        value = price_currency.convert_to(value, currency, on_date)
    elif price_currency is None and currency is not None and not currency.is_base:
        value = currency.from_base(value, on_date)
    elif price_currency is not None and not price_currency.is_base and currency is None:
        value = price_currency.to_base(value, on_date)
    return value.quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)


def quantity_in_price_unit(quantity, *, item, uom=None, price_uom=None, on_date=None):
    """`quantity` of `uom`, restated in the unit a price and its breaks are stated in."""
    if quantity is None:
        return None
    return Decimal(quantity) * item.unit_factor(uom, on_date) / item.unit_factor(price_uom, on_date)
