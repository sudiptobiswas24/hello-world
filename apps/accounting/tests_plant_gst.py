"""
The plant's own GST registration, from the office (seen on a first installation, 2 November): it had no screen and
no API, only a flag to setup_indian_books, while Settings' "GST registrations" edits the parties'. An installer who
typed the plant's GSTIN there made a party's profile, and the go-live check still said no registration was active.
"""

from decimal import Decimal as D
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


    def test_it_is_not_taken_out_of_force_once_tax_is_recorded_under_it(self):
        """Out of force, a taxable invoice posted untaxed and no month's return compiled (found by probing)."""
        from unittest import mock

        from apps.gst.tests import gstin

        controller = self.as_("Controller")
        made = controller.post("/api/accounting/gst-settings/", {
            "gstin": gstin("19AABCB1234C1Z"), "interstate_position": FiscalPosition.objects.get(code="INTER").pk},
            format="json")
        url = f"/api/accounting/gst-settings/{made.json()['id']}/"
        with mock.patch("apps.accounting.gst._anything_recorded", return_value=True):
            refused = controller.patch(url, {"is_active": False}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("it stays in force", str(refused.json()))
        self.assertTrue(GstSettings.objects.get().is_active)
        # Before anything is recorded under it, a registration entered by mistake is taken out of force.
        self.assertEqual(controller.patch(url, {"is_active": False}, format="json").status_code, 200)

    def test_a_position_that_does_not_tax_across_a_state_line_is_refused(self):
        """The screen offers every position: EXPORT-LUT zero-rated every inter-state sale, INTRA charged CGST and SGST."""
        from apps.gst.tests import gstin

        controller = self.as_("Controller")
        for code, maps in (("EXPORT-LUT", "CGST9 to IGST0"), ("INTRA", "CGST9 to CGST9")):
            refused = controller.post("/api/accounting/gst-settings/", {
                "gstin": gstin("19AABCB1234C1Z"), "interstate_position": FiscalPosition.objects.get(code=code).pk},
                format="json")
            self.assertEqual(refused.status_code, 400, refused.content)
            self.assertIn(maps, str(refused.json()))
        self.assertFalse(GstSettings.objects.exists())
        made = controller.post("/api/accounting/gst-settings/", {
            "gstin": gstin("19AABCB1234C1Z"), "interstate_position": FiscalPosition.objects.get(code="INTER").pk},
            format="json")
        self.assertEqual(made.status_code, 201, made.content)


class ThePlantsPositionStaysRightWhenItsMappingsChangeTests(TestCase):
    """
    The guard on choosing the plant's inter-state position was undone by editing its mappings afterwards: a
    Controller deleting INTER's CGST 9 to IGST 18 row left every inter-state sale at 18 per cent charged CGST 9 and
    SGST 9 to the other state's buyer (found probing the guard's edit path, 2 November).
    """

    @classmethod
    def setUpTestData(cls):
        from apps.gst.tests import gstin

        call_command("setup_roles", verbosity=0)
        call_command("setup_indian_books", stdout=StringIO())
        cls.inter = FiscalPosition.objects.get(code="INTER")
        GstSettings.objects.create(gstin=gstin("19AABCB1234C1Z"), interstate_position=cls.inter)

    def mapping(self, position, source):
        from apps.accounting.models import FiscalPositionTaxMapping

        return FiscalPositionTaxMapping.objects.get(fiscal_position=position, source_tax__code=source)

    def test_a_mapping_removed_or_bent_is_refused_and_kept(self):
        from django.core.exceptions import ValidationError

        from apps.accounting.models import Tax

        cgst = self.mapping(self.inter, "CGST9")
        with self.assertRaisesMessage(ValidationError, "CGST9 to CGST9"):
            cgst.delete()
        self.assertEqual(self.mapping(self.inter, "CGST9").target_tax.code, "IGST18")

        cgst.target_tax = Tax.objects.get(code="IGST5")
        with self.assertRaisesMessage(ValidationError, "CGST9 to IGST5"):
            cgst.save()
        self.assertEqual(self.mapping(self.inter, "CGST9").target_tax.code, "IGST18")

        sgst = self.mapping(self.inter, "SGST9")
        sgst.target_tax = Tax.objects.get(code="IGST18")
        with self.assertRaisesMessage(ValidationError, "SGST9 to IGST18"):
            sgst.save()
        self.assertIsNone(self.mapping(self.inter, "SGST9").target_tax)

    def test_the_controller_is_answered_in_words_through_the_api(self):
        user = User.objects.create_user("controller")
        user.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(user)
        row = self.mapping(self.inter, "CGST9")
        removed = client.delete(f"/api/accounting/fiscal-position-tax-mappings/{row.pk}/")
        self.assertEqual(removed.status_code, 400, removed.content)
        self.assertIn("what the plant taxes a supply across a state line through", str(removed.json()))
        self.assertEqual(self.mapping(self.inter, "CGST9").target_tax.code, "IGST18")

    def test_a_new_rate_is_mapped_one_tax_at_a_time(self):
        """Its CGST and SGST cannot be mapped at once: the first mapping leaves the second still wrong, not worse."""
        from apps.accounting.gst import interstate_position_faults
        from apps.accounting.models import FiscalPositionTaxMapping, Tax

        nine = Tax.objects.get(code="CGST9")

        def tax(code, rate, head):
            return Tax.objects.create(code=code, name=code, rate=D(rate), gst_head=head,
                                      collected_account=nine.collected_account, paid_account=nine.paid_account)

        cgst, sgst, igst = tax("CGST14", "14", "cgst"), tax("SGST14", "14", "sgst"), tax("IGST28", "28", "igst")
        self.assertEqual(interstate_position_faults(self.inter), ["CGST14 to CGST14", "SGST14 to SGST14"])
        FiscalPositionTaxMapping.objects.create(fiscal_position=self.inter, source_tax=cgst, target_tax=igst)
        FiscalPositionTaxMapping.objects.create(fiscal_position=self.inter, source_tax=sgst, target_tax=None)
        self.assertEqual(interstate_position_faults(self.inter), [])

    def test_another_positions_mappings_are_its_own(self):
        self.mapping(FiscalPosition.objects.get(code="EXPORT-LUT"), "CGST9").delete()

    def test_a_rate_added_later_and_left_unmapped_fails_the_go_live_check(self):
        from apps.accounting.models import Tax
        from apps.imports.management.commands.go_live_check import checks

        def level():
            return {what: level for level, what, _detail in checks()}.get("GST: inter-state position")

        self.assertIsNone(level())
        nine = Tax.objects.get(code="CGST9")
        Tax.objects.create(code="CGST14", name="CGST14", rate=D("14"), gst_head="cgst",
                           collected_account=nine.collected_account, paid_account=nine.paid_account)
        self.assertEqual(level(), "FAIL")

    def test_a_supply_across_a_state_line_at_an_unmapped_rate_is_refused(self):
        from django.core.exceptions import ValidationError

        from apps.accounting.gst import gst_taxes
        from apps.accounting.models import PartyTaxProfile, Tax
        from apps.core.models import Party
        from apps.gst.tests import gstin

        nine = Tax.objects.get(code="CGST9")
        buyer = PartyTaxProfile.objects.create(party=Party.objects.create(code="C-PATNA", name="Patna Mills"),
                                               gstin=gstin("10AABCP5678D1Z"))
        sgst9 = Tax.objects.get(code="SGST9")
        self.assertEqual([t.code for t in gst_taxes(buyer, [nine, sgst9])], ["IGST18"])
        fourteen = Tax.objects.create(code="CGST14", name="CGST14", rate=D("14"), gst_head="cgst",
                                      collected_account=nine.collected_account, paid_account=nine.paid_account)
        with self.assertRaisesMessage(ValidationError, "does not map CGST14"):
            gst_taxes(buyer, [fourteen])
