from django.db import migrations

from apps.accounting.migrations._holds_money import mark


def paid_from(apps, schema_editor):
    """Every account a TDS challan was paid from."""
    mark(apps, apps.get_model("purchasing", "TdsChallan").objects.values_list("bank_account", flat=True))


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0057_returned_payment_releases_fx"),
        ("accounting", "0023_account_holds_money"),
    ]

    operations = [
        migrations.RunPython(paid_from, migrations.RunPython.noop),
    ]
