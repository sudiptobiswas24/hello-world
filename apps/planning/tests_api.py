"""
The door to planning.

Reachability, and refusals arriving as answers rather than crashes.
The netting has its own tests.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import PlanningSettings
from .tests_base import TODAY, PlantTestCase


class PlanningApiTests(PlantTestCase):
    def setUp(self):
        super().setUp()
        PlanningSettings.objects.create(
            horizon_days=90, default_buy_lead_days=7, default_make_lead_days=2,
            requisition_requester=self.buyer,
        )
        user = get_user_model().objects.create_superuser(
            username="planner", email="p@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_every_collection_answers(self):
        for path in ("settings", "runs", "planned-orders", "planned-demands",
                     "actions", "levels"):
            response = self.client.get(f"/api/planning/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_levels_report_the_explosion_order(self):
        response = self.client.get("/api/planning/levels/")
        levels = {row["sku"]: row["level"] for row in response.data["levels"]}
        self.assertEqual(levels["FAB-10X10"], 0)
        self.assertEqual(levels["TAPE-1000"], 1)
        self.assertEqual(levels["PP-RAFFIA"], 2)
        self.assertEqual(response.data["cuts"], [])

    def test_planning_is_a_post_and_returns_the_run(self):
        self.sell(self.fabric, "1000", self.day(30))
        response = self.client.post("/api/planning/runs/plan/", {
            "warehouse": self.plant.pk, "planned_on": str(TODAY),
        })
        self.assertEqual(response.status_code, 200)
        run = response.data
        orders = self.client.get(f"/api/planning/runs/{run['id']}/orders/")
        skus = {row["item"] for row in orders.data}
        self.assertEqual(len(skus), 4)
        self.assertTrue(all(row["explanation"] for row in orders.data))

    def test_a_run_reports_what_to_move_as_well_as_what_to_raise(self):
        from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
        from decimal import Decimal

        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg,
            quantity=Decimal("1000"), unit_price=Decimal("90"),
            expected_date=self.day(60),
        )
        order.confirm()
        self.sell(self.fabric, "1000", self.day(10))
        response = self.client.post("/api/planning/runs/plan/", {
            "warehouse": self.plant.pk, "planned_on": str(TODAY),
        })
        self.assertEqual(response.data["expedites"], 1)
        actions = self.client.get(
            f"/api/planning/runs/{response.data['id']}/actions/"
        ).data
        self.assertEqual(len(actions), 1)
        self.assertIn("pull in", actions[0]["sentence"])

    def test_planning_without_a_warehouse_is_refused_as_an_answer(self):
        response = self.client.post("/api/planning/runs/plan/", {})
        self.assertEqual(response.status_code, 400)

    def test_planning_a_quarantine_warehouse_answers_with_the_reason(self):
        from apps.inventory.models import Warehouse

        hold = Warehouse.objects.create(code="Q", name="Hold", is_quarantine=True)
        response = self.client.post("/api/planning/runs/plan/", {
            "warehouse": hold.pk, "planned_on": str(TODAY),
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("nobody may pick from", str(response.data))

    def test_firming_through_the_api(self):
        self.sell(self.fabric, "1000", self.day(30))
        response = self.client.post("/api/planning/runs/plan/", {
            "warehouse": self.plant.pk, "planned_on": str(TODAY),
        })
        orders = self.client.get(
            f"/api/planning/runs/{response.data['id']}/orders/"
        ).data
        fabric = [row for row in orders if row["item"] == self.fabric.pk][0]
        firmed = self.client.post(
            f"/api/planning/planned-orders/{fabric['id']}/firm/"
        )
        self.assertEqual(firmed.status_code, 200)
        self.assertEqual(firmed.data["status"], "firmed")
        self.assertIsNotNone(firmed.data["work_order"])

    def test_firming_twice_answers_rather_than_crashes(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.client.post("/api/planning/runs/plan/", {
            "warehouse": self.plant.pk, "planned_on": str(TODAY),
        }).data
        orders = self.client.get(f"/api/planning/runs/{run['id']}/orders/").data
        first = orders[0]["id"]
        self.client.post(f"/api/planning/planned-orders/{first}/firm/")
        again = self.client.post(f"/api/planning/planned-orders/{first}/firm/")
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been firmed", str(again.data))

    def test_cancelling_through_the_api(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.client.post("/api/planning/runs/plan/", {
            "warehouse": self.plant.pk, "planned_on": str(TODAY),
        }).data
        orders = self.client.get(f"/api/planning/runs/{run['id']}/orders/").data
        response = self.client.post(
            f"/api/planning/planned-orders/{orders[0]['id']}/cancel/"
        )
        self.assertEqual(response.data["status"], "cancelled")
