"""
Load the Income-tax blocks of assets and their rates, unconfirmed, for the accountant (assets.tax).

Each rate is a row from the day it applies. Two rows per block: from 1 April 2017 under the 1961 Act
(Appendix I to the Income-tax Rules, 1962, as it has stood since the 2017-18 rates came in), and from
1 April 2026 under the Income-tax Act, 2025 (section 33 and the Income-tax Rules, 2026), at the same
percentage, which secondary sources report carried over. Neither text could be read at its official
source from where this was written: every row is loaded unconfirmed, and no year is filed at a rate
until the accountant confirms it (Fixed assets > Tax blocks). Running it again adds only what is
missing and changes nothing already there.
"""

import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

OLD_ACT = "Income-tax Act 1961 s.32(1)(ii); Income-tax Rules 1962 rule 5, Appendix I Part A (unconfirmed)"
NEW_ACT = "Income-tax Act 2025 s.33; Income-tax Rules 2026 (rates as reported carried over; unconfirmed)"
OLD_FROM, NEW_FROM = datetime.date(2017, 4, 1), datetime.date(2026, 4, 1)

# (code, name, percent): Appendix I Part A, the classes this plant is likely to hold.
BLOCKS = (
    ("BLD-RES", "Buildings used mainly for residential purposes", "5"),
    ("BLD-OTH", "Buildings other than residential (factory, office)", "10"),
    ("BLD-TEMP", "Purely temporary erections such as wooden structures", "40"),
    ("FURN", "Furniture and fittings, including electrical fittings", "10"),
    ("PM-GEN", "Plant and machinery, general rate", "15"),
    ("PM-CAR", "Motor cars, other than those used in a business of running them on hire", "15"),
    ("PM-COMP", "Computers, including computer software", "40"),
    ("PM-40", "Plant and machinery at 40% (energy saving, pollution control and the like)", "40"),
    ("INTANG", "Know-how, patents, copyrights, trademarks, licences, franchises", "25"),
)


def load():
    from apps.assets.tax import TaxBlock, TaxBlockRate

    added = 0
    with transaction.atomic():
        for code, name, percent in BLOCKS:
            block, _ = TaxBlock.objects.get_or_create(code=code, defaults={"name": name})
            for applies_from, authority in ((OLD_FROM, OLD_ACT), (NEW_FROM, NEW_ACT)):
                _, made = TaxBlockRate.objects.get_or_create(
                    section=block, applies_from=applies_from,
                    defaults={"rate_percent": Decimal(percent), "authority": authority})
                added += made
    return added


class Command(BaseCommand):
    help = "Load the Income-tax blocks of assets and their rates, unconfirmed, for the accountant to confirm."

    def handle(self, *args, **options):
        added = load()
        self.stdout.write(f"{added} rate(s) added, unconfirmed: the accountant confirms each under "
                          "Fixed assets > Tax blocks before a year is filed at it.")
