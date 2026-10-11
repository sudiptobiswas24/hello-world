"""
The register against the ledger it summarises.

A draft capitalised from a bill was left off the register while its
12,000 stood on the plant account: the register said 0, the ledger
12,000. It is on the register from the bill's date until its
capitalisation is undone, as capitalised until it goes into service.

The lathe: 12,000 from a bill dated 1 January 2026, twelve months at 1,000.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import JournalLine

from .models import AssetStatus, asset_register
from .tests import CapitalisationFixture


class TheRegisterFootsToTheLedgerTests(CapitalisationFixture):
    def setUp(self):
        super().setUp()
        (self.lathe,) = self.bill_line("1", "12000").capitalise_as_asset(self.category)

    def plant_on(self, day):
        rows = JournalLine.objects.filter(account=self.plant, entry__posted=True, entry__date__lte=day)
        return sum((row.debit - row.credit for row in rows), Decimal("0"))

    def on_it(self, day):
        return sum((row["cost"] for row in asset_register(as_of=day)), Decimal("0"))

    def test_a_capitalised_draft_is_on_it_as_capitalised(self):
        response = self.as_role("Bookkeeper").get("/api/assets/assets/register/", {"as_of": "2026-01-31"})
        self.assertEqual(response.status_code, 200, response.content)
        (row,) = response.json()
        self.assertEqual(
            (row["state"], Decimal(row["cost"]), Decimal(row["accumulated"]), Decimal(row["net_book_value"]),
             Decimal(row["monthly_charge"]), self.plant_on(datetime.date(2026, 1, 31))),
            ("capitalised", Decimal("12000"), Decimal("0"), Decimal("12000"), Decimal("0"), Decimal("12000")))

    def test_one_undone_is_on_it_until_the_day_it_was_undone(self):
        self.lathe.uncapitalise(on_date=datetime.date(2026, 1, 5))
        self.assertEqual(
            [(self.on_it(day), self.plant_on(day)) for day in (datetime.date(2026, 1, 3), datetime.date(2026, 1, 5))],
            [(Decimal("12000.00"), Decimal("12000.00")), (Decimal("0"), Decimal("0"))])

    def test_in_service_from_march_it_is_capitalised_in_february(self):
        self.lathe.place_in_service(on_date=datetime.date(2026, 3, 1))
        self.lathe.depreciate(through=datetime.date(2026, 3, 31))
        (february,) = asset_register(as_of=datetime.date(2026, 2, 15))
        (march,) = asset_register(as_of=datetime.date(2026, 3, 31))
        self.assertEqual(
            ((february["state"], february["monthly_charge"]),
             (march["state"], march["accumulated"], march["monthly_charge"])),
            (("capitalised", Decimal("0")), (AssetStatus.IN_SERVICE, Decimal("1000.00"), Decimal("1000.00"))))
