"""
What a weaver made, for payroll to pay by the piece.

Counted from the rolls that name them as the weaver and still stand: a
roll whose booking was voided was weighed in error and pays nobody. By
the shift-day, not the calendar day, so a night shift's rolls fall in
the period the shift began in.

**Metres the plant doubts are paid at the figure it trusts.** A roll
whose declared metres the station flagged against what its weight
supports is paid on the lesser of the two. The counter is the weaver's
own figure; paying on it, when the scale says otherwise, is paying for
the reading rather than the cloth, and a flag nobody's pay depends on
is a flag nobody fixes.
"""

from collections import defaultdict
from decimal import Decimal


def _rolls(employee, up_to):
    from .rolls import FabricRoll

    return FabricRoll.objects.filter(
        woven_by=employee, shift_date__lte=up_to, entry__voided_at__isnull=True,
    ).order_by("shift_date", "id")


def _by_day(rows):
    days = defaultdict(lambda: Decimal("0"))
    for day, quantity in rows:
        days[day] += quantity
    return sorted(days.items())


def metres_woven(employee, up_to):
    rows = []
    for roll in _rolls(employee, up_to):
        metres = roll.length_m
        if roll.is_metres_exception and roll.metres_from_weight is not None:
            metres = min(metres, roll.metres_from_weight)
        rows.append((roll.shift_date, metres))
    return _by_day(rows)


def kilograms_woven(employee, up_to):
    return _by_day((roll.shift_date, roll.net_weight_kg) for roll in _rolls(employee, up_to))
