"""
python manage.py setup_stores

The stores' masters a new plant cannot adjust or count stock without (O403):
the standard adjustment reasons, each booking its other side to the account
setup_indian_books seeds for it (5300 Stock written off, 4200 Stock found on
count). Nothing seeded them, and an adjustment or a count's difference could
not be booked until someone found the master and made them.

Each is a default for the accountant to review (accounting.SeededDefault).
A reason whose code is taken is left exactly as it is; run again, it adds only
what is missing. Run setup_indian_books first: without its accounts this
refuses, naming them.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounting.models import Account, SeededDefault
from apps.inventory.adjustments import AdjustmentDirection, AdjustmentReason

WRITTEN_OFF, FOUND = "5300", "4200"
# What each account is for, as setup_indian_books tells its own from a plant's account of that code: a 5300 that is
# something else was left alone there and the stock account seeded beside it as 5300-IN.
WORDS = {WRITTEN_OFF: ("written off", "write-off", "write off"), FOUND: ("found",)}


def stock_account(code):
    """The account setup_indian_books made for `code`'s purpose: `code` itself when its name says so, else its spare."""
    for candidate in Account.objects.filter(code__startswith=code).order_by("code"):
        if candidate.code != code and not candidate.code.startswith(f"{code}-IN"):
            continue
        if any(word in candidate.name.lower() for word in WORDS[code]):
            return candidate
    return None
REASONS = [
    ("DAMAGE", "Damaged or spoilt", WRITTEN_OFF, AdjustmentDirection.DECREASE),
    ("SHORT", "Short on count", WRITTEN_OFF, AdjustmentDirection.DECREASE),
    ("FOUND", "Found on count", FOUND, AdjustmentDirection.INCREASE),
]


class Command(BaseCommand):
    help = "Seed the standard stock adjustment reasons for review."

    def handle(self, *args, **options):
        accounts = {code: stock_account(code) for code in (WRITTEN_OFF, FOUND)}
        missing = [code for code, account in accounts.items() if account is None]
        if missing:
            raise CommandError(f"No account {', '.join(missing)}: run setup_indian_books first, which seeds "
                               "5300 Stock written off and 4200 Stock found on count.")
        made, kept = [], []
        with transaction.atomic():
            for code, name, account, direction in REASONS:
                if AdjustmentReason.objects.filter(code=code).exists():
                    kept.append(code)
                    continue
                reason = AdjustmentReason(code=code, name=name, account=accounts[account], direction=direction)
                reason.check_account()
                reason.save()
                SeededDefault.objects.create(model=reason._meta.label_lower, object_id=reason.pk,
                                             label=f"adjustment reason {code} {name} -> {account}")
                made.append(f"{code} {name} -> {account}")
        for line in made:
            self.stdout.write(f"seeded, to review: adjustment reason {line}")
        for code in kept:
            self.stdout.write(f"kept as it was: adjustment reason {code}")
        self.stdout.write(f"{len(made)} seeded for the accountant to review, {len(kept)} kept.")
