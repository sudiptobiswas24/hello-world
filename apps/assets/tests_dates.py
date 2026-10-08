"""
The days an asset's entries are dated.

A correction is dated on or after what it takes back, and nothing is
dated on a day still to come (apps.core.models.correction_date and
day_that_has_come). A lathe bought on 1 January 2026 was disposed of on
15 December 2025, and its capitalisation undone on 1 December: either way
the plant account stood at -12,000 over the year end. Depreciation run
to next June charged three months nobody had used the machine in yet.

The lathe: 12,000 from a bill dated 1 January 2026, twelve months at
1,000; the plant's day is 8 October 2026 unless a test says otherwise.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.accounting.models import AccountingPeriod, JournalLine

from .models import AssetCategory, AssetStatus, DepreciationMethod, FixedAsset
from .tests import CapitalisationFixture, the_plants_day

TODAY = datetime.date(2026, 10, 8)
TOMORROW = datetime.date(2026, 10, 9)


class DatedTestCase(CapitalisationFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(the_plants_day(TODAY))
        self.controller = self.as_role("Controller")
        (self.lathe,) = self.bill_line("1", "12000").capitalise_as_asset(self.category)

    def in_service(self):
        self.lathe.place_in_service(on_date=datetime.date(2026, 1, 1))
        return self.lathe

    def act(self, step, **data):
        return self.controller.post(f"/api/assets/assets/{self.lathe.pk}/{step}/", data, format="json")

    def plant_on(self, day):
        rows = JournalLine.objects.filter(account=self.plant, entry__posted=True, entry__date__lte=day)
        return sum((row.debit - row.credit for row in rows), Decimal("0"))


class NotBeforeWhatItTakesBackTests(DatedTestCase):
    def test_a_lathe_is_not_disposed_of_before_it_was_acquired(self):
        self.in_service()
        refused = self.act("dispose", on_date="2025-12-15")
        self.lathe.refresh_from_db()
        self.assertEqual((refused.status_code, self.lathe.status, self.plant_on(datetime.date(2025, 12, 31))),
                         (400, AssetStatus.IN_SERVICE, Decimal("0")))
        self.assertIn("it was acquired on 2026-01-01", str(refused.json()))

    def test_a_capitalisation_is_not_undone_before_it_was_made(self):
        refused = self.act("uncapitalise", on_date="2025-12-01")
        self.lathe.refresh_from_db()
        self.assertEqual((refused.status_code, self.lathe.status, self.plant_on(datetime.date(2025, 12, 31))),
                         (400, AssetStatus.DRAFT, Decimal("0")))
        self.assertIn("it was capitalised on 2026-01-01", str(refused.json()))

    def test_a_disposal_is_not_taken_back_before_it_was_made(self):
        self.in_service().dispose(on_date=datetime.date(2026, 8, 20))
        with self.assertRaisesMessage(ValidationError, "it was disposed of on 2026-08-20"):
            self.lathe.reinstate(on_date=datetime.date(2026, 8, 10))

    def test_the_day_it_was_acquired_is_a_day_it_can_go(self):
        self.in_service()
        self.assertEqual(self.act("dispose", on_date="2026-01-01").status_code, 200)

    def test_the_day_it_was_capitalised_is_a_day_it_can_be_undone(self):
        self.assertEqual(self.act("uncapitalise", on_date="2026-01-01").status_code, 200)


class NotOnADayToComeTests(DatedTestCase):
    def test_depreciation_is_not_charged_through_a_month_to_come(self):
        self.in_service()
        refused = self.act("depreciate", through="2027-06-30")
        self.assertEqual((refused.status_code, self.lathe.depreciation_entries.count()), (400, 0))
        self.assertIn("that day has not come", str(refused.json()))

    def test_nor_through_the_month_still_running(self):
        self.in_service()
        with self.assertRaisesMessage(ValidationError, "through 2026-10-31: that day has not come"):
            self.lathe.depreciate(through=datetime.date(2026, 10, 31))

    def test_run_to_today_it_charges_the_months_that_have_ended(self):
        made = self.in_service().depreciate()
        self.assertEqual((len(made), self.lathe.accumulated()), (9, Decimal("9000.00")))

    def test_the_month_end_run_is_not_made_through_a_day_to_come(self):
        refused = self.controller.post("/api/assets/assets/depreciate-all/", {"through": "2026-12-31"},
                                       format="json")
        self.assertEqual(refused.status_code, 400)

    def test_a_disposal_is_not_dated_on_a_day_to_come(self):
        self.in_service()
        self.assertEqual((self.act("dispose", on_date=str(TOMORROW)).status_code, self.lathe.depreciation_entries
                          .count()), (400, 0))

    def test_nor_is_an_undo(self):
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            self.lathe.uncapitalise(on_date=TOMORROW)

    def test_nor_is_a_reinstatement(self):
        self.in_service().dispose(on_date=datetime.date(2026, 8, 20))
        with self.assertRaisesMessage(ValidationError, "that day has not come"):
            self.lathe.reinstate(on_date=TOMORROW)


class AClosedMonthTests(DatedTestCase):
    """
    The first quarter closed with an old loom charged to March. The lathe,
    capitalised in January and commissioned since, put into service from
    15 January: every month-end run stopped on its January, the loom's
    April went uncharged, and the lathe could not be disposed of.
    """

    def setUp(self):
        super().setUp()
        self.loom = FixedAsset.objects.create(name="Old loom", category=self.category, cost=Decimal("12000"),
                                              acquisition_date=datetime.date(2026, 1, 1), life_months=12)
        self.loom.place_in_service(on_date=datetime.date(2026, 1, 1))
        self.loom.depreciate(through=datetime.date(2026, 3, 31))
        AccountingPeriod.objects.create(name="Q1 2026", start_date=datetime.date(2026, 1, 1),
                                        end_date=datetime.date(2026, 3, 31), closed=True)

    def test_a_machine_is_not_put_into_service_from_a_closed_month(self):
        refused = self.act("place-in-service", on_date="2026-01-15")
        self.lathe.refresh_from_db()
        self.assertEqual((refused.status_code, self.lathe.status, self.lathe.number), (400, AssetStatus.DRAFT, ""))
        self.assertIn("depreciation for January 2026 would fall in Q1 2026, which is closed", str(refused.json()))

    def test_from_an_open_month_it_is_and_the_run_charges_every_machine(self):
        self.assertEqual(self.act("place-in-service", on_date="2026-04-01").status_code, 200)
        run = self.controller.post("/api/assets/assets/depreciate-all/", {"through": "2026-04-30"}, format="json")
        self.assertEqual((run.status_code, len(run.json()["charged"]), self.loom.accumulated(),
                          self.lathe.accumulated()), (200, 2, Decimal("4000.00"), Decimal("1000.00")))
        self.assertEqual(self.act("dispose", on_date="2026-05-20").status_code, 200)
        self.assertEqual(self.balance(self.disposal), Decimal("11000.00"))

    def test_one_not_depreciated_goes_into_service_from_a_closed_month(self):
        land = AssetCategory.objects.create(code="LAND", name="Land", asset_account=self.plant,
                                            accumulated_account=self.accumulated, expense_account=self.depreciation,
                                            method=DepreciationMethod.NONE)
        yard = FixedAsset.objects.create(name="Yard", category=land, cost=Decimal("50000"), life_months=0,
                                         acquisition_date=datetime.date(2026, 1, 1))
        yard.place_in_service(on_date=datetime.date(2026, 1, 15))
        self.assertEqual(yard.status, AssetStatus.IN_SERVICE)

    def test_one_brought_in_at_go_live_whose_closed_months_were_the_old_systems(self):
        loom = FixedAsset.objects.create(name="Loom 7", category=self.category, cost=Decimal("12000"), life_months=12,
                                         acquisition_date=datetime.date(2026, 1, 1),
                                         depreciated_before=datetime.date(2026, 3, 31),
                                         opening_depreciation=Decimal("3000"))
        loom.place_in_service(on_date=datetime.date(2026, 1, 1))
        self.assertEqual([row.period_end for row in loom.depreciate(through=datetime.date(2026, 4, 30))],
                         [datetime.date(2026, 4, 30)])

    def test_a_month_closed_since_names_the_machine_holding_up_the_run(self):
        AccountingPeriod.objects.get(name="Q1 2026").reopen()
        self.lathe.place_in_service(on_date=datetime.date(2026, 1, 15))
        AccountingPeriod.objects.get(name="Q1 2026").close()
        run = self.controller.post("/api/assets/assets/depreciate-all/", {"through": "2026-04-30"}, format="json")
        self.assertEqual(run.status_code, 400)
        self.assertIn(f"{self.lathe.number} Machine's depreciation for January 2026 falls in Q1 2026", str(run.json()))

class TheAuditAsksItTests(SimpleTestCase):
    """`manage.py audit_invariants` reports a step that reverses on its given day without the rule."""

    def findings(self, edit=lambda text: text):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        sources["assets"] = {path: edit(text) if path.name == "models.py" else text
                             for path, text in sources["assets"].items()}
        return [detail for _, detail in Command().corrections_dated_without_the_rule(["assets"], sources)]

    def test_a_disposal_dated_without_the_rule_is_reported(self):
        (said,) = self.findings(lambda text: text.replace(
            'on_date = correction_date(on_date, self.acquisition_date, f"{self} is not disposed of on", '
            '"it was acquired")', "on_date = to_date(on_date) or timezone.localdate()"))
        self.assertIn("assets.FixedAsset.dispose", said)

    def test_the_asset_as_it_stands_is_not(self):
        self.assertEqual(self.findings(), [])
