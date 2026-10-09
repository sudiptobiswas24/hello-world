"""
An allocation keeps the day it was applied, the day its exchange difference
is realised (accounting.settlement.RealisedOnItsOwnDay). Allocations made
before this realised theirs on the payment's day, so that is the day they
are given: what they booked does not move.
"""

from django.db import migrations, models


def dated_as_they_were(apps, schema_editor):
    model = apps.get_model("sales", "InvoicePayment")
    for allocation in model.objects.filter(date__isnull=True).select_related("payment").iterator():
        model.objects.filter(pk=allocation.pk).update(date=allocation.payment.payment_date)


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0062_allocation_keeps_its_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoicepayment",
            name="date",
            field=models.DateField(null=True, blank=True),
        ),
        migrations.RunPython(dated_as_they_were, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoicepayment",
            name="date",
            field=models.DateField(
                blank=True,
                help_text="The day the payment was applied: the exchange difference is realised that day. "
                          "Left out, today; never before the payment or the document, nor a day to come.",
            ),
        ),
    ]
