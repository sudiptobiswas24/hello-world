"""
The morning's checks, run once a day by cron, and each person told what
is theirs: one mail a login with something waiting, nothing to the rest.
Where no mail server is set up it prints the same lines instead of
failing, so the inbox on the home page is never the only place.

First it sends each quality failure left undecided past the next working
day to the administrator (decision 22; apps.quality.decisions), so their
to-do is in this same morning's mail. Its own command,
escalate_quality_decisions, was scheduled nowhere: no cron line, the
RUNBOOK or a deploy file named it, and nothing was ever escalated. Escalated
once (`Inspection.escalated_on`), so a second run the same day sends nothing.
A dry run escalates nothing.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand

from apps.web.checks import inbox


class Command(BaseCommand):
    help = "Run the morning checks and mail each person what is theirs to act on today."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print what would be mailed; send nothing.")

    def handle(self, *args, **options):
        from apps.quality.decisions import escalate_overdue

        if not options["dry_run"]:
            escalated = escalate_overdue()
            self.stdout.write(f"{len(escalated)} quality decision(s) overdue sent to the administrator.")
        mailed, quiet, printed = 0, 0, 0
        counted = {}  # the plant-wide answers, found once and told to everyone they are for
        for user in get_user_model().objects.filter(is_active=True).order_by("username"):
            rows = inbox(user, counted=counted)
            if not rows:
                quiet += 1
                continue
            lines = [f"{row['label']}: {row['count']}" for row in rows]

            def show():
                nonlocal printed
                self.stdout.write(f"{user.username}:\n  " + "\n  ".join(lines))
                printed += 1

            if options["dry_run"] or not user.email:
                show()
                continue
            try:
                EmailMessage(subject="Yours to act on today", body="\n".join(lines), to=[user.email]).send()
                mailed += 1
            except ValidationError as refused:
                # No mail server: say so once, and print what would have gone.
                self.stdout.write(" ".join(refused.messages))
                show()
        self.stdout.write(f"{mailed} mailed, {printed} printed, {quiet} with nothing waiting.")
