from django.db import migrations, models
from django.db.models import Count, Q


def refuse_two_on_one_order(apps, schema_editor):
    """
    Two opportunities won into one sales order are reported, not merged (O136): which of them the order
    answers to is a person's decision, and the constraint cannot stand until it is made.
    """
    Opportunity = apps.get_model("sales", "Opportunity")
    SalesOrder = apps.get_model("sales", "SalesOrder")
    clashing = list(Opportunity.objects.filter(sales_order__isnull=False).values("sales_order").annotate(
        won=Count("id")).filter(won__gt=1).order_by("sales_order").values_list("sales_order", flat=True))
    if not clashing:
        return
    lines = []
    for order in SalesOrder.objects.filter(pk__in=clashing).order_by("pk"):
        won = Opportunity.objects.filter(sales_order=order).order_by("pk")
        names = ", ".join(f"{opportunity.number or opportunity.pk} ({opportunity.title})" for opportunity in won)
        lines.append(f"sales order {order.number or order.pk}: {names}")
    raise RuntimeError(
        "More than one opportunity was won into one sales order, and an order answers to one: "
        + "; ".join(lines)
        + ". Unlink all but one of each (set sales_order to NULL on the others), then migrate again.")


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0076_debit_note"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="opportunity",
            constraint=models.UniqueConstraint(condition=Q(sales_order__isnull=False), fields=("sales_order",),
                                               name="one_opportunity_won_into_an_order"),
        ),
    ]
