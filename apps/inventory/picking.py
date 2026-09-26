"""
Deciding what actually comes off the shelf.

`allocate()` chose lots first-expired-first-out, `suggest_pick()` routed
a picker through bins in walking order, and `suggest_putaway()` said
where goods should land. All three were built, tested, and called by
nothing: a delivery still refused to post unless somebody typed in the
lot and the bin by hand.

That is the worst shape in this codebase's own list — fully modelled,
admin-editable, and read by no code path. It looks handled. FEFO in
particular was advisory: the system knew which batch should go first
and was never asked.

This module is the part that was missing — one function that turns "ship
forty of these" into the specific lots and bins that will satisfy it,
so the documents can ask instead of demanding an answer.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .bins import bins_holding, suggest_pick, suggest_putaway, unbinned
from .tracking import allocate


def plan_issue(item, warehouse, quantity, lot=None, storage_bin=None,
               on_date=None, allow_expired=False):
    """
    Work out which lots and bins a withdrawal will actually take.

    Returns [(lot, bin, quantity)] in the item's stocking unit, summing
    to `quantity`. One entry when there is nothing to choose between;
    several when the withdrawal spans batches, shelves, or both.

    Anything named explicitly is honoured rather than second-guessed. A
    picker who says which batch they took is reporting a fact, and a
    system that overrules them with its own idea of what should have
    happened is recording fiction.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError("Nothing to issue.")

    tracked = item.tracking != "none"
    if not tracked and lot is not None:
        raise ValidationError(
            f"{item} is not tracked by lot or serial number, so a batch cannot be "
            "named for it."
        )

    if tracked and lot is None:
        chosen = allocate(
            item, warehouse, quantity, on_date=on_date, allow_expired=allow_expired
        )
    elif tracked:
        chosen = [(lot, quantity)]
    else:
        chosen = [(None, quantity)]

    plan = []
    for chosen_lot, chosen_quantity in chosen:
        plan.extend(
            _split_across_bins(item, warehouse, chosen_lot, chosen_quantity, storage_bin)
        )
    return plan


def _split_across_bins(item, warehouse, lot, quantity, storage_bin):
    """
    Turn one lot's share into the bins it comes off.

    A warehouse that does not track bins gets one entry with no bin,
    which is the truthful answer rather than an invented shelf.
    """
    if storage_bin is not None:
        return [(lot, storage_bin, quantity)]
    if not warehouse.requires_bins:
        # Bins may still exist here without being compulsory. Routing a
        # picker through them anyway would refuse a withdrawal that the
        # warehouse is perfectly happy to satisfy from unbinned stock.
        return [(lot, None, quantity)]

    loose = unbinned(item, warehouse)
    if loose > 0:
        raise ValidationError(
            f"{loose} of {item} at {warehouse} is on the shelf with no bin recorded. "
            "A binned warehouse cannot route a pick around it; count it into a bin "
            "first."
        )
    return [
        (lot, chosen_bin, chosen_quantity)
        for chosen_bin, chosen_quantity in suggest_pick(item, warehouse, quantity, lot=lot)
    ]


def plan_putaway(item, warehouse, storage_bin=None):
    """
    Where arriving goods should land.

    Only answers when the warehouse insists on bins. Somewhere that does
    not track them has no answer to give, and inventing one would put
    stock on a shelf nobody asked for.
    """
    if storage_bin is not None:
        return storage_bin
    if not warehouse.requires_bins:
        return None
    chosen = suggest_putaway(item, warehouse)
    if chosen is None:
        raise ValidationError(
            f"{warehouse} requires a bin and has none that stock can sit in. Add a "
            "pickable bin before receiving into it."
        )
    return chosen


def describe_plan(plan):
    """A plan in words, for a pick list or an error message."""
    parts = []
    for lot, storage_bin, quantity in plan:
        where = []
        if lot is not None:
            where.append(f"batch {lot.code}")
        if storage_bin is not None:
            where.append(f"bin {storage_bin.code}")
        parts.append(f"{quantity}" + (f" from {' in '.join(where)}" if where else ""))
    return "; ".join(parts)
