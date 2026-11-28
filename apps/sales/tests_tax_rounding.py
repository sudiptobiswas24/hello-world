"""
Line-level versus document-level tax rounding.

Three lines of 33.33 at 20% give 20.01 rounded per line and 20.00
rounded per document. Which is correct is a jurisdiction's rule, not a
preference, and a penny is small enough to be invisible and persistent
enough to fail a VAT return's reconciliation.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType, Tax, TaxGroup
from apps.core.models import Company

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine, revenue_report
from .tests_base import SalesTestCase


class TaxRoundingTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        group = TaxGroup.objects.create(code="VAT", name="VAT")
        self.vat_account = Account.objects.create(
            code="2100", name="VAT Payable", account_type=AccountType.LIABILITY
        )
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
            collected_account=self.vat_account, paid_account=self.vat_account,
        )

    def set_rounding(self, mode):
        company = Company.get()
        company.tax_rounding = mode
        company.save()

    def invoice_of(self, *prices):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd,
        )
        for price in prices:
            line = InvoiceLine.objects.create(
                invoice=invoice, item=self.item, description="Widget",
                quantity=Decimal("1"), unit_price=Decimal(price),
                revenue_account=self.revenue,
            )
            line.taxes.set([self.vat])
        return invoice


class RoundingModeTests(TaxRoundingTestCase):
    """The canonical disagreement: 3 x 33.33 at 20%."""

    def test_per_line_rounding_gives_the_sum_of_rounded_lines(self):
        self.set_rounding("line")
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        self.assertEqual(invoice.tax_total(), Decimal("20.01"))
        self.assertEqual(invoice.total(), Decimal("120.00"))

    def test_per_document_rounding_taxes_the_total_once(self):
        self.set_rounding("document")
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        self.assertEqual(invoice.tax_total(), Decimal("20.00"))
        self.assertEqual(invoice.total(), Decimal("119.99"))

    def test_line_rounding_is_the_default(self):
        """Switching the rule must be a decision, not something that
        happens to an existing ledger on upgrade."""
        self.assertEqual(Company.get().tax_rounding, "line")

    def test_the_two_agree_when_nothing_rounds(self):
        for mode in ("line", "document"):
            self.set_rounding(mode)
            self.assertEqual(self.invoice_of("100", "50").tax_total(), Decimal("30.00"))


class RoundingFootsTests(TaxRoundingTestCase):
    """Whatever the mode, the lines must add up to the document."""

    def setUp(self):
        super().setUp()
        self.set_rounding("document")

    def test_the_lines_sum_to_the_document_tax(self):
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        per_line = sum(
            (amount for amounts in invoice.line_tax_amounts().values() for _, amount in amounts),
            Decimal("0"),
        )
        self.assertEqual(per_line, invoice.tax_total())

    def test_the_difference_lands_on_the_largest_line(self):
        invoice = self.invoice_of("10.01", "10.01", "79.98")
        amounts = invoice.line_tax_amounts()
        biggest = invoice.lines.order_by("-unit_price").first()
        self.assertEqual(sum(a for amounts_ in amounts.values() for _, a in amounts_),
                         invoice.tax_total())
        self.assertGreater(dict(amounts[biggest])[self.vat], Decimal("0"))

    def test_the_breakdown_matches_the_total(self):
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        self.assertEqual(sum(invoice.tax_breakdown().values()), invoice.tax_total())

    def test_the_ledger_matches_the_invoice(self):
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        invoice.post()
        self.assertEqual(self.balance(self.vat_account), -invoice.tax_total())
        self.assertEqual(self.balance(self.ar), invoice.total())

    def test_the_revenue_report_matches_the_invoice(self):
        """A report that reads per-line tax would drift by the penny the
        document rounding just moved."""
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        invoice.post()
        self.assertEqual(revenue_report()[0]["tax"], invoice.tax_total())

    def test_a_credit_note_gives_back_exactly_what_was_charged(self):
        invoice = self.invoice_of("33.33", "33.33", "33.33")
        invoice.post()
        invoice.create_credit_note()
        self.assertEqual(self.balance(self.vat_account), Decimal("0"))
        self.assertEqual(self.balance(self.ar), Decimal("0"))


class RoundingGroupingTests(TaxRoundingTestCase):
    """Lines are grouped by the exact tax set, since compound and
    price-included taxes depend on what else applies."""

    def setUp(self):
        super().setUp()
        self.set_rounding("document")
        group = TaxGroup.objects.create(code="ECO", name="Eco")
        self.eco = Tax.objects.create(
            code="ECO5", name="Eco 5%", rate=Decimal("5"), group=group, sequence=2,
            collected_account=self.vat_account, paid_account=self.vat_account,
        )

    def test_differently_taxed_lines_round_separately(self):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd,
        )
        vat_only = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="A", quantity=Decimal("1"),
            unit_price=Decimal("33.33"), revenue_account=self.revenue,
        )
        vat_only.taxes.set([self.vat])
        both = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="B", quantity=Decimal("1"),
            unit_price=Decimal("33.33"), revenue_account=self.revenue,
        )
        both.taxes.set([self.vat, self.eco])

        breakdown = invoice.tax_breakdown()

        self.assertEqual(breakdown[self.vat], Decimal("13.34"))
        self.assertEqual(breakdown[self.eco], Decimal("1.67"))
        self.assertEqual(sum(breakdown.values()), invoice.tax_total())

    def test_an_untaxed_line_is_left_alone(self):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd,
        )
        InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="Untaxed", quantity=Decimal("1"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        self.assertEqual(invoice.tax_total(), Decimal("0"))
        self.assertEqual(invoice.total(), Decimal("100"))


class RoundingAppliesEverywhereTests(TaxRoundingTestCase):
    def test_an_order_rounds_the_same_way_as_its_invoice(self):
        """A quoted total that doesn't match the invoice is a support
        call, however small the difference."""
        self.set_rounding("document")
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        for _ in range(3):
            line = SalesOrderLine.objects.create(
                order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                unit_price=Decimal("33.33"), revenue_account=self.revenue,
            )
            line.taxes.set([self.vat])
        order.confirm()

        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))

        self.assertEqual(order.total(), Decimal("119.99"))
        self.assertEqual(invoice.total(), order.total())
