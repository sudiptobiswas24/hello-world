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

The one rule every caller depends on: `cost_of_removing()` must return
exactly what `replay()` will take off, or the ledger entry and the stock
ledger disagree by the difference, permanently. The two are written next
to each other here for that reason, and every outbound path in every
module asks this module rather than doing its own arithmetic.
"""

from decimal import Decimal

from django.db import models


class CostingMethod(models.TextChoices):
    AVERAGE = "average", "Weighted average"
    FIFO = "fifo", "First in, first out"
    STANDARD = "standard", "Standard cost"


def _movements(item, warehouse=None, before_id=None, as_of=None):
    movements = item.movements.all()
    if warehouse is not None:
        movements = movements.filter(warehouse=warehouse)
    if before_id is not None:
        movements = movements.filter(id__lt=before_id)
    if as_of is not None:
        # Valuation as at a date has to stop at that date. Reports ask
        # this; the posting paths never do, because they price a movement
        # against everything that came before it.
        movements = movements.filter(occurred_at__date__lte=as_of)
    return movements.order_by("occurred_at", "id")


def replay(item, warehouse=None, before_id=None, as_of=None):
    """(quantity, value) on the shelf, unrounded, by this item's method."""
    method = item.costing_method
    if method == CostingMethod.FIFO:
        quantity, _layers, value = _replay_fifo(item, warehouse, before_id, as_of)
        return quantity, value
    if method == CostingMethod.STANDARD:
        return _replay_standard(item, warehouse, before_id, as_of)
    return _replay_average(item, warehouse, before_id, as_of)


def cost_of_removing(item, warehouse, quantity):
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

    if method == CostingMethod.STANDARD:
        return quantity * (item.standard_cost or Decimal("0"))

    if method == CostingMethod.FIFO:
        _held, layers, _value = _replay_fifo(item, warehouse)
        taken, remaining = Decimal("0"), quantity
        for layer_quantity, layer_cost in layers:
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

    held, value = _replay_average(item, warehouse)
    if held <= 0:
        return Decimal("0")
    return quantity * (value / held)


def unit_cost_for(item, warehouse, quantity):
    """
    `cost_of_removing` expressed per unit, for the places that must put
    a rate on a movement rather than a total.

    The remainder the rate cannot express is the caller's to carry; this
    only divides.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        return Decimal("0")
    return cost_of_removing(item, warehouse, quantity) / quantity


def _last_cost(layers, item):
    if layers:
        return layers[-1][1]
    return item.standard_cost or Decimal("0")


def _replay_average(item, warehouse=None, before_id=None, as_of=None):
    quantity = Decimal("0")
    value = Decimal("0")
    for movement in _movements(item, warehouse, before_id, as_of):
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


def _replay_fifo(item, warehouse=None, before_id=None, as_of=None):
    """
    Walk the ledger keeping the layers that are still on the shelf.

    Returns (quantity, layers, value). The layers are what makes FIFO
    FIFO, and they are rebuilt from the movements every time rather than
    stored, for the same reason the average is: a stored layer table
    drifts the moment a movement is corrected, and nothing would say so.
    """
    layers = []
    for movement in _movements(item, warehouse, before_id, as_of):
        if movement.quantity > 0:
            layers.append([movement.quantity, movement.unit_cost or Decimal("0"), movement.pk])
        elif movement.quantity < 0:
            remaining = -movement.quantity
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
                layers.append([-remaining, _last_cost(layers, item), movement.pk])
        if movement.value_adjustment:
            _apply_adjustment(layers, movement)

    quantity = sum((layer[0] for layer in layers), Decimal("0"))
    value = sum((layer[0] * layer[1] for layer in layers), Decimal("0"))
    return quantity, [(layer[0], layer[1]) for layer in layers], value


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


def _replay_standard(item, warehouse=None, before_id=None, as_of=None):
    """
    Quantity times the standard, and nothing else.

    What was actually paid does not touch stock value under this method
    — that is the whole point of it. The difference is a variance
    somebody analyses, and a landed cost that quietly raised the shelf's
    value would be that variance hiding in an asset account.
    """
    quantity = Decimal("0")
    for movement in _movements(item, warehouse, before_id, as_of):
        quantity += movement.quantity
    return quantity, quantity * (item.standard_cost or Decimal("0"))
