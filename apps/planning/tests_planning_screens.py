"""
The forecast, master schedule and transfer-route screens' server side,
asked as the people who use them. The planner writes all three; the
floor writes none. Each list names what a row is about, so a screen
does not ask again for every item and warehouse.
"""

from apps.inventory.models import Warehouse

from .forecast import Forecast
from .mps import MasterScheduleEntry
from .tests_base import TODAY
from .tests_screens_api import ScreensTestCase


class ForecastScreenTests(ScreensTestCase):
    body = {"starts_on": "2026-06-01", "ends_on": "2026-06-30", "quantity": "5000"}

    def test_the_floor_does_not_forecast(self):
        response = self.as_("Production Supervisor").post(
            "/api/planning/forecasts/", {**self.body, "item": self.fabric.pk, "warehouse": self.plant.pk},
            format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Forecast.objects.exists())

    def test_the_planner_forecasts_and_the_list_names_it(self):
        planner = self.as_("Production Planner")
        made = planner.post("/api/planning/forecasts/",
                            {**self.body, "item": self.fabric.pk, "warehouse": self.plant.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = planner.get("/api/planning/forecasts/", {"item": self.fabric.pk}).json()
        self.assertEqual((row["item_label"], row["warehouse_name"]),
                         ("FAB-10X10 · Woven fabric, 10x10", "Plant"))
        self.assertEqual(planner.get("/api/planning/forecasts/", {"from": "2026-07-01"}).json(), [])


class MasterScheduleScreenTests(ScreensTestCase):
    def test_a_week_that_is_not_a_monday_is_refused_in_words(self):
        response = self.as_("Production Planner").post("/api/planning/master-schedule/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "week_of": "2026-06-03",
            "quantity": "2000", "reason": "Monsoon build",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("is not a Monday", str(response.json()))
        self.assertFalse(MasterScheduleEntry.objects.exists())

    def test_the_planner_schedules_a_week_and_the_list_names_it(self):
        planner = self.as_("Production Planner")
        made = planner.post("/api/planning/master-schedule/", {
            "item": self.fabric.pk, "warehouse": self.plant.pk, "week_of": str(TODAY),
            "quantity": "2000", "reason": "Monsoon build",
        }, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = planner.get("/api/planning/master-schedule/", {"search": "monsoon"}).json()
        self.assertEqual((row["item_label"], row["warehouse_name"], row["week_of"]),
                         ("FAB-10X10 · Woven fabric, 10x10", "Plant", "2026-06-01"))


class TransferRouteScreenTests(ScreensTestCase):
    def test_a_route_names_both_ends(self):
        depot = Warehouse.objects.create(code="D", name="Depot")
        planner = self.as_("Production Planner")
        made = planner.post("/api/planning/transfer-routes/", {
            "from_warehouse": self.plant.pk, "to_warehouse": depot.pk, "lead_days": 2,
        }, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = planner.get("/api/planning/transfer-routes/", {"to_warehouse": depot.pk}).json()
        self.assertEqual((row["from_name"], row["to_name"], row["lead_days"]), ("Plant", "Depot", 2))
        self.assertEqual(self.as_("Station").post("/api/planning/transfer-routes/", {
            "from_warehouse": depot.pk, "to_warehouse": self.plant.pk, "lead_days": 2,
        }, format="json").status_code, 403)
