"""
The door to quality.

Reachability, and refusals arriving as answers rather than crashes. The
domain behaviour has its own tests.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import Disposition, Evaluation
from .tests import TODAY, QualityTestCase


class QualityApiTests(QualityTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="qc", email="qc@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_every_collection_answers(self):
        for path in ("characteristics", "plans", "plan-lines", "inspections",
                     "readings"):
            response = self.client.get(f"/api/quality/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_a_plan_carries_its_lines(self):
        plan = self.plan()
        response = self.client.get(f"/api/quality/plans/{plan.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertEqual(
            Decimal(str(response.data["lines"][0]["upper_limit"])),
            Decimal("91.875000"),
        )

    def test_an_inspection_can_be_posted_and_voided(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        base = f"/api/quality/inspections/{inspection.pk}/"
        response = self.client.post(f"{base}post/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], "pass")
        self.assertEqual(response.data["lot_status"], "released")
        response = self.client.post(f"{base}void/", {"reason": "Wrong roll."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["lot_status"], "uninspected")

    def test_voiding_with_no_reason_is_a_sentence(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        response = self.client.post(
            f"/api/quality/inspections/{inspection.pk}/void/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nobody said why", str(response.data))

    def test_accepting_a_failure_is_a_sentence(self):
        inspection = self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.ACCEPT
        )
        response = self.client.post(
            f"/api/quality/inspections/{inspection.pk}/post/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot simply be accepted", str(response.data))

    def test_an_inspection_says_what_it_measured_against(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        response = self.client.get(
            f"/api/quality/inspections/{inspection.pk}/"
        )
        reading = response.data["readings"][0]
        self.assertEqual(
            Decimal(str(reading["upper_limit"])), Decimal("91.875000")
        )
        self.assertTrue(reading["passed"])

    def test_where_a_batch_stands_is_reachable(self):
        response = self.client.get(f"/api/quality/lot-status/{self.roll.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "uninspected")
        self.assertIsNone(response.data["inspection"])
        self.inspect(self.plan(), [87, 88, 89]).post()
        response = self.client.get(f"/api/quality/lot-status/{self.roll.pk}/")
        self.assertEqual(response.data["status"], "released")
        self.assertTrue(response.data["inspection"].startswith("QC-"))

    def test_a_batch_that_does_not_exist_is_a_sentence(self):
        response = self.client.get("/api/quality/lot-status/99999/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No batch with id", str(response.data))

    def test_a_concession_says_who_signed_it(self):
        inspection = self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.CONCESSION,
            decided_by=self.inspector, decision_note="Taken at a discount.",
        )
        inspection.post()
        response = self.client.get(
            f"/api/quality/inspections/{inspection.pk}/"
        )
        self.assertTrue(response.data["self_approved"])
