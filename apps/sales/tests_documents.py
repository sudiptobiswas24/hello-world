"""Invoice PDF and email, dunning, revenue reporting, and quotations."""

import datetime
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.accounting.models import Account, AccountType, Tax
from apps.core.models import (
    Address,
    AddressType,
    Company,
    Contact,
    Country,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item

from .models import (
    CustomerProfile,
    DunningLevel,
    DunningNotice,
    Invoice,
    InvoiceLine,
    Quotation,
    QuotationLine,
    QuotationStatus,
    SalesOrder,
    SalesOrderLine,
    revenue_report,
    run_dunning,
)


class SalesDocumentTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", symbol="$", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(
            sku="WDG-1", name="Widget", uom=self.uom, sale_price=Decimal("10")
        )
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
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
        Company.objects.create(
            name="Test Co", legal_name="Test Co Ltd", tax_id="GB123",
            email="billing@testco.example", base_currency=self.usd,
        )

        self.net30 = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd,
            payment_terms=self.net30, email="ap@acme.example",
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        country = Country.objects.create(code="US", name="United States")
        Address.objects.create(
            party=self.customer, address_type=AddressType.BILLING, line1="1 Main St",
            city="Springfield", postal_code="12345", country=country, is_primary=True,
        )

    def make_invoice(self, quantity="10", price="10", taxes=(), post=True,
                     invoice_date=datetime.date(2026, 3, 1), customer=None):
        invoice = Invoice.objects.create(
            customer=customer or self.customer, invoice_date=invoice_date,
            receivable_account=self.ar,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="Widgets",
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        if post:
            invoice.post()
        return invoice


class InvoicePdfTests(SalesDocumentTestCase):
    def test_a_posted_invoice_renders_a_pdf(self):
        invoice = self.make_invoice(taxes=[self.vat])
        pdf = invoice.render_pdf()

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_a_draft_invoice_can_be_previewed(self):
        invoice = self.make_invoice(post=False)
        self.assertTrue(invoice.render_pdf().startswith(b"%PDF-"))

    def test_a_credit_note_renders_too(self):
        invoice = self.make_invoice()
        credit_note = invoice.create_credit_note()
        self.assertTrue(credit_note.render_pdf().startswith(b"%PDF-"))

    def test_an_invoice_with_no_addresses_still_renders(self):
        bare = Party.objects.create(code="C-2", name="No Address Co")
        PartyRoleAssignment.objects.create(party=bare, role=PartyRole.CUSTOMER)
        invoice = self.make_invoice(customer=bare)
        self.assertTrue(invoice.render_pdf().startswith(b"%PDF-"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvoiceEmailTests(SalesDocumentTestCase):
    def test_sending_attaches_the_pdf(self):
        invoice = self.make_invoice(taxes=[self.vat])
        recipient = invoice.email_to_customer()

        self.assertEqual(recipient, "ap@acme.example")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn(invoice.number, message.subject)
        self.assertEqual(message.to, ["ap@acme.example"])
        name, content, mimetype = message.attachments[0]
        self.assertEqual(name, f"{invoice.number}.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_sending_records_when_it_went(self):
        invoice = self.make_invoice()
        self.assertIsNone(invoice.sent_at)
        invoice.email_to_customer()
        invoice.refresh_from_db()
        self.assertIsNotNone(invoice.sent_at)

    def test_a_primary_contact_beats_the_party_address(self):
        Contact.objects.create(
            party=self.customer, first_name="Ada", email="ada@acme.example", is_primary=True
        )
        invoice = self.make_invoice()
        self.assertEqual(invoice.email_to_customer(), "ada@acme.example")

    def test_an_explicit_recipient_wins(self):
        invoice = self.make_invoice()
        self.assertEqual(
            invoice.email_to_customer(to="someone.else@acme.example"),
            "someone.else@acme.example",
        )

    def test_a_draft_invoice_cannot_be_sent(self):
        invoice = self.make_invoice(post=False)
        with self.assertRaises(ValidationError):
            invoice.email_to_customer()

    def test_a_customer_with_no_email_fails_loudly(self):
        silent = Party.objects.create(code="C-3", name="Unreachable")
        PartyRoleAssignment.objects.create(party=silent, role=PartyRole.CUSTOMER)
        invoice = self.make_invoice(customer=silent)
        with self.assertRaises(ValidationError):
            invoice.email_to_customer()
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DunningTests(SalesDocumentTestCase):
    def setUp(self):
        super().setUp()
        self.gentle = DunningLevel.objects.create(name="Gentle reminder", days_overdue=7)
        self.firm = DunningLevel.objects.create(name="Firm reminder", days_overdue=30)
        self.final = DunningLevel.objects.create(name="Final notice", days_overdue=60)

    def test_an_invoice_not_yet_due_is_left_alone(self):
        self.make_invoice()  # due 2026-03-31
        self.assertEqual(run_dunning(as_of=datetime.date(2026, 3, 15)), [])

    def test_the_first_level_fires_once_overdue(self):
        invoice = self.make_invoice()
        notices = run_dunning(as_of=datetime.date(2026, 4, 10))  # 10 days over

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].level, self.gentle)
        self.assertEqual(notices[0].invoice, invoice)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_same_level_is_never_sent_twice(self):
        self.make_invoice()
        run_dunning(as_of=datetime.date(2026, 4, 10))
        self.assertEqual(run_dunning(as_of=datetime.date(2026, 4, 12)), [])

    def test_it_escalates_as_the_debt_ages(self):
        self.make_invoice()
        run_dunning(as_of=datetime.date(2026, 4, 10))   # gentle
        second = run_dunning(as_of=datetime.date(2026, 5, 10))  # 40 days -> firm
        self.assertEqual(second[0].level, self.firm)

    def test_it_jumps_straight_to_the_level_reached(self):
        """A long-ignored invoice shouldn't be sent the earlier reminders too."""
        self.make_invoice()
        notices = run_dunning(as_of=datetime.date(2026, 7, 1))  # ~92 days over
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].level, self.final)

    def test_a_paid_invoice_is_not_chased(self):
        invoice = self.make_invoice()
        invoice.create_credit_note()  # settles it
        self.assertEqual(run_dunning(as_of=datetime.date(2026, 7, 1)), [])

    def test_the_notice_records_the_debt_at_the_time(self):
        self.make_invoice("10", "10")
        notice = run_dunning(as_of=datetime.date(2026, 4, 10))[0]
        self.assertEqual(notice.amount_due, Decimal("100.00"))
        self.assertEqual(notice.days_overdue, 10)
        self.assertEqual(notice.sent_to, "ap@acme.example")

    def test_the_message_is_rendered_from_the_level(self):
        self.make_invoice()
        run_dunning(as_of=datetime.date(2026, 4, 10))
        message = mail.outbox[0]
        self.assertIn("Acme", message.body)
        self.assertIn("10 days", message.body)

    def test_it_can_run_without_sending(self):
        self.make_invoice()
        notices = run_dunning(as_of=datetime.date(2026, 4, 10), send=False)
        self.assertEqual(len(notices), 1)
        self.assertIsNone(notices[0].sent_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_levels_configured_means_no_chasing(self):
        DunningLevel.objects.all().delete()
        self.make_invoice()
        self.assertEqual(run_dunning(as_of=datetime.date(2026, 7, 1)), [])


class RevenueReportTests(SalesDocumentTestCase):
    def test_revenue_grouped_by_customer(self):
        other = Party.objects.create(code="C-9", name="Globex", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)

        self.make_invoice("10", "10")                      # Acme 100
        self.make_invoice("5", "10", customer=other)       # Globex 50

        report = {row["key"]: row["net"] for row in revenue_report(group_by="customer")}
        self.assertEqual(report[str(self.customer)], Decimal("100.00"))
        self.assertEqual(report[str(other)], Decimal("50.00"))

    def test_credit_notes_reduce_revenue(self):
        invoice = self.make_invoice("10", "10")
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("4")})

        report = revenue_report(group_by="customer")
        self.assertEqual(report[0]["net"], Decimal("60.00"))

    def test_tax_is_reported_separately_from_net(self):
        self.make_invoice("10", "10", taxes=[self.vat])
        row = revenue_report(group_by="customer")[0]
        self.assertEqual(row["net"], Decimal("100.00"))
        self.assertEqual(row["tax"], Decimal("20.00"))
        self.assertEqual(row["gross"], Decimal("120.00"))

    def test_grouping_by_item(self):
        gadget = Item.objects.create(sku="GDG-1", name="Gadget", uom=self.uom)
        invoice = self.make_invoice("10", "10", post=False)
        InvoiceLine.objects.create(
            invoice=invoice, item=gadget, description="Gadgets",
            quantity=Decimal("2"), unit_price=Decimal("25"), revenue_account=self.revenue,
        )
        invoice.post()

        report = {row["key"]: row["net"] for row in revenue_report(group_by="item")}
        self.assertEqual(report[str(self.item)], Decimal("100.00"))
        self.assertEqual(report[str(gadget)], Decimal("50.00"))

    def test_grouping_by_month(self):
        self.make_invoice("10", "10", invoice_date=datetime.date(2026, 1, 15))
        self.make_invoice("20", "10", invoice_date=datetime.date(2026, 2, 15))

        report = {row["key"]: row["net"] for row in revenue_report(group_by="month")}
        self.assertEqual(report["2026-01"], Decimal("100.00"))
        self.assertEqual(report["2026-02"], Decimal("200.00"))

    def test_the_period_is_respected(self):
        self.make_invoice("10", "10", invoice_date=datetime.date(2026, 1, 15))
        self.make_invoice("20", "10", invoice_date=datetime.date(2026, 2, 15))

        report = revenue_report(
            date_from=datetime.date(2026, 2, 1), date_to=datetime.date(2026, 2, 28)
        )
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["net"], Decimal("200.00"))

    def test_drafts_are_excluded(self):
        self.make_invoice("10", "10", post=False)
        self.assertEqual(revenue_report(), [])

    def test_an_unknown_grouping_is_rejected(self):
        self.make_invoice()
        with self.assertRaises(ValueError):
            revenue_report(group_by="colour")


class QuotationTests(SalesDocumentTestCase):
    def make_quotation(self, quantity="10", price="10", valid_until=None, taxes=()):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
            valid_until=valid_until,
        )
        line = QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        if taxes:
            line.taxes.set(taxes)
        return quotation

    def test_a_quote_totals_like_an_order(self):
        quotation = self.make_quotation("10", "10", taxes=[self.vat])
        self.assertEqual(quotation.subtotal(), Decimal("100.00"))
        self.assertEqual(quotation.tax_total(), Decimal("20.00"))
        self.assertEqual(quotation.total(), Decimal("120.00"))

    def test_sending_assigns_a_number(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        self.assertEqual(quotation.number, "QT-2026-00001")
        self.assertEqual(quotation.status, QuotationStatus.SENT)

    def test_an_empty_quote_cannot_be_sent(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1)
        )
        with self.assertRaises(ValidationError):
            quotation.mark_sent()

    def test_accepting_creates_a_confirmed_order(self):
        quotation = self.make_quotation("10", "10", taxes=[self.vat])
        quotation.mark_sent()

        order = quotation.accept(order_date=datetime.date(2026, 3, 5))

        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.total(), Decimal("120.00"))
        self.assertEqual(list(order.lines.get().taxes.all()), [self.vat])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, QuotationStatus.ACCEPTED)
        self.assertEqual(quotation.sales_order, order)

    def test_the_quoted_price_carries_over_even_if_the_list_changed(self):
        quotation = self.make_quotation("10", "7.50")
        quotation.mark_sent()
        self.item.sale_price = Decimal("99")
        self.item.save()

        order = quotation.accept(order_date=datetime.date(2026, 3, 5))
        self.assertEqual(order.lines.get().unit_price, Decimal("7.50"))

    def test_an_expired_quote_cannot_be_accepted(self):
        quotation = self.make_quotation(valid_until=datetime.date(2026, 3, 10))
        quotation.mark_sent()

        with self.assertRaises(ValidationError):
            quotation.accept(order_date=datetime.date(2026, 4, 1))

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, QuotationStatus.EXPIRED)

    def test_a_quote_is_valid_up_to_its_last_day(self):
        quotation = self.make_quotation(valid_until=datetime.date(2026, 3, 10))
        quotation.mark_sent()
        order = quotation.accept(order_date=datetime.date(2026, 3, 10))
        self.assertEqual(order.status, "confirmed")

    def test_accepting_twice_is_refused(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        quotation.accept(order_date=datetime.date(2026, 3, 5))
        with self.assertRaises(ValidationError):
            quotation.accept(order_date=datetime.date(2026, 3, 6))

    def test_a_declined_quote_cannot_be_accepted(self):
        quotation = self.make_quotation()
        quotation.mark_sent()
        quotation.decline()
        with self.assertRaises(ValidationError):
            quotation.accept()

    def test_validity_cannot_precede_the_quote_date(self):
        quotation = Quotation(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
            valid_until=datetime.date(2026, 2, 1),
        )
        with self.assertRaises(ValidationError):
            quotation.full_clean()

    def test_a_quote_line_resolves_its_price_like_an_order_line(self):
        quotation = Quotation.objects.create(
            customer=self.customer, quotation_date=datetime.date(2026, 3, 1)
        )
        line = QuotationLine.objects.create(
            quotation=quotation, item=self.item, uom=self.uom,
            quantity=Decimal("3"), revenue_account=self.revenue,
        )
        self.assertEqual(line.unit_price, Decimal("10"))

    def test_accepting_respects_the_credit_limit(self):
        CustomerProfile.objects.create(party=self.customer, credit_limit=Decimal("50"))
        quotation = self.make_quotation("10", "10")
        quotation.mark_sent()
        with self.assertRaises(ValidationError):
            quotation.accept(order_date=datetime.date(2026, 3, 5))
