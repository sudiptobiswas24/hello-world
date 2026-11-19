"""
O603: every document's letterhead and foot print the GSTIN the tax invoice, the returns and the e-invoice name,
the plant's GST registration's. They printed the company's own tax ID: the demo plant, registered under GST and
with no tax ID typed on the company, issued purchase orders and challans with no GSTIN at all.
"""

from django.test import TestCase

from apps.core.documents import issuer, letterhead, printed_footer, printed_letterhead
from apps.core.models import Company

from .gst import GstSettings
from .models import FiscalPosition


class EveryDocumentPrintsTheRegistrationsGstinTests(TestCase):
    def setUp(self):
        self.company = Company.get()
        Company.objects.filter(pk=self.company.pk).update(tax_id="")
        self.company.refresh_from_db()

    def register(self, gstin="27AAECD4821K1ZL"):
        return GstSettings.objects.create(
            gstin=gstin, interstate_position=FiscalPosition.objects.create(code="INTER", name="Inter-state"))

    def test_the_letterhead_and_foot_print_the_registration_where_the_company_has_no_tax_id(self):
        self.register()
        self.assertEqual(issuer(self.company)["tax_id"], "27AAECD4821K1ZL")
        self.assertIn("GSTIN 27AAECD4821K1ZL", str(letterhead(self.company)))
        self.assertIn("Tax ID 27AAECD4821K1ZL", printed_footer(issuer(self.company)))

    def test_the_registration_wins_over_a_different_tax_id_typed_on_the_company(self):
        self.register()
        Company.objects.filter(pk=self.company.pk).update(tax_id="27AABCD1234E1Z8")
        self.company.refresh_from_db()
        self.assertEqual(issuer(self.company)["tax_id"], "27AAECD4821K1ZL")

    def test_a_plant_with_no_registration_prints_its_own_tax_number(self):
        Company.objects.filter(pk=self.company.pk).update(tax_id="DE123456789")
        self.company.refresh_from_db()
        self.assertEqual(issuer(self.company)["tax_id"], "DE123456789")
        # O606: printed as what it is, not as a GSTIN; a letterhead kept before the label was, reprints as it did.
        self.assertIn("Tax ID DE123456789", str(letterhead(self.company)))
        self.assertNotIn("GSTIN", str(letterhead(self.company)))
        kept_before = {key: value for key, value in issuer(self.company).items() if key != "tax_label"}
        self.assertIn("GSTIN DE123456789", str(printed_letterhead(kept_before)))

    def test_an_inactive_registration_is_not_printed(self):
        registration = self.register()
        GstSettings.objects.filter(pk=registration.pk).update(is_active=False)
        self.assertEqual(issuer(self.company)["tax_id"], "")
