"""
Tests for the money side of Sales: taxes, discounts, multi-currency,
payment terms and document numbering.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
    TaxScope,
)
from apps.core.models import (
    Address,
    AddressType,
    Currency,
    DocumentSequence,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item

from .models import Invoice, InvoiceLine, OrderStatus, SalesOrder, SalesOrderLine


class SalesBillingTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)

        self.ar = Account.objects.create(
            code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
        )
        self.revenue = Account.objects.create(
            code="4000", name="Sales Revenue", account_type=AccountType.INCOME
        )
        self.tax_payable = Account.objects.create(
            code="2100", name="Sales Tax Payable", account_type=AccountType.LIABILITY
        )
        self.tax_input = Account.objects.create(
            code="1300", name="Input Tax", account_type=AccountType.ASSET
        )

        self.net30 = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd, payment_terms=self.net30
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            collected_account=self.tax_payable, paid_account=self.tax_input,
        )

    def make_invoice(self, quantity="10", price="10", discount="0", taxes=(), **kwargs):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date=kwargs.pop("invoice_date", datetime.date(2026, 3, 1)),
            receivable_account=self.ar,
            **kwargs,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="Widgets",
            quantity=Decimal(quantity), unit_price=Decimal(price),
            discount_percent=Decimal(discount), revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        return invoice


class LineArithmeticTests(SalesBillingTestCase):
    def test_discount_reduces_the_net_amount(self):
        invoice = self.make_invoice(quantity="10", price="10", discount="10")
        line = invoice.lines.first()
        self.assertEqual(line.gross_amount(), Decimal("100.00"))
        self.assertEqual(line.discount_amount(), Decimal("10.00"))
        self.assertEqual(line.net_amount(), Decimal("90.00"))

    def test_tax_is_charged_on_the_discounted_amount(self):
        invoice = self.make_invoice(quantity="10", price="10", discount="10", taxes=[self.vat])
        line = invoice.lines.first()
        self.assertEqual(line.tax_total(), Decimal("18.00"))  # 20% of 90, not of 100
        self.assertEqual(line.total(), Decimal("108.00"))

    def test_document_totals_sum_the_lines(self):
        invoice = self.make_invoice(quantity="10", price="10", taxes=[self.vat])
        InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="More",
            quantity=Decimal("1"), unit_price=Decimal("50"), revenue_account=self.revenue,
        )
        self.assertEqual(invoice.subtotal(), Decimal("150.00"))
        self.assertEqual(invoice.tax_total(), Decimal("20.00"))
        self.assertEqual(invoice.total(), Decimal("170.00"))

    def test_tax_breakdown_groups_across_lines(self):
        invoice = self.make_invoice(quantity="10", price="10", taxes=[self.vat])
        second = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="Second",
            quantity=Decimal("5"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        second.taxes.set([self.vat])
        self.assertEqual(invoice.tax_breakdown(), {self.vat: Decimal("30.00")})


class TaxPostingTests(SalesBillingTestCase):
    def test_posting_creates_separate_revenue_and_tax_lines(self):
        invoice = self.make_invoice(quantity="10", price="10", taxes=[self.vat])
        invoice.post()

        entry = invoice.journal_entry
        self.assertEqual(entry.total_debit(), entry.total_credit())
        self.assertEqual(entry.lines.get(account=self.ar).debit, Decimal("120.00"))
        self.assertEqual(entry.lines.get(account=self.revenue).credit, Decimal("100.00"))
        self.assertEqual(entry.lines.get(account=self.tax_payable).credit, Decimal("20.00"))

    def test_untaxed_invoice_posts_without_a_tax_line(self):
        invoice = self.make_invoice(quantity="10", price="10")
        invoice.post()
        entry = invoice.journal_entry
        self.assertEqual(entry.lines.count(), 2)
        self.assertEqual(entry.lines.get(account=self.ar).debit, Decimal("100.00"))

    def test_posting_rejects_a_purchase_only_tax(self):
        purchase_tax = Tax.objects.create(
            code="PT", name="Purchase only", rate=Decimal("5"),
            scope=TaxScope.PURCHASE, paid_account=self.tax_input,
        )
        invoice = self.make_invoice(taxes=[purchase_tax])
        with self.assertRaises(ValidationError):
            invoice.post()

    def test_customer_fiscal_position_zero_rates_the_invoice(self):
        """A tax swapped to zero-rated must produce no tax in the ledger."""
        zero = Tax.objects.create(
            code="VAT0", name="Zero rated", rate=Decimal("0"),
            collected_account=self.tax_payable, paid_account=self.tax_input,
        )
        export = FiscalPosition.objects.create(code="EXPORT", name="Export")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=export, source_tax=self.vat, target_tax=zero
        )
        profile = PartyTaxProfile.objects.create(party=self.customer, fiscal_position=export)

        applicable = profile.applicable_taxes([self.vat])
        invoice = self.make_invoice(quantity="10", price="10", taxes=applicable)
        invoice.post()

        self.assertEqual(invoice.tax_total(), Decimal("0.00"))
        self.assertEqual(invoice.journal_entry.lines.get(account=self.ar).debit, Decimal("100.00"))


class MultiCurrencyTests(SalesBillingTestCase):
    def setUp(self):
        super().setUp()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.10"), valid_from=datetime.date(2026, 1, 1)
        )

    def test_foreign_invoice_posts_to_the_ledger_in_base_currency(self):
        invoice = self.make_invoice(quantity="10", price="10", currency=self.eur)
        invoice.post()

        self.assertEqual(invoice.total(), Decimal("100.00"))  # document stays in EUR
        self.assertEqual(invoice.exchange_rate, Decimal("1.10"))
        self.assertEqual(invoice.journal_entry.lines.get(account=self.ar).debit, Decimal("110.00"))

    def test_rate_is_frozen_at_posting_time(self):
        invoice = self.make_invoice(quantity="10", price="10", currency=self.eur)
        invoice.post()
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("2.00"), valid_from=datetime.date(2026, 6, 1)
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.exchange_rate, Decimal("1.10"))

    def test_posting_without_a_rate_fails_loudly(self):
        gbp = Currency.objects.create(code="GBP", name="Pound")
        invoice = self.make_invoice(currency=gbp)
        with self.assertRaises(ValidationError):
            invoice.post()

    def test_ledger_stays_balanced_when_conversion_rounds(self):
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.0733"), valid_from=datetime.date(2026, 2, 1)
        )
        invoice = self.make_invoice(quantity="3", price="33.33", taxes=[self.vat], currency=self.eur)
        invoice.post()
        entry = invoice.journal_entry
        self.assertEqual(entry.total_debit(), entry.total_credit())


class PaymentTermsOnInvoiceTests(SalesBillingTestCase):
    def test_due_date_comes_from_the_customers_terms(self):
        invoice = self.make_invoice()
        invoice.post()
        self.assertEqual(invoice.payment_terms, self.net30)
        self.assertEqual(invoice.due_date, datetime.date(2026, 3, 31))

    def test_without_terms_the_invoice_is_due_immediately(self):
        self.customer.payment_terms = None
        self.customer.save()
        invoice = self.make_invoice()
        invoice.post()
        self.assertEqual(invoice.due_date, datetime.date(2026, 3, 1))


class InvoiceNumberingTests(SalesBillingTestCase):
    def test_posting_assigns_a_sequence_number(self):
        invoice = self.make_invoice()
        self.assertEqual(invoice.number, "")
        invoice.post()
        self.assertEqual(invoice.number, "INV-2026-00001")

    def test_numbers_are_sequential_and_unique(self):
        numbers = []
        for _ in range(3):
            invoice = self.make_invoice()
            invoice.post()
            numbers.append(invoice.number)
        self.assertEqual(numbers, ["INV-2026-00001", "INV-2026-00002", "INV-2026-00003"])

    def test_credit_notes_use_their_own_sequence(self):
        invoice = self.make_invoice()
        invoice.post()
        credit_note = invoice.create_credit_note()
        self.assertTrue(credit_note.number.startswith("CN-"))
        self.assertTrue(invoice.number.startswith("INV-"))

    def test_the_number_appears_on_the_journal_entry(self):
        invoice = self.make_invoice()
        invoice.post()
        self.assertEqual(invoice.journal_entry.reference, invoice.number)


class CustomerDefaultsTests(SalesBillingTestCase):
    def test_invoice_inherits_currency_terms_and_addresses(self):
        billing = Address.objects.create(
            party=self.customer, address_type=AddressType.BILLING,
            line1="1 Main St", city="Springfield", is_primary=True,
        )
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar,
        )
        self.assertEqual(invoice.currency, self.usd)
        self.assertEqual(invoice.payment_terms, self.net30)
        self.assertEqual(invoice.billing_address, billing)
        self.assertEqual(invoice.shipping_address, billing)  # falls back to billing

    def test_explicit_values_are_not_overwritten(self):
        other_terms = PaymentTerms.objects.create(code="NET7", name="Net 7", net_days=7)
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, payment_terms=other_terms,
        )
        self.assertEqual(invoice.payment_terms, other_terms)


class OrderToInvoiceTests(SalesBillingTestCase):
    def make_order(self, discount="0", taxes=()):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("10"),
            discount_percent=Decimal(discount), revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        return order

    def test_confirming_assigns_a_number(self):
        order = self.make_order()
        self.assertEqual(order.number, "")
        order.confirm()
        self.assertEqual(order.number, "SO-2026-00001")
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_cannot_confirm_an_empty_order(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        with self.assertRaises(ValidationError):
            order.confirm()

    def test_cannot_confirm_twice(self):
        order = self.make_order()
        order.confirm()
        with self.assertRaises(ValidationError):
            order.confirm()

    def test_order_totals_include_tax(self):
        order = self.make_order(discount="10", taxes=[self.vat])
        self.assertEqual(order.subtotal(), Decimal("90.00"))
        self.assertEqual(order.tax_total(), Decimal("18.00"))
        self.assertEqual(order.total(), Decimal("108.00"))

    def test_invoicing_an_order_carries_taxes_and_discounts(self):
        order = self.make_order(discount="10", taxes=[self.vat])
        order.confirm()

        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))

        self.assertEqual(invoice.sales_order, order)
        line = invoice.lines.get()
        self.assertEqual(line.discount_percent, Decimal("10.00"))
        self.assertEqual(list(line.taxes.all()), [self.vat])
        self.assertEqual(invoice.total(), Decimal("108.00"))

    def test_cannot_invoice_an_unconfirmed_order(self):
        order = self.make_order()
        with self.assertRaises(ValidationError):
            order.create_invoice(self.ar)

    def test_invoice_from_order_posts_correctly(self):
        order = self.make_order(taxes=[self.vat])
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()

        entry = invoice.journal_entry
        self.assertEqual(entry.total_debit(), entry.total_credit())
        self.assertEqual(entry.lines.get(account=self.ar).debit, Decimal("120.00"))
