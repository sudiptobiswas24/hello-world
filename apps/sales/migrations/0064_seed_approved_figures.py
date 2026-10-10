"""
O174: 0063 added approved_figures empty, so an order approved before it had
no approval to change within: approved at 20%, cut to 10%, it could not go
back to 20% ("needs approval beyond what it has"), and approve() refuses a
second approval. Seeded here from what the order holds, which is what its
approval stood on: a draft or awaiting order loses its approval on any change
past it, and a confirmed one changed only within the policy.
"""

from django.db import migrations


def seed(apps, schema_editor):
    kept = apps.get_model("sales", "SalesOrder")
    pks = list(kept.objects.filter(approved_at__isnull=False, approved_figures__isnull=True)
               .values_list("pk", flat=True))
    if not pks:
        return
    # The total and the margin are the model's arithmetic (taxes, charges, costs), which a
    # historical model does not carry: the live one works them out, as approve() does. Asked
    # only when there is an order to seed, so a new database never imports it.
    from apps.sales.models import SalesOrder

    for order in SalesOrder.objects.filter(pk__in=pks):
        kept.objects.filter(pk=order.pk).update(approved_figures=order.figures_as_kept())


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0063_salesorder_approved_figures"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
