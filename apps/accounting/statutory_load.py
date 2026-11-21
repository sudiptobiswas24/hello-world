"""
Reading the dated statutory files kept in statutory/ (the repository's
root), and loading the TDS and TCS ones: shared by setup_statutory (hr), which
loads every file, and setup_indian_books, which seeds the scrap section's TCS
rates from the same rows, so the figure has one home.

Each row carries its source. A row is loaded unconfirmed unless its
`confirmed_by` column names a user who holds the permission to confirm it;
then it is confirmed as that user, with the day it was loaded. A row whose
key is taken is left exactly as it is and reported as kept: a figure that
something was taken at is a fact (dated_rates), and a correction is a new
row from the day it applies.
"""

import csv
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.core.models import to_date

from .statutory_names import statutory_dir


def rows(name):
    """The rows of statutory/<name>.csv, as dicts of text."""
    with open(statutory_dir() / f"{name}.csv", newline="", encoding="utf-8") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def number(text):
    return Decimal(text) if text not in ("", None) else None


def confirmer(row, permission):
    """The user the row says confirmed it, when they may: else None, and the row waits."""
    name = row.get("confirmed_by", "")
    if not name:
        return None
    user = get_user_model().objects.filter(username=name, is_active=True).first()
    return user if user is not None and user.has_perm(permission) else None


class Loader:
    """What one run loaded, kept and left waiting, in words for the person who ran it."""

    def __init__(self):
        self.made, self.kept, self.waiting, self.skipped = [], [], [], []

    def seeded(self, instance, label):
        from .models import SeededDefault

        SeededDefault.objects.create(model=instance._meta.label_lower, object_id=instance.pk, label=label[:255])
        self.made.append(label)

    def settle(self, instance, row, permission, label):
        """Confirm `instance` as the row's confirmer, or list it as waiting for the Controller."""
        user = confirmer(row, permission)
        if user is not None:
            instance.confirm(user)
            return
        self.waiting.append(f"{label}: {row.get('source', '')[:160]}")

    def report(self, write):
        for line in self.made:
            write(f"loaded: {line}")
        for line in self.kept:
            write(f"kept as it was: {line}")
        for line in self.skipped:
            write(f"not loaded: {line}")
        for line in self.waiting:
            write(f"waiting for the Controller to confirm: {line}")
        write(f"{len(self.made)} loaded, {len(self.kept)} kept, {len(self.skipped)} not loaded, "
              f"{len(self.waiting)} waiting for confirmation.")


def load_tcs(loader, payable_account=None):
    from .models import TcsRate, TcsSection

    for row in rows("tcs_rates"):
        section = TcsSection.objects.filter(code=row["section"]).first()
        if section is None:
            section = TcsSection.objects.create(
                code=row["section"], name=row["name"], form_code=row["form_code"], payable_account=payable_account,
                section_1961=row["section_1961"], section_2025=row["section_2025"])
            loader.seeded(section, f"TCS section {section.code} {section.name}")
        elif not (section.section_1961 or section.section_2025):
            TcsSection.objects.filter(pk=section.pk).update(section_1961=row["section_1961"],
                                                            section_2025=row["section_2025"])
        applies = to_date(row["applies_from"])
        if section.rates.filter(applies_from=applies).exists():
            loader.kept.append(f"TCS rate {section.code} from {applies}")
            continue
        rate = TcsRate.objects.create(section=section, applies_from=applies, section_code=row["law_section"],
                                      rate_percent=number(row["rate"]), no_pan_rate_percent=number(row["no_pan_rate"]),
                                      authority=row["source"][:128])
        label = f"TCS rate {row['law_section']} from {rate.applies_from:%d %b %Y}"
        loader.seeded(rate, f"{label}, unconfirmed: {row['question']}")
        loader.settle(rate, row, "accounting.confirm_tcsrate", label)


def load_tds(loader):
    from .models import Account, TdsRate, TdsSection

    payable = Account.objects.filter(code="2250").first()
    for row in rows("tds_rates"):
        section = TdsSection.objects.filter(code=row["section"]).first()
        if section is None:
            section = TdsSection.objects.create(
                code=row["section"], name=row["name"], mode=row["mode"],
                single_threshold=number(row["single_threshold"]), annual_threshold=number(row["annual_threshold"]),
                section_1961=row["section_1961"], section_2025=row["section_2025"], payable_account=payable)
            loader.seeded(section, f"TDS section {section.code} {section.name}")
        elif not (section.section_1961 or section.section_2025):
            TdsSection.objects.filter(pk=section.pk).update(section_1961=row["section_1961"],
                                                            section_2025=row["section_2025"])
        applies = to_date(row["applies_from"])
        if section.rates.filter(applies_from=applies).exists():
            loader.kept.append(f"TDS rate {section.code} from {applies}")
            continue
        rate = TdsRate.objects.create(section=section, applies_from=applies, rate_percent=number(row["rate"]),
                                      no_pan_rate_percent=number(row["no_pan_rate"]), authority=row["source"][:128])
        label = f"TDS rate {section.code} {rate.rate_percent.normalize():f}% from {rate.applies_from:%d %b %Y}"
        loader.seeded(rate, f"{label}, unconfirmed")
        loader.settle(rate, row, "accounting.confirm_tdsrate", label)
