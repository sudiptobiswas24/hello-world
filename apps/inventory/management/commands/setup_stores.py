"""
python manage.py setup_stores

The stores' masters a new plant cannot make an item, adjust or count stock without: the units of measure a woven
sack plant buys, makes and sells in, each with its GST unit code (none existed on a fresh install, so no item could
be made or imported: seen on a first installation, 2 November), and (O403)
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

WRITTEN_OFF, FOUND, OPENING = "5300", "4200", "3900"
# What each account is for, as setup_indian_books tells its own from a plant's account of that code: a 5300 that is
# something else was left alone there and the stock account seeded beside it as 5300-IN.
WORDS = {WRITTEN_OFF: ("written off", "write-off", "write off"), FOUND: ("found",), OPENING: ("opening",)}


def stock_account(code):
    """The account setup_indian_books made for `code`'s purpose: `code` itself when its name says so, else its spare."""
    for candidate in Account.objects.filter(code__startswith=code).order_by("code"):
        if candidate.code != code and not candidate.code.startswith(f"{code}-IN"):
            continue
        if any(word in candidate.name.lower() for word in WORDS[code]):
            return candidate
    return None
# (code, name, category, base unit code or None, how many of the base one is, GST unit quantity code).
UNITS = [
    ("kg", "Kilogram", "weight", None, "1", "KGS"), ("g", "Gram", "weight", "kg", "0.001", "GMS"),
    ("t", "Tonne", "weight", "kg", "1000", "TON"),
    ("nos", "Numbers", "count", None, "1", "NOS"), ("pcs", "Pieces", "count", None, "1", "PCS"),
    ("bag", "Bags", "count", None, "1", "BAG"), ("bale", "Bales", "count", None, "1", "BAL"),
    ("roll", "Rolls", "count", None, "1", "ROL"),
    ("m", "Metre", "length", None, "1", "MTR"), ("L", "Litre", "volume", None, "1", "LTR"),
]
REASONS = [
    ("DAMAGE", "Damaged or spoilt", WRITTEN_OFF, AdjustmentDirection.DECREASE),
    ("SHORT", "Short on count", WRITTEN_OFF, AdjustmentDirection.DECREASE),
    ("FOUND", "Found on count", FOUND, AdjustmentDirection.INCREASE),
    # The opening position's stock (docs/IMPORT.md: import_csv opening_stock takes OPENING unless told otherwise).
    ("OPENING", "Opening stock", OPENING, AdjustmentDirection.INCREASE),
]


class Command(BaseCommand):
    help = "Seed the standard stock adjustment reasons for review."

    def handle(self, *args, **options):
        accounts = {code: stock_account(code) for code in (WRITTEN_OFF, FOUND, OPENING)}
        missing = [code for code, account in accounts.items() if account is None]
        if missing:
            raise CommandError(f"No account {', '.join(missing)}: run setup_indian_books first, which seeds "
                               "5300 Stock written off, 4200 Stock found on count and 3900 Opening balances.")
        made, kept = [], []
        with transaction.atomic():
            self.units(made, kept)
            for code, name, account, direction in REASONS:
                if AdjustmentReason.objects.filter(code=code).exists():
                    kept.append(f"adjustment reason {code}")
                    continue
                reason = AdjustmentReason(code=code, name=name, account=accounts[account], direction=direction)
                reason.check_account()
                reason.save()
                SeededDefault.objects.create(model=reason._meta.label_lower, object_id=reason.pk,
                                             label=f"adjustment reason {code} {name} -> {account}")
                made.append(f"adjustment reason {code} {name} -> {account}")
        for line in made:
            self.stdout.write(f"seeded, to review: {line}")
        for line in kept:
            self.stdout.write(f"kept as it was: {line}")
        self.stdout.write(f"{len(made)} seeded for the accountant to review, {len(kept)} kept.")

    def units(self, made, kept):
        """Each unit made if its code is free, with its GST code where the GST module is installed (no import edge)."""
        from decimal import Decimal

        from django.apps import apps

        from apps.core.models import UnitOfMeasure

        uqc = apps.get_model("gst", "UnitQuantityCode") if apps.is_installed("apps.gst") else None
        for code, name, category, base, factor, gst_code in UNITS:
            if UnitOfMeasure.objects.filter(code=code).exists():
                kept.append(f"unit {code}")
                continue
            unit = UnitOfMeasure.objects.create(
                code=code, name=name, category=category, conversion_factor=Decimal(factor),
                base_unit=UnitOfMeasure.objects.get(code=base) if base else None)
            if uqc is not None:
                uqc.objects.create(uom=unit, code=gst_code)
            SeededDefault.objects.create(model=unit._meta.label_lower, object_id=unit.pk,
                                         label=f"unit {code} {name}, GST {gst_code}")
            made.append(f"unit {code} {name}, GST {gst_code}")

