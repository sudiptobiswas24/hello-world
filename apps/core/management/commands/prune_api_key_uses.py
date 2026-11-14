"""
manage.py prune_api_key_uses [--months 18]: what each API key signed in, and
was refused, kept for 18 months and no longer. A bridge posting every few
seconds adds tens of thousands of rows a day. Run it from the nightly job.
"""

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.api_keys import ApiKeyUse


class Command(BaseCommand):
    help = "Delete API key uses older than --months (18)."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=18)

    def handle(self, *args, **options):
        cutoff = timezone.now() - datetime.timedelta(days=round(options["months"] * 365.25 / 12))
        gone, _ = ApiKeyUse.objects.filter(at__lt=cutoff).delete()
        self.stdout.write(f"{gone} API key uses before {timezone.localdate(cutoff)} deleted.")
