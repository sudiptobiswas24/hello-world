"""
Each movement keeps its money: the paisa its document booked (O269).

Before this the shelf replayed each movement at its rate (eight places,
an average carried unrounded) while the ledger booked each document to
the paisa. A row written before it has no money of its own, so this walks
every shelf in the order the replay did, with the replay as it was, and
gives each row what it moved then, to the paisa: what its document booked
when the document was the row alone, which is every path but the few that
summed several lines before rounding. Where that figure was not already a
whole paisa the row's notes say so, and the summary printed says how many
rows and how much, shelf by shelf: what this could not read back.

Under the standard the shelf was quantity times today's standard, and a
change of standard a value-only row the replay never read. Each row is
given the standard in force when it moved, read back from today's through
each revaluation, and a revaluation its own amount.

Snapshots are a cache of the replay, and FIFO's layers now hold money
rather than rates: every one goes, and the next write refolds.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations, models

ZERO = Decimal("0")
CENT = Decimal("0.01")


def paisa(amount):
    return (amount + 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _average(rows):
    quantity = value = ZERO
    for row in rows:
        before = value
        if row.quantity > 0:
            value += row.quantity * (row.unit_cost or ZERO)
            quantity += row.quantity
        elif row.quantity < 0:
            average = (value / quantity) if quantity > 0 else ZERO
            value -= -row.quantity * average
            quantity += row.quantity
        if row.value_adjustment:
            value += row.value_adjustment
        yield row, value - before


def _fifo(rows, standard):
    layers = []

    def total():
        return sum((q * c for q, c, _pk in layers), ZERO)

    for row in rows:
        before = total()
        if row.quantity > 0:
            layers.append([row.quantity, row.unit_cost or ZERO, row.pk])
        elif row.quantity < 0:
            remaining = -row.quantity
            shortfall = layers[-1][1] if layers else standard
            while remaining > 0 and layers:
                drawn = min(layers[0][0], remaining)
                layers[0][0] -= drawn
                remaining -= drawn
                if layers[0][0] <= 0:
                    layers.pop(0)
            if remaining > 0:
                layers.append([-remaining, shortfall, row.pk])
        if row.value_adjustment:
            amount, landed = row.value_adjustment, False
            if row.adjusts_id is not None:
                for layer in layers:
                    if layer[2] == row.adjusts_id and layer[0] > 0:
                        layer[1] += amount / layer[0]
                        landed = True
                        break
                else:
                    landed = True  # its layer had shipped: the shelf kept none of it
            if not landed:
                whole = total()
                if whole <= 0:
                    held = sum((q for q, _c, _pk in layers), ZERO)
                    if held > 0:
                        for layer in layers:
                            layer[1] += amount / held
                else:
                    for layer in layers:
                        if layer[0] > 0:
                            layer[1] += (layer[0] * layer[1]) / whole * amount / layer[0]
        yield row, total() - before


def _specific(rows, standard):
    pools, arrived, last_in = {}, {}, {}
    for row in rows:
        pool = pools.setdefault(row.lot_id, [ZERO, ZERO])
        before = pool[1]
        if row.quantity > 0:
            pool[0] += row.quantity
            pool[1] += row.quantity * (row.unit_cost or ZERO)
            arrived[row.lot_id] = row.unit_cost
            last_in[row.lot_id] = row.unit_cost
        elif row.quantity < 0:
            if pool[0] > 0:
                average = pool[1] / pool[0]
            elif row.lot_id in arrived:
                average = arrived[row.lot_id] if arrived[row.lot_id] is not None else standard
            else:
                average = last_in.get(row.lot_id) or standard
            pool[1] -= -row.quantity * average
            pool[0] += row.quantity
        if row.value_adjustment:
            pool[1] += row.value_adjustment
        yield row, pool[1] - before


def _standard(rows, standard, others):
    rows = list(rows)
    held, running = [], ZERO
    for row in rows:
        held.append(running)
        running += row.quantity
    rate, moved = standard, [None] * len(rows)
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if others:
            moved[index] = ZERO
        elif row.quantity == 0:
            moved[index] = row.value_adjustment or ZERO
            if held[index] and row.value_adjustment:
                rate -= row.value_adjustment / held[index]
        else:
            moved[index] = row.quantity * rate
    return zip(rows, moved)


def give_each_movement_its_money(apps, schema_editor):
    Movement = apps.get_model("inventory", "StockMovement")
    Snapshot = apps.get_model("inventory", "StockValuationSnapshot")
    Item = apps.get_model("inventory", "Item")
    Warehouse = apps.get_model("inventory", "Warehouse")
    Snapshot.objects.all().delete()
    items = {item.pk: item for item in Item.objects.all()}
    others = {
        shelf.pk for shelf in Warehouse.objects.all()
        if shelf.consignment_vendor_id or shelf.held_for_id
    }
    positions = Movement.objects.order_by("item_id", "warehouse_id").values_list(
        "item_id", "warehouse_id").distinct()
    unread = defaultdict(lambda: [0, ZERO])
    for item_id, warehouse_id in positions:
        item = items[item_id]
        standard = item.standard_cost or ZERO
        rows = Movement.objects.filter(item_id=item_id, warehouse_id=warehouse_id).order_by("occurred_at", "id")
        method = item.costing_method
        if method == "fifo":
            walked = _fifo(rows.iterator(), standard)
        elif method == "specific":
            walked = _specific(rows.iterator(), standard)
        elif method == "standard":
            walked = _standard(rows.iterator(), standard, warehouse_id in others)
        else:
            walked = _average(rows.iterator())
        changed = []
        for row, exact in walked:
            row.value = paisa(exact)
            if row.value != exact:
                note = f"Money {row.value} given by inventory 0040 from {exact.normalize():f} as replayed then (O269)."
                row.notes = f"{row.notes}\n{note}" if row.notes else note
                tally = unread[(item.sku, warehouse_id)]
                tally[0] += 1
                tally[1] += exact - row.value
            if row.quantity == 0 and row.value_adjustment is not None:
                row.value_adjustment = row.value
            changed.append(row)
            if len(changed) >= 500:
                Movement.objects.bulk_update(changed, ["value", "notes", "value_adjustment"])
                changed = []
        if changed:
            Movement.objects.bulk_update(changed, ["value", "notes", "value_adjustment"])
    if unread:
        print("\n  inventory 0040: movements whose replayed money was not a whole paisa, rounded:")
        for (sku, warehouse_id), (count, dropped) in sorted(unread.items(), key=lambda pair: (pair[0][0], pair[0][1])):
            print(f"    {sku} in warehouse {warehouse_id}: {count} rows, {dropped.normalize():f} dropped")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0039_moved_from"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="value",
            field=models.DecimalField(decimal_places=2, max_digits=18, null=True, editable=False),
        ),
        migrations.RunPython(give_each_movement_its_money, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="stockmovement",
            name="value",
            field=models.DecimalField(
                decimal_places=2, editable=False, max_digits=18,
                help_text="What this movement put on its shelf (positive) or took off it (negative), to the paisa: exactly what its document booked to the inventory account. Every valuation sums these; the rate beside it is for reading and pricing only.",
            ),
        ),
    ]
