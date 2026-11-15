"""
O365: a punched day corrected by hand before O304 kept source=import, so the
next punch file wrote over the correction. The punch file writes only a
present day with a time in and a time out; overtime and a note are never
its. A day still marked as the file's that holds anything else (another
status, a missing time, overtime, a note) was corrected by a hand, and is
marked as entered so the file keeps it as it is. A hand that changed only a
time leaves nothing to tell it by: that day stays the file's.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import Q


def hand_corrected(apps, schema_editor):
    day = apps.get_model("hr", "AttendanceDay")
    day.objects.filter(source="import").filter(
        ~Q(status="present") | Q(time_in__isnull=True) | Q(time_out__isnull=True)
        | Q(overtime_hours__gt=Decimal("0")) | ~Q(note="")).update(source="manual")


class Migration(migrations.Migration):
    dependencies = [("hr", "0042_the_gratuity_ceiling_is_a_dated_figure")]

    # Backwards changes nothing: which day was the file's before cannot be told from a hand's once both are entered.
    operations = [migrations.RunPython(hand_corrected, migrations.RunPython.noop)]
