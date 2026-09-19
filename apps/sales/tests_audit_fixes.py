"""
Fixes for defects found by auditing the running system after Sales was
'finished'. Each one was reachable through the normal API and silently
wrong rather than loud.
"""

import datetime
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Payment,
    PaymentDirection,
    Tax,
)
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item

from .models import (
    CustomerProfile,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    PriceList,
    PriceListItem,
    Quotation,
    QuotationLine,
    QuotationStatus,
    SalesOrder,
    SalesOrderLine,
    committed_balance,
)


class AuditTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", symbol="$", is_base=True)
        self.eur = Currency.objects.create(code="EUR", name="Euro", symbol="€")
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.10"), valid_from=datetime.date(2026, 1, 1)
        )
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(
            sku="WDG-1", name="Widget", uom=self.uom, sale_price=Decimal("10")
        )
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.tax_payable = Account.objects.create(
            code="2100", name="Tax Payable", account_type=AccountType.LIABILITY
        )
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            collected_account=self.tax_payable, paid_account=self.tax_payable,
        )
        self.zero_rated = Tax.objects.create(
            code="VAT0", name="Zero rated", rate=Decimal("0"),
            collected_account=self.tax_payable, paid_account=self.tax_payable,
        )
        Company.objects.create(name="Test Co", base_currency=self.usd)

        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd, email="ap@acme.example"
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def make_invoice(self, amount="100", currency=None, taxes=(), post=True):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=currency or self.usd,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal(amount), revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        if post:
            invoice.post()
        return invoice

    def make_payment(self, amount, currency=None, direction=PaymentDirection.RECEIPT):
        payment = Payment.objects.create(
            party=self.customer, direction=direction,
            payment_date=datetime.date(2026, 3, 5), amount=Decimal(amount),
            currency=currency or self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        return payment


class CrossCurrencySettlementTests(AuditTestCase):
    """A USD payment used to settle a EUR invoice at face value."""

    def test_a_payment_in_another_currency_is_refused(self):
        invoice = self.make_invoice("100", currency=self.eur)
        payment = self.make_payment("100", currency=self.usd)

        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(
                invoice=invoice, payment=payment, amount=Decimal("100")
            )
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))

    def test_matching_currencies_still_settle(self):
        invoice = self.make_invoice("100", currency=self.eur)
        payment = self.make_payment("100", currency=self.eur)
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))


class FiscalPositionAppliedTests(AuditTestCase):
    """Fiscal positions were configured but nothing applied them."""

    def setUp(self):
        super().setUp()
        export = FiscalPosition.objects.create(code="EXPORT", name="Export")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=export, source_tax=self.vat, target_tax=self.zero_rated
        )
        PartyTaxProfile.objects.create(party=self.customer, fiscal_position=export)

    def test_an_invoice_line_uses_the_mapped_tax(self):
        invoice = self.make_invoice("100", taxes=[self.vat], post=False)
        line = invoice.lines.get()

        self.assertEqual([tax.code for tax in line.effective_taxes()], ["VAT0"])
        self.assertEqual(invoice.tax_total(), Decimal("0.00"))

    def test_posting_charges_no_tax_to_a_zero_rated_customer(self):
        invoice = self.make_invoice("100", taxes=[self.vat])
        entry = invoice.journal_entry
        self.assertEqual(entry.lines.get(account=self.ar).debit, Decimal("100.00"))
        self.assertFalse(entry.lines.filter(account=self.tax_payable).exists())

    def test_an_exempt_customer_is_charged_nothing(self):
        PartyTaxProfile.objects.filter(party=self.customer).update(
            tax_exempt=True, exemption_reference="EX-1"
        )
        invoice = self.make_invoice("100", taxes=[self.vat], post=False)
        self.assertEqual(invoice.tax_total(), Decimal("0.00"))

    def test_a_customer_without_a_profile_pays_the_ordinary_tax(self):
        plain = Party.objects.create(code="C-2", name="Domestic", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=plain, role=PartyRole.CUSTOMER)
        invoice = Invoice.objects.create(
            customer=plain, invoice_date=datetime.date(2026, 3, 1),
            receivable_account=self.ar, currency=self.usd,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        line.taxes.set([self.vat])
        self.assertEqual(invoice.tax_total(), Decimal("20.00"))

    def test_order_lines_honour_the_fiscal_position_too(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        line.taxes.set([self.vat])
        self.assertEqual(order.tax_total(), Decimal("0.00"))


class CommittedCreditExposureTests(AuditTestCase):
    """The limit counted posted invoices only, so orders sailed past it."""

    def make_order(self, amount="400", confirm=True):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal(amount), revenue_account=self.revenue,
        )
        if confirm:
            order.confirm()
        return order

    def test_confirmed_orders_count_towards_exposure(self):
        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("500"))
        self.make_order("400")

        self.assertEqual(committed_balance(self.customer), Decimal("400.00"))
        with self.assertRaises(ValidationError):
            self.make_order("400")

    def test_invoicing_an_order_does_not_double_count_it(self):
        self.make_order("400")
        order = SalesOrder.objects.get(pk=SalesOrder.objects.latest("id").pk)
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()

        # Billed once: the invoice now carries it, the order no longer does.
        self.assertEqual(committed_balance(self.customer), Decimal("400.00"))

    def test_a_partly_invoiced_order_counts_both_halves_once(self):
        order = self.make_order("400")
        order.lines.update(quantity=Decimal("2"), unit_price=Decimal("200"))

        partial = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        partial.lines.update(quantity=Decimal("1"))
        partial.post()

        self.assertEqual(committed_balance(self.customer), Decimal("400.00"))

    def test_a_draft_order_does_not_consume_the_limit(self):
        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("500"))
        self.make_order("400", confirm=False)
        self.assertEqual(committed_balance(self.customer), Decimal("0"))


class PriceListCurrencyTests(AuditTestCase):
    """A EUR price list used to price a USD order at face value."""

    def test_a_list_in_another_currency_is_ignored(self):
        euro_list = PriceList.objects.create(code="EURLIST", name="Euro list", currency=self.eur)
        PriceListItem.objects.create(
            price_list=euro_list, item=self.item, unit_price=Decimal("5")
        )
        CustomerProfile.objects.create(party=self.customer, price_list=euro_list)

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("10"))  # fell back to the item price

    def test_a_list_in_the_right_currency_is_used(self):
        usd_list = PriceList.objects.create(code="USDLIST", name="USD list", currency=self.usd)
        PriceListItem.objects.create(price_list=usd_list, item=self.item, unit_price=Decimal("5"))
        CustomerProfile.objects.create(party=self.customer, price_list=usd_list)

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("5"))

    def test_a_currency_agnostic_list_still_applies(self):
        any_list = PriceList.objects.create(code="ANY", name="Any currency", currency=None)
        PriceListItem.objects.create(price_list=any_list, item=self.item, unit_price=Decimal("6"))
        CustomerProfile.objects.create(party=self.customer, price_list=any_list)

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("6"))


class AcceptedQuotationTests(AuditTestCase):
    """An accepted quote could still be edited, so it stopped matching its order."""

    def make_quotation(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal("5"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        return quotation

    def test_an_accepted_quote_cannot_be_edited(self):
        quotation = self.make_quotation()
        quotation.accept(order_date=datetime.date(2026, 3, 2))

        quotation.reference = "tampered"
        with self.assertRaises(ValidationError):
            quotation.save()

    def test_lines_on_an_accepted_quote_are_frozen(self):
        quotation = self.make_quotation()
        order = quotation.accept(order_date=datetime.date(2026, 3, 2))

        line = quotation.lines.get()
        line.quantity = Decimal("500")
        with self.assertRaises(ValidationError):
            line.save()

        quotation.refresh_from_db()
        self.assertEqual(quotation.total(), order.total())

    def test_lines_cannot_be_removed_after_acceptance(self):
        quotation = self.make_quotation()
        quotation.accept(order_date=datetime.date(2026, 3, 2))
        with self.assertRaises(ValidationError):
            quotation.lines.get().delete()

    def test_an_unaccepted_quote_is_still_editable(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        line = quotation.lines.get()
        line.quantity = Decimal("7")
        line.save()
        self.assertEqual(quotation.total(), Decimal("70.00"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class QuotationDocumentTests(AuditTestCase):
    """mark_sent() recorded a send without sending, and quotes had no PDF."""

    def make_quotation(self, valid_until=None, taxes=()):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
            valid_until=valid_until, currency=self.usd,
        )
        line = QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal("5"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        return quotation

    def test_a_quotation_renders_a_pdf(self):
        quotation = self.make_quotation(
            valid_until=datetime.date(2026, 3, 31), taxes=[self.vat]
        )
        pdf = quotation.render_pdf()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_emailing_sends_the_pdf_and_marks_it_sent(self):
        quotation = self.make_quotation()
        recipient = quotation.email_to_customer()

        self.assertEqual(recipient, "ap@acme.example")
        self.assertEqual(len(mail.outbox), 1)
        name, content, mimetype = mail.outbox[0].attachments[0]
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, QuotationStatus.SENT)
        self.assertIsNotNone(quotation.sent_at)
        self.assertTrue(quotation.number.startswith("QT-"))

    def test_an_empty_quote_cannot_be_emailed(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1)
        )
        with self.assertRaises(ValidationError):
            quotation.email_to_customer()
        self.assertEqual(len(mail.outbox), 0)

    def test_a_customer_with_no_email_fails_loudly(self):
        silent = Party.objects.create(code="C-9", name="Unreachable")
        PartyRoleAssignment.objects.create(party=silent, role=PartyRole.CUSTOMER)
        quotation = Quotation.objects.create(
            customer=silent, quotation_date=datetime.date(2026, 3, 1)
        )
        QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal("1"), unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        with self.assertRaises(ValidationError):
            quotation.email_to_customer()


class EmptyDocumentTests(AuditTestCase):
    def test_a_zero_value_invoice_says_so(self):
        invoice = self.make_invoice("0", post=False)
        with self.assertRaises(ValidationError) as caught:
            invoice.post()
        self.assertIn("no value", str(caught.exception))
