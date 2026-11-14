"""
Issuing by scan (L9), as the Production Supervisor. Batch V1 of PP raffia
holds 200 kg (expires 01-10-2026), V2 500 kg (expires 01-01-2027); the issue
draws 300 kg: V1 takes 200 and V2 the other 100.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.inventory.models import Lot
from apps.inventory.tracking import TrackingMode

from .tests_orders import TODAY, RunTestCase


class IssueByScanTests(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        self.v1 = Lot.objects.create(item=self.virgin, code="V1", expires_on=TODAY.replace(year=2026, month=10, day=1))
        self.v2 = Lot.objects.create(item=self.virgin, code="V2", expires_on=TODAY.replace(year=2027, month=1, day=1))
        self.stock(self.virgin, "200", "100", lot=self.v1)
        self.stock(self.virgin, "500", "100", lot=self.v2)
        self.run_order = self.order()
        self.run_order.release(TODAY)
        self.document = self.issue(self.run_order, [(self.virgin, "300")])
        self.url = f"/api/manufacturing/material-issues/{self.document.pk}/scan/"
        self.supervisor = self.as_("Production Supervisor")

    def as_(self, role):
        user, _made = User.objects.get_or_create(username=role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_fefo_names_the_batch_to_take_first(self):
        response = self.supervisor.post(self.url, {"code": "V2"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("take batch V1 first (expires 01-10-2026)", response.content.decode())

    def test_the_first_batch_takes_what_it_has_and_the_next_the_rest(self):
        for code in ("V1", "V2"):
            response = self.supervisor.post(self.url, {"code": code}, format="json")
            self.assertEqual(response.status_code, 200, response.content)
        lines = sorted((line.lot.code, line.quantity) for line in self.document.lines.all())
        self.assertEqual(lines, [("V1", Decimal("200")), ("V2", Decimal("100"))])

    def test_a_batch_release_holds_is_refused_in_its_words(self):
        from apps.inventory import tracking

        def held(item, lot, warehouse=None, action="issue"):
            return "V1 failed its inspection." if lot and lot.code == "V1" else ""
        tracking.register_lot_hold(held)
        self.addCleanup(tracking.LOT_HOLDS.remove, held)
        response = self.supervisor.post(self.url, {"code": "V1"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("V1 failed its inspection.", response.content.decode())

    def test_a_scan_into_an_issue_posted_meanwhile_is_refused(self):
        type(self.document).objects.filter(pk=self.document.pk).update(posted=True)
        response = self.supervisor.post(self.url, {"code": "V1"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("was posted meanwhile", response.content.decode())

    def test_the_store_scans_into_a_run_as_it_issues_and_the_planner_does_not(self):
        # O431, decided: Stores issues material, so the store is offered the issue and scans into it.
        found = self.as_("Warehouse Staff").get("/api/inventory/scan/open/").json()
        self.assertIn("material_issue", [row["kind"] for row in found])
        response = self.as_("Warehouse Staff").post(self.url, {"code": "V1"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        refused = self.as_("Production Planner").post(self.url, {"code": "V1"}, format="json")
        self.assertIn(refused.status_code, (403, 404))
        found = self.as_("Production Planner").get("/api/inventory/scan/open/").json()
        self.assertNotIn("material_issue", [row["kind"] for row in found])
        offered = self.supervisor.get("/api/inventory/scan/open/").json()
        self.assertIn(("material_issue", self.document.pk, ["issue"]),
                      [(row["kind"], row["id"], row["actions"]) for row in offered])

    def test_a_scanned_batch_is_taken_off_before_posting_and_its_quantity_comes_back(self):
        line = self.supervisor.post(self.url, {"code": "V1"}, format="json").json()["line"]
        response = self.supervisor.post(f"/api/manufacturing/material-issues/{self.document.pk}/unpick/",
                                        {"line": line["id"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([(row.lot, row.quantity) for row in self.document.lines.all()], [(None, Decimal("300"))])
