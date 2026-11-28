"""
A trading document says what currency its amounts are in when it is not the base (O611).

  INV in USD to Dubai, 1,000 sacks at 1.37 under the LUT: total 1,370.00, and it reads foreign_currency "USD".
  The same sacks inside Maharashtra, in rupees: foreign_currency empty, as before.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.accounting.serializers import NamesItsCurrencyMixin
from apps.core.models import Country
from apps.core.tests_second_factor import staff_session
from apps.gst.tests_einvoice import EInvoiceTestCase
from apps.sales.serializers import InvoiceSerializer


class ADocumentNamesItsForeignCurrencyTests(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        receivables = User.objects.create_user("ar")
        receivables.groups.add(Group.objects.get(name="AR Manager"))
        self.client = staff_session(APIClient(), receivables)

    def export(self):
        """An invoice to the one customer in Dubai: codes are unique, so the customer is made once."""
        if not hasattr(self, "abroad"):
            self.abroad = self.party("EXPORT", currency=self.usd, gst_registration="overseas", fiscal_position=self.lut)
            uae = Country.objects.get_or_create(code="AE", defaults={"name": "United Arab Emirates"})[0]
            self.address(self.abroad, "Jebel Ali Free Zone", "Dubai", "", "", country=uae)
        return self.invoice(self.abroad, [(self.sack, "1000", "1.37", "0", self.pair)], currency=self.usd)

    def read(self, invoice):
        response = self.client.get(f"/api/sales/invoices/{invoice.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["foreign_currency"], response.json()["total"]

    def test_an_invoice_in_dollars_says_so(self):
        self.assertEqual(self.read(self.export()), ("USD", "1370.00"))

    def test_an_invoice_in_rupees_names_none(self):
        invoice = self.invoice(self.mh, [(self.sack, "100", "10", "0", self.pair)])
        self.assertEqual(self.read(invoice), (None, "1180.00"))

    def test_the_list_reads_the_codes_once_not_once_a_row(self):
        invoices = [self.export(), self.invoice(self.mh, [(self.sack, "100", "10", "0", self.pair)]), self.export()]
        listed = InvoiceSerializer(invoices, many=True)
        with self.assertNumQueries(1):
            codes = [listed.child.get_foreign_currency(invoice) for invoice in invoices]
        self.assertEqual(codes, ["USD", None, "USD"])


class EveryTradingDocumentNamesItTests(SimpleTestCase):
    """The mirror: the field is the shared mixin's, so a bill has it as an invoice does."""

    def test_every_serializer_on_the_mixin_lists_it(self):
        import apps.purchasing.serializers  # noqa: F401  the purchase side's serializers, registered by import
        import apps.sales.serializers  # noqa: F401

        found = {cls.__name__: "foreign_currency" in cls().fields for cls in NamesItsCurrencyMixin.__subclasses__()
                 if cls.__module__.startswith("apps.")}
        self.assertEqual({name for name, has in found.items() if not has}, set())
        self.assertEqual({"InvoiceSerializer", "QuotationSerializer", "SalesOrderSerializer",
                          "BillSerializer", "PurchaseOrderSerializer"}, set(found))
