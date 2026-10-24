"""
Customer returns posted before returns recorded their allocations get
them now, from the stock movements they wrote, so a lot trace can read
every delivery both ways through links.

Each receipt a return posted is attached to the first line of that
return for the same item. A trace needs the customer, the batch and the
quantity, and those are exact; which of two lines of the same item on
one return took which movement is not recorded anywhere to recover.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Delivery = apps.get_model("sales", "Delivery")
    DeliveryAllocation = apps.get_model("sales", "DeliveryAllocation")
    StockMovement = apps.get_model("inventory", "StockMovement")
    returns = Delivery.objects.filter(posted=True, reverses__isnull=False).exclude(number="")
    for delivery in returns:
        lines = list(delivery.lines.select_related("order_line").order_by("id"))
        if DeliveryAllocation.objects.filter(line__in=lines).exists():
            continue
        by_item = {}
        for line in lines:
            by_item.setdefault(line.order_line.item_id, line)
        for movement in StockMovement.objects.filter(
            reference=delivery.number, movement_type="receipt",
        ).order_by("id"):
            line = by_item.get(movement.item_id)
            if line is None:
                continue
            DeliveryAllocation.objects.create(
                line=line, lot_id=movement.lot_id, bin_id=movement.bin_id,
                quantity=movement.quantity, movement=movement,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0031_recorded_taxes"),
        ("inventory", "0027_item_hsn_code"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
