"""
O200: 0065 seeded an approval's figures from its note, matching each line
by its label, and could not name a charge line: a historical model has no
ChargeType.__str__, so a charge line cut after its approval was seeded at
what it held and lost the cover the approval gave it. ChargeType.__str__
is its name, which the historical model does hold: matched by it here.

Only orders approved before 0065 ran, which are the ones it seeded (an
approval since records its own figures as it is given), and only ever
raised to what the note names, never lowered, as 0065 seeded them.

What the history cannot tell is left as 0065 left it: an approval given
with a note of the approver's own names no figures, and the record of
changes (core.RecordEvent) names the fields a change touched, not what
they held, so a cut line under such a note keeps what it holds; and a
total or margin the note does not name stays empty (held to the order as
it stood before a change), since the approver's costs are not today's.
"""

import re
from decimal import Decimal

from django.db import migrations

DISCOUNT = re.compile(r"'(?P<label>.*)' is discounted (?P<figure>-?[\d.]+)%, above the ")


def seeded_before(schema_editor):
    """When 0065 ran: orders approved before it are the ones it seeded."""
    from django.db.migrations.recorder import MigrationRecorder

    row = MigrationRecorder(schema_editor.connection).migration_qs.filter(
        app="sales", name="0065_seed_approved_figures").first()
    return row.applied if row else None


def seed(apps, schema_editor):
    ran = seeded_before(schema_editor)
    if ran is None:
        return
    orders = apps.get_model("sales", "SalesOrder")
    for order in orders.objects.filter(approved_at__isnull=False, approved_at__lt=ran,
                                       approved_figures__isnull=False):
        noted = {}
        for reason in (order.approval_note or "").split("; "):
            if found := DISCOUNT.match(reason):
                noted.setdefault(found["label"], []).append(Decimal(found["figure"]))
        if not noted:
            continue
        figures = dict(order.approved_figures)
        discounts = dict(figures.get("discounts") or {})
        changed = False
        for line in order.lines.select_related("charge").filter(
                charge__isnull=False, description="", created_at__lte=order.approved_at,
                updated_at__gt=order.approved_at):
            kept = discounts.get(str(line.pk))
            figure = max([Decimal(kept) if kept is not None else line.discount_percent,
                          *noted.get(line.charge.name, [])])
            if kept is None or figure > Decimal(kept):
                discounts[str(line.pk)] = str(figure)
                changed = True
        if changed:
            figures["discounts"] = discounts
            orders.objects.filter(pk=order.pk).update(approved_figures=figures)


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0066_write_off_recovered_amount"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
