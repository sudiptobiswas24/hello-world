from django.db import migrations


def drop_fifo_and_specific_snapshots(apps, schema_editor):
    # Folded when the FIFO and specific-identification replays priced what
    # went below zero at nothing, while what it was booked at was the last
    # cost. A snapshot is a cache of the replay, so the next read folds a new
    # one under the replay as it now is.
    apps.get_model("inventory", "StockValuationSnapshot").objects.filter(
        method__in=["fifo", "specific"]).delete()


class Migration(migrations.Migration):
    dependencies = [("inventory", "0036_adjustment_line_keeps_its_value")]

    operations = [migrations.RunPython(drop_fifo_and_specific_snapshots, migrations.RunPython.noop)]
