"""
A line reads back the taxes it is charged beside those chosen on it (O610).

  The item's own taxes are CGST9 and SGST9, chosen on every line here.
  Inside Maharashtra the line is charged them: CGST9, SGST9.
  To Karnataka the inter-state position maps CGST9 to IGST18 and SGST9 to nothing: IGST18.
  To Dubai under the LUT the position maps CGST9 to IGST0 and SGST9 to nothing: IGST0,
  a tax of 0.00, where the screen had shown CGST9, SGST9 beside it.
"""

from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.accounting.serializers import MoneyLineSerializerMixin
from apps.core.models import Country
from apps.core.tests_second_factor import staff_session
from apps.gst.tests_einvoice import EInvoiceTestCase


class ALineReadsBackWhatItIsChargedTests(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        # Who reads and changes a sale's lines without being held to their own customers.
        receivables = User.objects.create_user("ar")
        receivables.groups.add(Group.objects.get(name="AR Manager"))
        self.client = staff_session(APIClient(), receivables)

    def read(self, invoice):
        """The invoice's line as its screen reads it: nested in the invoice."""
        response = self.client.get(f"/api/sales/invoices/{invoice.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        (line,) = response.json()["lines"]
        return sorted(line["taxes"]), line["charged_taxes"], line["tax_total"]

    def chosen(self):
        return sorted([self.cgst.pk, self.sgst.pk])

    def test_inside_the_state_it_is_charged_what_was_chosen(self):
        invoice = self.invoice(self.mh, [(self.sack, "100", "10", "0", self.pair)])
        self.assertEqual(self.read(invoice), (self.chosen(), ["CGST9", "SGST9"], "180.00"))

    def test_across_a_state_line_it_is_charged_igst(self):
        invoice = self.invoice(self.ka, [(self.sack, "100", "10", "0", self.pair)])
        self.assertEqual(self.read(invoice), (self.chosen(), ["IGST18"], "180.00"))

    def test_an_export_under_the_lut_is_charged_igst_at_nothing(self):
        abroad = self.party("EXPORT", currency=self.usd, gst_registration="overseas", fiscal_position=self.lut)
        uae = Country.objects.get_or_create(code="AE", defaults={"name": "United Arab Emirates"})[0]
        self.address(abroad, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        invoice = self.invoice(abroad, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd)
        self.assertEqual(self.read(invoice), (self.chosen(), ["IGST0"], "0.00"))

    def test_a_draft_line_reads_it_too_and_sending_it_back_is_refused(self):
        invoice = self.invoice(self.ka, [(self.sack, "100", "10", "0", self.pair)], post=False)
        self.assertEqual(self.read(invoice)[1], ["IGST18"])
        line = invoice.lines.get()
        response = self.client.patch(f"/api/sales/invoice-lines/{line.pk}/",
                                     {"quantity": "5", "charged_taxes": ["CGST9"]}, format="json")
        self.assertEqual((response.status_code, list(response.json())), (400, ["charged_taxes"]), response.content)
        line.refresh_from_db()
        self.assertEqual(line.quantity, D("100"))


class EveryTradingLineReadsItBackTests(SimpleTestCase):
    """The mirror: the field is the shared mixin's, so a purchase line has it as a sale's does."""

    def test_every_serializer_on_the_mixin_lists_it(self):
        def every(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from every(sub)

        import apps.purchasing.serializers  # noqa: F401  the purchase side's serializers, registered by import
        import apps.sales.serializers  # noqa: F401

        found = {cls.__name__: "charged_taxes" in cls().fields for cls in every(MoneyLineSerializerMixin)
                 if cls.__module__.startswith("apps.") and ".tests" not in cls.__module__}
        self.assertEqual({name for name, has in found.items() if not has}, set())
        self.assertTrue({"InvoiceLineSerializer", "QuotationLineSerializer", "SalesOrderLineSerializer",
                         "BillLineSerializer", "PurchaseOrderLineSerializer"} <= set(found), found)

