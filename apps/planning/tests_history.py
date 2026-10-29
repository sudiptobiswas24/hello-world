"""
Two like years of fabric shipments brought in as history give the same
seasonal forecast the same two years of posted deliveries give; a year
of history followed by a year of deliveries reads as one series. A
month the system shipped in itself is refused, a job-work month is not,
and a figure run in again replaces the first.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.imports.importer import run

from .history import ShipmentHistory, parse_month
from .statistical import _add_months, propose
from .tests_statistical import AS_OF, PATTERN, StatisticalTestCase

EXPECTED = [Decimal(q) for q in ("5000", "6000", "5000", "5000", "5000", "6000")]


class HistoryTestCase(StatisticalTestCase):
    def history(self, *factors, start=datetime.date(2024, 10, 1), pattern=PATTERN):
        # The same months and figures years() ships, as the old system's register.
        month = start
        for factor in factors:
            for _ in range(12):
                quantity = Decimal(pattern[month.month - 1]) * 100 * Decimal(str(factor))
                ShipmentHistory.objects.create(item=self.fabric, warehouse=self.plant, month=month, quantity=quantity)
                month = _add_months(month, 1)


class RefusedTests(HistoryTestCase):
    def figure(self, **extra):
        values = dict(item=self.fabric, warehouse=self.plant, month=datetime.date(2025, 4, 1), quantity=Decimal("5000"))
        values.update(extra)
        return ShipmentHistory.objects.create(**values)

    def test_a_month_is_its_first_day_and_has_ended(self):
        with self.assertRaisesMessage(ValidationError, "first day of the month"):
            self.figure(month=datetime.date(2025, 4, 15))
        with self.assertRaisesMessage(ValidationError, "a month that has ended"):
            self.figure(month=timezone.localdate().replace(day=1))
        with self.assertRaisesMessage(ValidationError, "nothing or more"):
            self.figure(quantity=Decimal("-1"))

    def test_not_a_month_the_system_shipped_in_itself(self):
        self.ship(datetime.date(2026, 3, 5), "100")
        with self.assertRaisesMessage(ValidationError, "the system counts that month itself"):
            self.figure(month=datetime.date(2026, 3, 1))
        # A job-work delivery is not a shipment the forecast counts, so the month is still history's.
        self.ship(datetime.date(2026, 2, 5), "100", job_work=True)
        self.assertEqual(self.figure(month=datetime.date(2026, 2, 1)).month, datetime.date(2026, 2, 1))

    def test_a_month_is_read_in_several_ways(self):
        self.assertEqual({parse_month(v) for v in ("2025-04", "2025-04-01", "04-2025", "04/2025")},
                         {datetime.date(2025, 4, 1)})
        self.assertIsNone(parse_month("April 2025"))


class ForecastFromHistoryTests(HistoryTestCase):
    def test_two_years_of_history_forecast_as_two_years_of_deliveries_do(self):
        self.history(1, 1)
        found = propose(self.fabric, self.plant, as_of=AS_OF)
        self.assertEqual((found["method"], found["history_months"], found["history_from"]),
                         ("seasonal", 24, datetime.date(2024, 10, 1)))
        self.assertEqual((found["indices"][11], found["indices"][7]), (Decimal("1.5000"), Decimal("0.5000")))
        self.assertEqual(self.quantities(found), EXPECTED)

    def test_a_year_of_history_and_a_year_of_deliveries_read_as_one_series(self):
        self.history(1)
        self.years(1, start=datetime.date(2025, 10, 1))
        found = propose(self.fabric, self.plant, as_of=AS_OF)
        self.assertEqual((found["method"], found["history_months"]), ("seasonal", 24))
        self.assertEqual(self.quantities(found), EXPECTED)


class ImportedTests(HistoryTestCase):
    def file(self, *rows):
        return "sku,warehouse,month,quantity,note\n" + "".join(f"{row}\n" for row in rows)

    def test_checked_whole_then_kept_and_run_again_replaces(self):
        text = self.file(f"{self.fabric.sku},{self.plant.code},2025-04,5000,old register",
                         f"NOPE,{self.plant.code},2025-05,100,")
        report = run("shipment_history", text)
        self.assertEqual([(row, column) for row, column, _ in report.errors], [(3, "sku")])
        self.assertFalse(ShipmentHistory.objects.exists())
        kept = run("shipment_history", self.file(f"{self.fabric.sku},{self.plant.code},2025-04,5000,old register"),
                   commit=True)
        self.assertEqual((kept.created, kept.committed), (1, True))
        again = run("shipment_history", self.file(f"{self.fabric.sku},{self.plant.code},2025-04-01,5200,corrected"),
                    commit=True)
        self.assertTrue(again.committed)
        figure = ShipmentHistory.objects.get()
        self.assertEqual((figure.quantity, figure.note), (Decimal("5200"), "corrected"))

    def test_what_a_row_must_say(self):
        report = run("shipment_history", self.file(f"{self.fabric.sku},NOWHERE,2025-04,5000,",
                                                   f"{self.fabric.sku},{self.plant.code},April,5000,",
                                                   f"{self.fabric.sku},{self.plant.code},2025-04,-5,"))
        self.assertEqual([(row, column) for row, column, _ in report.errors],
                         [(2, "warehouse"), (3, "month"), (4, "quantity")])


class HistoryApiTests(HistoryTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_planner_keeps_it_and_the_stores_do_not_see_it(self):
        planner = self.as_("Production Planner")
        made = planner.post("/api/planning/shipment-history/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "month": "2025-04-01", "quantity": "5000"},
            format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = planner.get("/api/planning/shipment-history/", {"item": self.fabric.pk}).json()
        self.assertEqual((row["item_sku"], row["month"], row["quantity"]), (self.fabric.sku, "2025-04-01", "5000.0000"))
        refused = planner.post("/api/planning/shipment-history/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "month": "2025-04-15", "quantity": "1"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("first day of the month", refused.content.decode())
        self.assertEqual(self.as_("Warehouse Staff").get("/api/planning/shipment-history/").status_code, 403)
