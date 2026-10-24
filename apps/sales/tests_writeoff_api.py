"""
Bad debt through the API, as the controller: a receivable written off
with a reason, and the write-off reversed when the money came after all.
The AR manager who chased the debt does neither.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .tests_base import SalesTestCase


class WriteOffApiTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.invoice = self.bill(self.make_order("10", "100"))

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_written_off_in_part_then_recovered(self):
        controller = self.as_("Controller")
        url = f"/api/sales/invoices/{self.invoice.pk}/write_off/"
        unexplained = controller.post(url, {"amount": "600"}, format="json")
        self.assertEqual(unexplained.status_code, 400)
        self.assertIn("Say why", unexplained.content.decode())
        too_much = controller.post(url, {"amount": "1200", "reason": "Gone"}, format="json")
        self.assertEqual(too_much.status_code, 400)
        self.assertIn("1000.00 outstanding", too_much.content.decode())

        done = controller.post(url, {"amount": "600", "reason": "Settled at 40 paise", "date": "2026-09-01"},
                               format="json")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["amount_due"], "400.00")
        self.assertEqual(self.balance(self.bad_debt), Decimal("600"))

        [row] = controller.get("/api/sales/invoice-write-offs/", {"invoice": self.invoice.pk}).json()
        self.assertEqual((row["amount"], row["reason"], row["is_recovered"]), ("600.00", "Settled at 40 paise", False))
        recovered = controller.post(f"/api/sales/invoice-write-offs/{row['id']}/recover/", {}, format="json")
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertTrue(recovered.json()["is_recovered"])
        self.assertEqual(self.balance(self.bad_debt), Decimal("0"))
        again = controller.post(f"/api/sales/invoice-write-offs/{row['id']}/recover/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been recovered", again.content.decode())

    def test_the_ar_manager_reads_and_does_not_write_off(self):
        self.invoice.write_off(reason="Gone")
        manager = self.as_("AR Manager")
        self.assertEqual(manager.post(f"/api/sales/invoices/{self.invoice.pk}/write_off/",
                                      {"reason": "Gone"}, format="json").status_code, 403)
        [row] = manager.get("/api/sales/invoice-write-offs/").json()
        self.assertEqual(manager.post(f"/api/sales/invoice-write-offs/{row['id']}/recover/", {},
                                      format="json").status_code, 403)
