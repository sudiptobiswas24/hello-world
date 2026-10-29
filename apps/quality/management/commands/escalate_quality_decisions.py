"""
Failures under an advisory plan left undecided past the next working day,
each sent to the administrator: a to-do for every active superuser, and a
mail. Run once a day by cron, before the morning checks. Safe to run
again: an inspection is escalated once (`Inspection.escalated_on`).
"""

from django.core.management.base import BaseCommand

from apps.quality.decisions import escalate_overdue


class Command(BaseCommand):
    help = "Send failures left undecided past the next working day to the administrator."

    def handle(self, *args, **options):
        found = escalate_overdue()
        for inspection, told in found:
            line = f"{inspection.number} ({inspection.lot}): told {', '.join(told.told) or 'nobody'}."
            self.stdout.write(" ".join([line, *told.failures()]))
        self.stdout.write(f"{len(found)} escalated.")
