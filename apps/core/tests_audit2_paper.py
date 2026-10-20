"""audit2_core probe: the printed paper takes the plant's own words, not markup."""
import datetime
import subprocess
import tempfile
from decimal import Decimal

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_documents import SalesDocumentTestCase


class PrintedInvoiceTextTests(SalesDocumentTestCase):
    def _invoice(self, customer_name, description):
        customer = Party.objects.create(code="C-9", name=customer_name)
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        invoice = Invoice.objects.create(customer=customer, invoice_date=datetime.date(2026, 3, 1),
                                         receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, description=description,
                                   quantity=Decimal("10"), unit_price=Decimal("10"), revenue_account=self.revenue)
        invoice.post()
        return invoice

    def _text(self, invoice):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(invoice.render_pdf())
            handle.flush()
            return subprocess.run(["pdftotext", "-layout", handle.name, "-"], capture_output=True, text=True).stdout

    def test_what_the_paper_says_is_what_was_typed(self):
        invoice = self._invoice("Sharma <Raipur> & Sons", "HDPE sack <50 kg> 20x30")
        text = self._text(invoice)
        self.assertIn("Sharma <Raipur> & Sons", text)
        self.assertIn("HDPE sack <50 kg> 20x30", text)

    def test_a_customer_called_ab_polymers_and_a_size_in_angle_brackets_still_print(self):
        # Plant words: "Sharma <Raipur> & Sons", "HDPE sack <50 kg> 20x30".
        invoice = self._invoice("Sharma <Raipur> & Sons", "HDPE sack <50 kg> 20x30")
        self.assertTrue(invoice.render_pdf().startswith(b"%PDF-"))

    def test_a_description_with_a_lone_less_than_sign_prints(self):
        invoice = self._invoice("Sharma and Sons", "Tape < 2 mm wide")
        self.assertTrue(invoice.render_pdf().startswith(b"%PDF-"))


class PostedInvoiceExtraTests(SalesDocumentTestCase):
    def test_a_posted_invoice_keeps_its_custom_fields_as_posted(self):
        """Custom fields 'move no number', but a posted paper is immutable and its history says only 'Changed extra'."""
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from apps.core.customfields import CustomField

        CustomField.objects.create(kind="sales.invoice", key="gate_pass", label="Gate pass")
        invoice = self.make_invoice()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("boss", "b@example.com", "x"))
        answer = client.patch(f"/api/sales/invoices/{invoice.pk}/", {"extra": {"gate_pass": "GP-77"}}, format="json")
        print("POSTED EXTRA", answer.status_code)
        self.assertEqual(answer.status_code, 400)
