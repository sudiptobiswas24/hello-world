"""
The plant's own GST registration, from the office (seen on a first installation, 2 November): it had no screen and
no API, only a flag to setup_indian_books, while Settings' "GST registrations" edits the parties'. An installer who
typed the plant's GSTIN there made a party's profile, and the go-live check still said no registration was active.
"""

from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.gst import GstSettings
from apps.accounting.models import FiscalPosition


class ThePlantsGstRegistrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        call_command("setup_indian_books", stdout=StringIO())

    def as_(self, role):
        user = User.objects.create_user(role.lower().replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_enters_it_and_the_state_is_read_off_the_gstin(self):
        from apps.gst.tests import gstin
        from apps.imports.management.commands.go_live_check import checks

        self.assertEqual({what: level for level, what, _detail in checks()}["GST"], "WARN")
        number = gstin("19AABCB1234C1Z")
        inter = FiscalPosition.objects.get(code="INTER")
        made = self.as_("Controller").post("/api/accounting/gst-settings/", {
            "gstin": number, "interstate_position": inter.pk, "state": "27"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["state"], made.json()["state_name"]), ("19", "West Bengal"))
        self.assertEqual(GstSettings.active().gstin, number)
        self.assertEqual({what: level for level, what, _detail in checks()}["GST"], "ok")

    def test_the_officer_reads_it_a_rep_does_not_and_there_is_one(self):
        from apps.gst.tests import gstin

        inter = FiscalPosition.objects.get(code="INTER")
        controller = self.as_("Controller")
        first = controller.post("/api/accounting/gst-settings/", {
            "gstin": gstin("19AABCB1234C1Z"), "interstate_position": inter.pk}, format="json")
        self.assertEqual(first.status_code, 201, first.content)
        url = f"/api/accounting/gst-settings/{first.json()['id']}/"
        officer = self.as_("GST Officer")
        self.assertEqual(officer.get(url).status_code, 200)
        self.assertEqual(officer.patch(url, {"b2cl_limit": "250000"}, format="json").status_code, 403)
        self.assertEqual(self.as_("Sales Rep").get(url).status_code, 403)
        second = controller.post("/api/accounting/gst-settings/", {
            "gstin": gstin("27AABCB1234C1Z"), "interstate_position": inter.pk}, format="json")
        self.assertEqual(second.status_code, 400, second.content)
        self.assertIn("one GST registration", str(second.json()))
        self.assertIn(controller.delete(url).status_code, (403, 405))  # never deleted: made inactive instead
        self.assertTrue(GstSettings.objects.filter(pk=first.json()["id"]).exists())
        changed = controller.patch(url, {"b2cl_limit": "250000.00"}, format="json")
        self.assertEqual((changed.status_code, changed.json()["b2cl_limit"]), (200, "250000.00"))

    def test_what_the_screen_sends_is_taken_a_blank_limit_being_the_notified_one(self):
        """The screen sent null for each limit left blank, and its create was refused."""
        from apps.gst.tests import gstin

        made = self.as_("Controller").post("/api/accounting/gst-settings/", {
            "gstin": gstin("19AABCB1234C1Z"), "interstate_position": FiscalPosition.objects.get(code="INTER").pk,
            "is_active": True, "b2cl_limit": None, "einvoicing_from": None, "eway_bill_limit": None,
            "job_work_sac": ""}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["b2cl_limit"], made.json()["eway_bill_limit"], made.json()["is_active"]),
                         ("100000.00", "50000.00", True))

