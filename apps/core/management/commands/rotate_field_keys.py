"""
manage.py rotate_field_keys: every sealed secret in the database sealed
again under the first of ERP_FIELD_KEYS (O255, apps/core/sealed.py).

To rotate: put the new key first in ERP_FIELD_KEYS, keep the old one after
it, restart, run this, and only then drop the old key. A value no key opens
is left as it is and named: its login is reset (Logins, Reset second
factor) and enrols again. The command then fails, so a script sees it.
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.sealed import SEALED_FIELDS, Unreadable, reseal


class Command(BaseCommand):
    help = "Seal every sealed secret again under the first key in ERP_FIELD_KEYS."

    def handle(self, *args, **options):
        resealed, unreadable = 0, []
        for label, field in SEALED_FIELDS:
            model = apps.get_model(label)
            with transaction.atomic():
                for row in model.objects.select_for_update(no_key=True).order_by("pk"):
                    try:
                        value = reseal(getattr(row, field))
                    except Unreadable:
                        unreadable.append(f"{label} {row.pk} ({row})")
                        continue
                    model.objects.filter(pk=row.pk).update(**{field: value})
                    resealed += 1
        self.stdout.write(f"{resealed} sealed again under the first key.")
        if unreadable:
            raise CommandError(
                f"{len(unreadable)} no key opens, left as they are: " + "; ".join(unreadable)
                + ". Reset each one's second factor; it enrols again at its next sign-in.")
