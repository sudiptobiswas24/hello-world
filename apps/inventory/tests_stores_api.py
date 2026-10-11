"""
The stores screens' server side, asked as the people who use it.

100 widgets on the shelf at 5.00. The store counts 96: the books said
100, a shortfall of 4, written off at 5.00 is 20.00 to shrinkage. The
person who counts may not post the difference; the Stores Manager may.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .tests_adjustments import AdjustmentTestCase


def as_role(role):
    user = User.objects.create_user(role.replace(" ", "-").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class CountingTests(AdjustmentTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.stock("100", "5")

    def test_the_store_counts_and_its_manager_posts(self):
        store = as_role("Warehouse Staff")
        response = store.post("/api/inventory/stock-counts/", {
            "count_date": "2026-03-01", "warehouse": self.warehouse.pk, "reason": self.recount.pk,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        count = response.json()["id"]
        line = store.post(f"/api/inventory/stock-counts/{count}/add/",
                          {"item": self.item.pk, "counted_quantity": "96"}, format="json")
        self.assertEqual(line.status_code, 200, line.content)
        self.assertEqual((line.json()["item_label"], line.json()["system_quantity"]),
                         ("W · Widget", "100.0000"))
        # Whoever counted does not post what was found.
        self.assertEqual(store.post(f"/api/inventory/stock-counts/{count}/post/", {},
                                    format="json").status_code, 403)
        posted = as_role("Stores Manager").post(f"/api/inventory/stock-counts/{count}/post/", {},
                                                 format="json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("20.00"))

    def test_the_list_says_where_and_why(self):
        sheet = as_role("Stores Manager").post("/api/inventory/stock-counts/", {
            "count_date": "2026-03-01", "warehouse": self.warehouse.pk, "reason": self.recount.pk,
        }, format="json").json()
        [row] = as_role("Warehouse Staff").get("/api/inventory/stock-counts/", {"posted": "false"}).json()
        self.assertEqual((row["id"], row["warehouse_name"], row["reason_name"], row["lines_count"]),
                         (sheet["id"], "Main", "Count variance", 0))

    def test_an_adjustment_is_written_by_the_manager_and_only_read_by_the_store(self):
        adjustment = self.adjust("-3", reason=self.shrink, post=False)
        store = as_role("Warehouse Staff")
        self.assertEqual(store.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {},
                                    format="json").status_code, 403)
        [row] = store.get("/api/inventory/stock-adjustments/").json()
        self.assertEqual((row["reason_name"], row["lines"][0]["item_label"]), ("Shrinkage", "W · Widget"))
        self.assertEqual(as_role("Stores Manager").post(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json").status_code, 200)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("97"))
