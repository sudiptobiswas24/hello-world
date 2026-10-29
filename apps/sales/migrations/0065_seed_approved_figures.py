"""
O174: 0063 added approved_figures empty, so an order approved before it had
no approval to change within: approved at 20%, cut to 10%, it could not go
back to 20% ("needs approval beyond what it has"), and approve() refuses a
second approval.

Seeded here from what the approval stood on, with the models as they stood
at this migration, never the live ones (a later column would break it, and
today's costs and taxes are not the approver's):
- a line's discount: as the line holds it, when the line has not changed
  since the approval (its updated_at); otherwise the higher of what it holds
  and what the approval's note names for it ("'X' is discounted 20.00%"),
  so an order cut after its approval keeps the approval it had (O186);
- the total and the margin: what the note names, when the approval was for
  them; otherwise none, and a change is held to the order as it stood
  before it. An approval given with a note of the approver's own names no
  figures: its cut lines are seeded at what they hold, as nothing else
  says what they were.
A line added after the approval was not covered by it and is left out.
"""

import re
from decimal import Decimal

from django.db import migrations

DISCOUNT = re.compile(r"'(?P<label>.*)' is discounted (?P<figure>-?[\d.]+)%, above the ")
TOTAL = re.compile(r"The order is (?P<figure>-?[\d.]+), above the ")
MARGIN = re.compile(r"Gross margin is (?P<figure>-?[\d.]+)%, below the ")


def label(line):
    """SalesOrderLine.label() as it stood: the description, the charge, or the item."""
    if line.description:
        return line.description
    if line.charge_id:
        return None  # a charge is named by its own __str__, which a historical model does not carry
    if line.item_id:
        return f"{line.item.sku} - {line.item.name}"
    return "—"


def seed(apps, schema_editor):
    orders = apps.get_model("sales", "SalesOrder")
    for order in orders.objects.filter(approved_at__isnull=False, approved_figures__isnull=True):
        noted, total, margin = {}, None, None
        for reason in (order.approval_note or "").split("; "):
            if found := DISCOUNT.match(reason):
                noted.setdefault(found["label"], []).append(Decimal(found["figure"]))
            elif found := TOTAL.match(reason):
                total = found["figure"]
            elif found := MARGIN.match(reason):
                margin = found["figure"]
        discounts = {}
        for line in order.lines.select_related("item").filter(created_at__lte=order.approved_at):
            figure = line.discount_percent
            if line.updated_at > order.approved_at:
                figure = max([figure, *noted.get(label(line), [])])
            discounts[str(line.pk)] = str(figure)
        orders.objects.filter(pk=order.pk).update(
            approved_figures={"discounts": discounts, "total": total, "margin": margin})


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0064_salesorder_approved_figures"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
