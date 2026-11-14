"""
Goods held for inspection, decided through the API by the quality
inspector: passed to a shelf, or failed and sent back with a reason.

Both steps were built and reachable only from the admin, so goods put in
the inspection bay stayed there until somebody opened it.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import ReceiptInspection
from .tests_inspection import InspectionTestCase


class InspectionApiTests(InspectionTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_part_passed_to_the_shelf_and_the_rest_failed_back(self):
        _order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()
        inspector = self.as_("Quality Inspector")
        waiting = inspector.get(f"/api/purchasing/goods-receipts/{receipt.pk}/inspection/").json()["waiting"]
        self.assertEqual([(row["line"], row["quantity"]) for row in waiting], [(line.pk, "10.0000")])

        passed = inspector.post(f"/api/purchasing/goods-receipts/{receipt.pk}/accept/", {
            "warehouse": self.warehouse.pk, "quantities": {str(line.pk): "6"}}, format="json")
        self.assertEqual(passed.status_code, 200, passed.content)
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("6"))

        unexplained = inspector.post(f"/api/purchasing/goods-receipts/{receipt.pk}/reject/", {
            "quantities": {str(line.pk): "4"}}, format="json")
        self.assertEqual(unexplained.status_code, 400)
        self.assertIn("Say why they failed", unexplained.content.decode())
        failed = inspector.post(f"/api/purchasing/goods-receipts/{receipt.pk}/reject/", {
            "quantities": {str(line.pk): "4"}, "note": "Melt flow out of range"}, format="json")
        self.assertEqual(failed.status_code, 201, failed.content)
        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("0"))
        decided = inspector.get(f"/api/purchasing/goods-receipts/{receipt.pk}/inspection/").json()
        self.assertEqual(decided["waiting"], [])
        self.assertEqual([(row["quantity"], row["accepted"]) for row in decided["decided"]],
                         [("6.0000", True), ("4.0000", False)])

    def test_no_more_is_decided_than_waits(self):
        _order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()
        refused = self.as_("Quality Inspector").post(f"/api/purchasing/goods-receipts/{receipt.pk}/accept/", {
            "warehouse": self.warehouse.pk, "quantities": {str(line.pk): "11"}}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertFalse(ReceiptInspection.objects.exists())

    def test_the_stores_clerk_does_not_decide(self):
        _order, receipt = self.inspected_receipt("10", "5")
        refused = self.as_("Warehouse Staff").post(f"/api/purchasing/goods-receipts/{receipt.pk}/accept/", {
            "warehouse": self.warehouse.pk}, format="json")
        self.assertEqual(refused.status_code, 403)
