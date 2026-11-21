from django.db import migrations

from apps.accounting.migrations._holds_money import mark


def paid_from(apps, schema_editor):
    """Every account a claim was paid from."""
    claims = apps.get_model("hr", "ExpenseClaim").objects.exclude(paid_from=None)
    mark(apps, claims.values_list("paid_from", flat=True))


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0018_people"),
        ("accounting", "0023_account_holds_money"),
    ]

    operations = [
        migrations.RunPython(paid_from, migrations.RunPython.noop),
    ]
