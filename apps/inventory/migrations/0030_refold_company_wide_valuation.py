from django.db import migrations


def drop_company_wide_snapshots(apps, schema_editor):
    # Folded when the company-wide replay still counted stock held for
    # vendors and customers. A snapshot is a cache of the replay, so the
    # next read folds a new one under the replay as it now is.
    apps.get_model("inventory", "StockValuationSnapshot").objects.filter(
        warehouse__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("inventory", "0029_warehouse_held_for")]

    operations = [migrations.RunPython(drop_company_wide_snapshots, migrations.RunPython.noop)]
