from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.gst import GstSettings
from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
)

from .tests_audit_fixes import AuditTestCase


class GstInvoiceTests(AuditTestCase):
    """The state line, carried through to what an invoice posts."""

    def setUp(self):
        super().setUp()

        def tax(code, rate, account_code):
            account = Account.objects.create(
                code=account_code, name=f"Output {code}", account_type=AccountType.LIABILITY
            )
            return Tax.objects.create(
                code=code, name=code, rate=Decimal(rate),
                collected_account=account, paid_account=account,
            )

        self.cgst = tax("CGST9", "9", "2201")
        self.sgst = tax("SGST9", "9", "2202")
        self.igst = tax("IGST18", "18", "2203")
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.cgst, target_tax=self.igst
        )
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.sgst, target_tax=None
        )
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)

    def posted(self, invoice):
        return {
            line.account.code: line.credit - line.debit
            for line in invoice.journal_entry.lines.all()
        }

    def test_inside_the_state_central_and_state_tax_each_to_its_own_account(self):
        PartyTaxProfile.objects.create(party=self.customer, gstin="27AABCD1234E2Z7")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])

        posted = self.posted(invoice)
        self.assertEqual(posted["2201"], Decimal("90.00"))
        self.assertEqual(posted["2202"], Decimal("90.00"))
        self.assertNotIn("2203", posted)
        self.assertEqual(posted["1100"], Decimal("-1180.00"))

    def test_across_a_state_line_integrated_tax_at_the_whole_rate(self):
        PartyTaxProfile.objects.create(party=self.customer, gstin="29AABCE5678F1ZD")
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst])

        posted = self.posted(invoice)
        self.assertEqual(posted["2203"], Decimal("180.00"))
        self.assertNotIn("2201", posted)
        self.assertNotIn("2202", posted)
        self.assertEqual(posted["1100"], Decimal("-1180.00"))

    def test_a_customer_nobody_has_placed_cannot_be_invoiced_tax(self):
        """Defaulting to the intra-state pair is the plausible wrong answer."""
        invoice = self.make_invoice("1000", taxes=[self.cgst, self.sgst], post=False)
        with self.assertRaisesMessage(ValidationError, "no tax profile"):
            invoice.tax_total()

    def test_an_untaxed_line_needs_no_place(self):
        invoice = self.make_invoice("1000", post=False)
        self.assertEqual(invoice.tax_total(), Decimal("0"))
