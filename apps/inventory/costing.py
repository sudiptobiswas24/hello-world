"""
What stock is worth, and what it costs to take some off the shelf.

Weighted average was the only answer here, and it is a good default: it
needs no layer bookkeeping and it cannot be gamed by choosing which
physical unit to ship. It is not the only answer a business may be
required to give. A company reporting under a first-in-first-out policy,
or running to a standard cost with variances analysed monthly, cannot
use it and cannot bolt it on later without restating every period.

Three methods, one replay, chosen per item:

- **Average.** The running weighted average. What leaves costs what the
  shelf averages at that moment.
- **FIFO.** Each receipt is a layer. What leaves costs what the oldest
  layers cost, in the order they arrived. The shelf's total value is
  the sum of what is left.
- **Standard.** The shelf is worth quantity times the standard cost, and
  nothing else. What was actually paid is a separate fact, and the
  difference is a variance for somebody to explain, not a number to
  quietly absorb into inventory.
- **Specific identification.** Each batch keeps its own cost, and what
  leaves costs what *that* batch cost. Only available to an item that
  is tracked by lot or serial number, because the method's entire
  premise is knowing which physical goods left — an untracked item
  cannot say, and offering it the method would be offering an answer it
  has no way to compute.

  Within one batch the cost is still an average, because a batch that
  arrives twice at two prices has two prices and one identity. For
  serial tracking that average is over one unit and so is exact, which
  is the case the method is really for.

The one rule every caller depends on: `cost_of_removing()` must return
exactly what `replay()` will take off, or the ledger entry and the stock
ledger disagree by the difference, permanently. The two are written next
to each other here for that reason, and every outbound path in every
module asks this module rather than doing its own arithmetic.
"""

import contextvars
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class CostingMethod(models.TextChoices):
    AVERAGE = "average", "Weighted average"
    FIFO = "fifo", "First in, first out"
    STANDARD = "standard", "Standard cost"
    SPECIFIC = "specific", "Specific identification"


_PRELOADED = contextvars.ContextVar("preloaded_positions", default=None)


@contextmanager
def preloaded(items, warehouses, as_of=None):
    """
    Read every position a report walks in two queries, not two a position.

    A stock report asked each item and warehouse for its fold and then
    for the movements since it: 600 queries for 300 items, nearly two
    seconds. Inside this block the folds come from one query and the
    movements after each fold from another, and the replays — unchanged —
    walk those. Only the positions named, only at this `as_of`; anything
    else asks the database as before.
    """
    from .models import StockMovement
    from .snapshots import StockValuationSnapshot, usable_from

    items = [item for item in items]
    warehouses = [warehouse for warehouse in warehouses]
    item_ids = {item.pk for item in items}
    warehouse_ids = {warehouse.pk for warehouse in warehouses}
    folds = {}
    for snapshot in StockValuationSnapshot.objects.filter(
        item_id__in=item_ids, warehouse_id__in=warehouse_ids
    ):
        folds[(snapshot.item_id, snapshot.warehouse_id, snapshot.method)] = snapshot

    methods = {item.pk: item.costing_method for item in items}
    usable = {}
    for item_id in item_ids:
        for warehouse_id in warehouse_ids:
            usable[(item_id, warehouse_id)] = usable_from(
                folds.get((item_id, warehouse_id, methods[item_id])), as_of)
    # By lists of items and warehouses, each position's fold applied as
    # the rows are read. One OR clause a position, as this was, nests a
    # level a position: SQLite refused it short of a thousand (250 items
    # in four warehouses), "Expression tree is too large". The database
    # still starts at the earliest fold when every position has one.
    rows = StockMovement.objects.filter(item_id__in=item_ids, warehouse_id__in=warehouse_ids)
    boundaries = [fold.boundary_at for fold in usable.values() if fold is not None]
    if usable and len(boundaries) == len(usable):
        rows = rows.filter(occurred_at__gte=min(boundaries))
    if as_of is not None:
        rows = rows.filter(occurred_at__date__lte=as_of)
    movements = defaultdict(list)
    for movement in rows.order_by("occurred_at", "id"):
        key = (movement.item_id, movement.warehouse_id)
        fold = usable.get(key)
        if fold is not None and (movement.occurred_at, movement.pk) <= (fold.boundary_at, fold.boundary_id):
            continue
        movements[key].append(movement)

    token = _PRELOADED.set({"as_of": as_of, "folds": usable, "movements": movements})
    try:
        yield
    finally:
        _PRELOADED.reset(token)


def preloaded_fold(item, warehouse, as_of):
    """(True, fold) when this position was read in advance, else (False, None)."""
    held = _PRELOADED.get()
    if held is None or warehouse is None or held["as_of"] != as_of:
        return False, None
    key = (item.pk, warehouse.pk)
    if key not in held["folds"]:
        return False, None
    return True, held["folds"][key]


def _movements(item, warehouse=None, before_id=None, as_of=None, after=None):
    held = _PRELOADED.get()
    if (held is not None and warehouse is not None and before_id is None
            and held["as_of"] == as_of and (item.pk, warehouse.pk) in held["folds"]
            and held["folds"][(item.pk, warehouse.pk)] == after):
        return held["movements"].get((item.pk, warehouse.pk), [])
    movements = item.movements.all()
    if warehouse is not None:
        movements = movements.filter(warehouse=warehouse)
    else:
        # The company's stock is what the company owns. A vendor's
        # consignment and a customer's material sit here at no cost; left
        # in, they drag the average of the company's own stock towards
        # nothing, and every margin check and cost fallback that asks for
        # it prices low.
        movements = movements.filter(warehouse__consignment_vendor__isnull=True,
                                     warehouse__held_for__isnull=True)
    if before_id is not None:
        movements = movements.filter(id__lt=before_id)
    if as_of is not None:
        # Valuation as at a date has to stop at that date. Reports ask
        # this; the posting paths never do, because they price a movement
        # against everything that came before it.
        movements = movements.filter(occurred_at__date__lte=as_of)
    if after is not None:
        # Everything since a fold, in the order the replay walks: by when
        # it happened, then by id where two share a moment.
        movements = movements.filter(
            models.Q(occurred_at__gt=after.boundary_at)
            | models.Q(occurred_at=after.boundary_at, id__gt=after.boundary_id)
        )
    return movements.order_by("occurred_at", "id")


def _fold_to_start_from(item, warehouse, before_id, as_of):
    """
    The snapshot this replay may begin at, if any.

    A `before_id` replay is skipped: it cuts the ledger by insertion
    order while the replay walks it by when things happened, and the two
    only agree when nothing was ever backdated. It is asked for by tests
    and by nothing else, so it pays the full walk.
    """
    if before_id is not None:
        return None
    from .snapshots import usable_snapshot

    return usable_snapshot(item, warehouse, item.costing_method, as_of)


def replay(item, warehouse=None, before_id=None, as_of=None):
    """(quantity, value) on the shelf, unrounded, by this item's method."""
    fold = _fold_to_start_from(item, warehouse, before_id, as_of)
    method = item.costing_method
    if method == CostingMethod.SPECIFIC:
        quantity, _pools, value = _replay_specific(
            item, warehouse, before_id, as_of, fold
        )
        return quantity, value
    if method == CostingMethod.FIFO:
        quantity, _layers, value = _replay_fifo(item, warehouse, before_id, as_of, fold)
        return quantity, value
    if method == CostingMethod.STANDARD:
        return _replay_standard(item, warehouse, before_id, as_of, fold)
    return _replay_average(item, warehouse, before_id, as_of, fold)


def replay_with_state(item, warehouse=None):
    """
    The current position, the method's working state, and the movement
    the walk ended on — everything a fold needs to be written.
    """
    fold = _fold_to_start_from(item, warehouse, None, None)
    method = item.costing_method
    if method == CostingMethod.SPECIFIC:
        quantity, pools, value = _replay_specific(item, warehouse, None, None, fold)
        state = {"pools": {str(k): [str(p[0]), str(p[1])] for k, p in pools.items()}}
    elif method == CostingMethod.FIFO:
        quantity, layers, value = _replay_fifo(item, warehouse, None, None, fold)
        state = {"layers": [[str(q), str(c), pk] for q, c, pk in layers]}
    elif method == CostingMethod.STANDARD:
        quantity, value = _replay_standard(item, warehouse, None, None, fold)
        state = {}
    else:
        quantity, value = _replay_average(item, warehouse, None, None, fold)
        state = {}
    last = _movements(item, warehouse).last()
    return quantity, value, state, last


def cost_of_removing(item, warehouse, quantity, lot=None):
    """
    What taking `quantity` off this shelf will take off its value.

    A total, not a rate: under FIFO the units leaving may span two
    layers bought at different prices, and there is no single per-unit
    figure that multiplies back to the right answer.

    It assumes the movement it is being asked about will be the next one
    written, which is what every caller here does — compute the cost,
    then write the movement. A movement dated into the past would be
    replayed somewhere else in the sequence and priced differently, and
    that is a property of weighted average and FIFO rather than of this
    function.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        return Decimal("0")
    method = item.costing_method

    if method == CostingMethod.SPECIFIC:
        if lot is None:
            # The method's whole premise is knowing which goods left.
            # Guessing here would be weighted average wearing another
            # name, and it would be wrong by exactly the amount the
            # method exists to get right.
            raise ValidationError(
                f"{item} is costed by specific identification, so what a withdrawal "
                "costs depends on which batch it comes from. Say which."
            )
        _held, pools, _value = _replay_specific(
            item, warehouse, fold=_fold_to_start_from(item, warehouse, None, None)
        )
        pool_quantity, pool_value = pools.get(lot.pk, (Decimal("0"), Decimal("0")))
        if pool_quantity <= 0:
            return quantity * _last_cost_for_lot(item, warehouse, lot)
        return quantity * (pool_value / pool_quantity)

    if method == CostingMethod.STANDARD:
        return quantity * (item.standard_cost or Decimal("0"))

    if method == CostingMethod.FIFO:
        _held, layers, _value = _replay_fifo(
            item, warehouse, fold=_fold_to_start_from(item, warehouse, None, None)
        )
        taken, remaining = Decimal("0"), quantity
        for layer_quantity, layer_cost, _source in layers:
            if remaining <= 0:
                break
            drawn = min(layer_quantity, remaining)
            taken += drawn * layer_cost
            remaining -= drawn
        if remaining > 0:
            # Shipping into negative stock. There is no layer to price it
            # from, so the last known cost is the only honest guess, and
            # the receipt that eventually covers it will correct the total.
            taken += remaining * _last_cost(layers, item)
        return taken

    held, value = _replay_average(
        item, warehouse, fold=_fold_to_start_from(item, warehouse, None, None)
    )
    if held <= 0:
        return Decimal("0")
    return quantity * (value / held)


def holds_value(item, warehouse, adjusts=None, lot=None):
    """
    Whether a value-only movement written here next would land on goods.

    The mirror of `cost_of_removing` for value with no quantity, and for
    the same reason beside the replays: a landed cost posted to the
    inventory account that the shelf does not take parts the two for good.
    It does not land when the receipt layer it names has gone (FIFO: the
    replay drops it), when the shelf is valued at the standard and nothing
    else, or when there is nothing there to carry it, which would leave
    value on an empty shelf for whatever arrives next. A caller posts that
    part somewhere else.
    """
    fold = _fold_to_start_from(item, warehouse, None, None)
    method = item.costing_method
    if method == CostingMethod.STANDARD:
        return False
    if method == CostingMethod.SPECIFIC:
        _held, pools, _value = _replay_specific(item, warehouse, fold=fold)
        return pools.get(lot.pk if lot is not None else None, (Decimal("0"),))[0] > 0
    if method == CostingMethod.FIFO:
        _held, layers, _value = _replay_fifo(item, warehouse, fold=fold)
        if adjusts is not None:
            # What _apply_adjustment does with it: onto that layer, or nowhere.
            return any(source == adjusts.pk and quantity > 0 for quantity, _cost, source in layers)
        return (sum((quantity for quantity, _cost, _source in layers), Decimal("0")) > 0
                or sum((quantity * cost for quantity, cost, _source in layers), Decimal("0")) > 0)
    held, _value = _replay_average(item, warehouse, fold=fold)
    return held > 0


def unit_cost_for(item, warehouse, quantity, lot=None):
    """
    `cost_of_removing` expressed per unit, for the places that must put
    a rate on a movement rather than a total.

    The remainder the rate cannot express is the caller's to carry; this
    only divides.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        return Decimal("0")
    return cost_of_removing(item, warehouse, quantity, lot=lot) / quantity


def _last_cost(layers, item):
    if layers:
        return layers[-1][1]
    return item.standard_cost or Decimal("0")


def _last_cost_for_lot(item, warehouse, lot, before=None):
    """
    What a batch cost the last time any of it arrived, before `before`
    when a replay asks it of a movement in the ledger.

    Only reached when the batch is empty and something is still going
    out of it — shipping into negative stock. The price it last came in
    at is the only honest guess, and the receipt that covers the
    shortfall will correct the total.
    """
    latest = item.movements.filter(lot=lot, quantity__gt=0)
    if warehouse is not None:
        latest = latest.filter(warehouse=warehouse)
    if before is not None:
        latest = latest.filter(models.Q(occurred_at__lt=before.occurred_at)
                               | models.Q(occurred_at=before.occurred_at, id__lt=before.pk))
    latest = latest.order_by("-occurred_at", "-id").first()
    if latest is not None and latest.unit_cost is not None:
        return latest.unit_cost
    return item.standard_cost or Decimal("0")


def _replay_specific(item, warehouse=None, before_id=None, as_of=None, fold=None):
    """
    Walk the ledger keeping each batch's own cost.

    Returns (quantity, pools, value), where pools maps a lot to what is
    left of it and what that is worth. A batch is its own little
    warehouse: goods entering it raise its value, goods leaving take
    their share of it, and a landed cost attached to a receipt lands on
    the batch that receipt brought in.

    Movements with no lot are pooled together under None. An item costed
    this way must be tracked, so that pool only ever holds history
    written before the method was chosen — and it is still counted,
    because it is still on the shelf.
    """
    pools = {}
    if fold is not None:
        pools = {
            (None if key == "None" else int(key)): [Decimal(pair[0]), Decimal(pair[1])]
            for key, pair in fold.state.get("pools", {}).items()
        }
    arrived_at = {}
    for movement in _movements(item, warehouse, before_id, as_of, fold):
        pool = pools.setdefault(movement.lot_id, [Decimal("0"), Decimal("0")])
        if movement.quantity > 0:
            pool[0] += movement.quantity
            pool[1] += movement.quantity * (movement.unit_cost or Decimal("0"))
            arrived_at[movement.lot_id] = movement.unit_cost
        elif movement.quantity < 0:
            leaving = -movement.quantity
            if pool[0] > 0:
                average = pool[1] / pool[0]
            elif movement.lot_id in arrived_at:
                # An empty batch prices what still leaves it at what it last
                # came in at, as cost_of_removing does: priced at nothing
                # here, a write-off of a batch at minus three credited the
                # ledger 700 and took nothing off the shelf.
                average = arrived_at[movement.lot_id]
                if average is None:
                    average = item.standard_cost or Decimal("0")
            else:
                average = _last_cost_for_lot(item, warehouse, movement.lot_id, before=movement)
            pool[1] -= leaving * average
            pool[0] -= leaving
        if movement.value_adjustment:
            # Landed cost belongs to the goods it was incurred on, and a
            # value-only movement carries the batch it was incurred for.
            pool[1] += movement.value_adjustment

    quantity = sum((pool[0] for pool in pools.values()), Decimal("0"))
    value = sum((pool[1] for pool in pools.values()), Decimal("0"))
    return quantity, {key: tuple(pool) for key, pool in pools.items()}, value


def _replay_average(item, warehouse=None, before_id=None, as_of=None, fold=None):
    quantity = fold.quantity if fold is not None else Decimal("0")
    value = fold.value if fold is not None else Decimal("0")
    for movement in _movements(item, warehouse, before_id, as_of, fold):
        if movement.quantity > 0:
            value += movement.quantity * (movement.unit_cost or Decimal("0"))
            quantity += movement.quantity
        elif movement.quantity < 0:
            leaving = -movement.quantity
            average = (value / quantity) if quantity > 0 else Decimal("0")
            value -= leaving * average
            quantity -= leaving
        # A value-only movement changes what the stock is worth without
        # changing how much there is, which is exactly what landed cost
        # does. Replaying it here rather than storing a corrected average
        # keeps valuation derived, like everything else.
        if movement.value_adjustment:
            value += movement.value_adjustment
    return quantity, value


def _replay_fifo(item, warehouse=None, before_id=None, as_of=None, fold=None):
    """
    Walk the ledger keeping the layers that are still on the shelf.

    Returns (quantity, layers, value). The layers are what makes FIFO
    FIFO, and they are rebuilt from the movements every time rather than
    stored, for the same reason the average is: a stored layer table
    drifts the moment a movement is corrected, and nothing would say so.
    """
    layers = []
    if fold is not None:
        # The movement each layer came from is kept, not dropped. A
        # landed cost names the receipt it was incurred on, and that
        # invoice can arrive months after the receipt has been folded in
        # — a layer with no id would not match, the cost would be
        # silently discarded, and the shelf would disagree with the
        # ledger by the freight.
        layers = [
            [Decimal(quantity), Decimal(cost), pk]
            for quantity, cost, pk in fold.state.get("layers", [])
        ]
    for movement in _movements(item, warehouse, before_id, as_of, fold):
        if movement.quantity > 0:
            layers.append([movement.quantity, movement.unit_cost or Decimal("0"), movement.pk])
        elif movement.quantity < 0:
            remaining = -movement.quantity
            # A shortfall is priced at the newest layer as it stood before
            # any of it was drawn, which is what cost_of_removing asks.
            # Asked after the layers were emptied, the five beyond a shelf
            # of three went below zero at nothing while the ledger was
            # credited 50 for them.
            shortfall_cost = _last_cost(layers, item)
            while remaining > 0 and layers:
                drawn = min(layers[0][0], remaining)
                layers[0][0] -= drawn
                remaining -= drawn
                if layers[0][0] <= 0:
                    layers.pop(0)
            if remaining > 0:
                # Below zero. A negative layer priced at the last known
                # cost keeps quantity honest, and the next receipt fills
                # it in the order everything else is filled.
                layers.append([-remaining, shortfall_cost, movement.pk])
        if movement.value_adjustment:
            _apply_adjustment(layers, movement)

    quantity = sum((layer[0] for layer in layers), Decimal("0"))
    value = sum((layer[0] * layer[1] for layer in layers), Decimal("0"))
    return quantity, [(layer[0], layer[1], layer[2]) for layer in layers], value


def _apply_adjustment(layers, movement):
    """
    Put a value-only movement on the layer it belongs to.

    Landed cost belongs to the goods it was incurred on, and says which
    receipt those were, so it lands on that receipt's layer. Anything
    else — a revaluation, a correction — belongs to the shelf as a whole
    and is spread across what is left in proportion to value. Spreading
    is a judgement, said out loud rather than dressed up as precision.
    """
    amount = movement.value_adjustment
    target = movement.adjusts_id
    if target is not None:
        for layer in layers:
            if layer[2] == target and layer[0] > 0:
                layer[1] += amount / layer[0]
                return
        # The layer it belonged to has already shipped. Its cost went out
        # with it, so there is nothing left here to adjust; the ledger
        # keeps the amount and the shelf correctly does not.
        return

    total = sum((layer[0] * layer[1] for layer in layers), Decimal("0"))
    if total <= 0:
        held = sum((layer[0] for layer in layers), Decimal("0"))
        if held > 0:
            per_unit = amount / held
            for layer in layers:
                layer[1] += per_unit
        return
    for layer in layers:
        if layer[0] <= 0:
            continue
        share = (layer[0] * layer[1]) / total * amount
        layer[1] += share / layer[0]


def _replay_standard(item, warehouse=None, before_id=None, as_of=None, fold=None):
    """
    Quantity times the standard, and nothing else.

    What was actually paid does not touch stock value under this method
    — that is the whole point of it. The difference is a variance
    somebody analyses, and a landed cost that quietly raised the shelf's
    value would be that variance hiding in an asset account.
    """
    quantity = fold.quantity if fold is not None else Decimal("0")
    for movement in _movements(item, warehouse, before_id, as_of, fold):
        quantity += movement.quantity
    # Value from the standard in force now, never from the fold: the
    # standard changes, and a folded value would be what the shelf was
    # deemed to be worth under the old one.
    return quantity, quantity * (item.standard_cost or Decimal("0"))
