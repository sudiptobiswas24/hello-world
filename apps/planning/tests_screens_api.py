"""
What the planning and production screens ask the server, asked as the
people who use them: the planner plans and firms, the floor releases,
the station login does neither. Refusals first.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.manufacturing.orders import WorkOrder, WorkOrderStatus

from .models import PlannedOrder, PlanningRun
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class ScreensTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def plan_as(self, role, **body):
        return self.as_(role).post("/api/planning/runs/plan/",
                                   {"warehouse": self.plant.pk, "planned_on": str(TODAY), **body}, format="json")


class PlanRefusalTests(ScreensTestCase):
    def test_a_horizon_that_is_not_a_number_is_refused_by_the_field(self):
        for typed in ["ninety", "1.5", "0", "-3"]:
            with self.subTest(typed=typed):
                response = self.plan_as("Production Planner", horizon_days=typed)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("horizon_days", response.json())
        self.assertEqual(PlanningRun.objects.count(), 0)

    def test_no_warehouse_is_said_so(self):
        response = self.as_("Production Planner").post("/api/planning/runs/plan/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("warehouse", str(response.json()))

    def test_the_floor_does_not_plan(self):
        for role in ["Production Supervisor", "Station"]:
            with self.subTest(role=role):
                self.assertEqual(self.plan_as(role).status_code, 403)
        self.assertEqual(PlanningRun.objects.count(), 0)

    def test_the_floor_does_not_firm_the_plan(self):
        self.sell(self.fabric, "1000", self.day(30))
        run_id = self.plan_as("Production Planner").json()["id"]
        order = PlannedOrder.objects.filter(run_id=run_id, item=self.fabric).get()
        response = self.as_("Production Supervisor").post(f"/api/planning/planned-orders/{order.pk}/firm/")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(WorkOrder.objects.exists())


class PlanAndFirmTests(ScreensTestCase):
    def test_the_planner_plans_reads_and_firms(self):
        self.sell(self.fabric, "1000", self.day(30))
        planner = self.as_("Production Planner")
        response = self.plan_as("Production Planner", horizon_days="60")
        self.assertEqual(response.status_code, 200, response.content)
        run = response.json()
        self.assertEqual(run["warehouse_code"], "P")

        listed = planner.get("/api/planning/planned-orders/", {"run": run["id"], "kind": "make",
                                                               "ordering": "needed_by"}).json()
        skus = {row["item_sku"] for row in listed}
        self.assertIn("FAB-10X10", skus)
        fabric = next(row for row in listed if row["item_sku"] == "FAB-10X10")
        self.assertEqual(fabric["item_name"], "Woven fabric, 10x10")

        response = planner.post(f"/api/planning/planned-orders/{fabric['id']}/firm/")
        self.assertEqual(response.status_code, 200, response.content)
        work_order = WorkOrder.objects.get(item=self.fabric)
        self.assertEqual(response.json()["work_order_number"], work_order.number)
        self.assertEqual(work_order.status, WorkOrderStatus.DRAFT)

        drafts = planner.get("/api/manufacturing/work-orders/", {"status": "draft", "search": "FAB"}).json()
        self.assertEqual([row["id"] for row in drafts], [work_order.pk])
        self.assertEqual((drafts[0]["item_sku"], drafts[0]["work_centre_code"]),
                         ("FAB-10X10", work_order.work_centre.code if work_order.work_centre_id else ""))
        self.assertNotIn("components", drafts[0])  # the list is the light one
        detail = planner.get(f"/api/manufacturing/work-orders/{work_order.pk}/").json()
        self.assertIn("components", detail)
        self.assertEqual(detail["item_sku"], "FAB-10X10")

    def test_the_floor_releases_what_was_firmed(self):
        # Tape on the shelf to cost the run from: release refuses a run it
        # would plan as though its tape were free.
        self.stock(self.tape, "2000")
        self.sell(self.fabric, "1000", self.day(30))
        order = self.orders()["FAB-10X10"].firm()
        response = self.as_("Production Supervisor").post(f"/api/manufacturing/work-orders/{order.pk}/release/")
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.RELEASED)
        self.assertEqual(self.as_("Station").get("/api/manufacturing/work-orders/").status_code, 403)


class ListsDoNotAskPerRowTests(ScreensTestCase):
    def count(self, client, url):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(client.get(url).status_code, 200)
        return len(queries)

    def test_work_orders_and_planned_orders(self):
        planner = self.as_("Production Planner")
        self.sell(self.fabric, "1000", self.day(30))
        self.orders()["FAB-10X10"].firm()
        urls = ["/api/manufacturing/work-orders/", "/api/planning/planned-orders/",
                "/api/planning/actions/", "/api/planning/runs/"]
        for url in urls:
            self.count(planner, url)
        before = {url: self.count(planner, url) for url in urls}
        for days in (40, 50, 60):
            self.sell(self.fabric, "500", self.day(days))
            self.orders()["FAB-10X10"].firm()
        self.assertEqual({url: self.count(planner, url) for url in urls}, before)
