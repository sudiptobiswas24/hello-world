"""
Whether a shelf can give up what is being asked of it.

One invariant, in one place: stock does not go negative unless the
warehouse says it may. It is written here because manufacturing needed
it and there were already four copies of it — in `sales` at delivery,
twice in `adjustments`, twice in `transfers` — and a fifth copy is how
this project's fifth defect shape starts. Those four are not folded in
yet because each asks a different shelf: a delivery must also refuse a
consignment warehouse, a transfer line measures a bin when the
warehouse is binned, and two of them count a lot rather than an item.
Folding them in means giving this function all three of those, and
that is a change to three modules rather than a note in one.

What is shared is the rule and the message shape. What is not shared,
for now, is who looks up the number.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError


def check_available(item, warehouse, wanted, lot=None, action="take"):
    """
    Refuse to move `wanted` off a shelf that has not got it.

    `wanted` is positive and in the item's stocking unit — callers
    convert before asking, because a case of twelve passing a check that
    only twelve eaches should have passed is the bug this signature
    exists to prevent.
    """
    if wanted <= 0 or not item.track_inventory:
        return
    if warehouse.allow_negative_stock:
        return
    on_hand = (
        lot.on_hand_at(warehouse) if lot is not None
        else item.on_hand_at(warehouse)
    )
    if wanted > on_hand:
        subject = f"batch {lot.code}" if lot is not None else str(item)
        raise ValidationError(
            f"Only {on_hand} {item.uom} of {subject} on hand at {warehouse}; "
            f"cannot {action} {wanted}."
        )
    return Decimal(on_hand)
