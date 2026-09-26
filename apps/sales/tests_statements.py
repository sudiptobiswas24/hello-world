"""
Customer statements.

Dunning chases one invoice at a time. A customer with forty open items
wants one document that adds up, and the property worth testing is
exactly that: the running balance has to foot to what they owe, under
every settlement path the system supports.
"""

import datetime
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.core.models import Currency, Party, PartyRole, PartyRoleAssignment

from .models import customer_statement, email_statement, outstanding_balance, send_statements
from .tests_base import SalesTestCase


class StatementBalanceTests(SalesTestCase):
    """Every path that clears a receivable must show up, or it won't foot."""

    def assert_foots(self, as_of=datetime.date(2026, 12, 31)):
        statement = customer_statement(self.customer, as_of=as_of)
        self.assertEqual(statement["closing_balance"], outstanding_balance(self.customer))
        return statement

    def test_a_plain_invoice(self):
        self.bill(self.make_order("10", "100"))
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("1000"))
        self.assertEqual(len(statement["entries"]), 1)

    def test_an_invoice_and_a_payment(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("400")), invoice, Decimal("400"))
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("600"))
        self.assertEqual([entry.kind for entry in statement["entries"]],
                         ["Invoice", "Payment"])

    def test_a_credit_note(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.create_credit_note()
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("0"))
        self.assertIn("Credit note", [entry.kind for entry in statement["entries"]])

    def test_a_write_off(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(on_date=datetime.date(2026, 9, 1), reason="Liquidated")
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("0"))
        self.assertIn("Written off", [entry.kind for entry in statement["entries"]])

    def test_a_recovered_write_off_puts_the_debt_back(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(on_date=datetime.date(2026, 9, 1))
        invoice.recover_write_off(invoice.write_offs.get(), on_date=datetime.date(2026, 10, 1))
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("1000"))
        self.assertIn("Write-off reversed", [entry.kind for entry in statement["entries"]])

    def test_a_settlement_discount(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("980"), on=datetime.date(2026, 3, 8)),
                      invoice, Decimal("980"))
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        statement = self.assert_foots()
        self.assertEqual(statement["closing_balance"], Decimal("0"))
        self.assertIn("Settlement discount", [entry.kind for entry in statement["entries"]])

    def test_a_down_payment_and_its_drawdown(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, percent=30).post()
        self.bill(order)
        statement = self.assert_foots()
        kinds = [entry.kind for entry in statement["entries"]]
        self.assertIn("Down payment", kinds)
        self.assertIn("Deposit applied", kinds)
        self.assertEqual(statement["closing_balance"], Decimal("1000"))

    def test_everything_at_once(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, percent=30).post()
        invoice = self.bill(order)
        self.allocate(self.receipt(Decimal("200"), on=datetime.date(2026, 5, 1)),
                      invoice, Decimal("200"))
        invoice.write_off(Decimal("100"), on_date=datetime.date(2026, 6, 1))
        self.assert_foots()


class StatementShapeTests(SalesTestCase):
    def test_the_running_balance_advances(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("400"), on=datetime.date(2026, 4, 1)),
                      invoice, Decimal("400"))
        entries = customer_statement(self.customer)["entries"]
        self.assertEqual([entry.balance for entry in entries],
                         [Decimal("1000"), Decimal("600")])

    def test_nothing_after_the_as_of_date_appears(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("400"), on=datetime.date(2026, 6, 1)),
                      invoice, Decimal("400"))

        statement = customer_statement(self.customer, as_of=datetime.date(2026, 5, 1))

        self.assertEqual(statement["closing_balance"], Decimal("1000"))
        self.assertEqual(len(statement["entries"]), 1)

    def test_a_period_view_keeps_an_opening_balance(self):
        """Truncating the list without carrying the balance forward would
        show a customer owing less than they do."""
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("400"), on=datetime.date(2026, 6, 1)),
                      invoice, Decimal("400"))

        statement = customer_statement(self.customer, since=datetime.date(2026, 5, 1))

        self.assertEqual(statement["opening_balance"], Decimal("1000"))
        self.assertEqual(len(statement["entries"]), 1)
        self.assertEqual(statement["closing_balance"], Decimal("600"))

    def test_it_reports_how_much_is_overdue(self):
        self.bill(self.make_order("10", "100"))
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 9, 1))
        self.assertEqual(statement["overdue"], Decimal("1000"))

    def test_a_customer_with_nothing_gets_an_empty_statement(self):
        statement = customer_statement(self.customer)
        self.assertEqual(statement["entries"], [])
        self.assertEqual(statement["closing_balance"], Decimal("0"))

    def test_mixed_currencies_are_refused_not_summed(self):
        """Same stance as cross-currency settlement: 100 USD plus 100 EUR
        is not 200 of anything."""
        from apps.core.models import ExchangeRate

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.1"), valid_from=datetime.date(2026, 1, 1)
        )
        self.bill(self.make_order("10", "100"))
        order = self.make_order("1", "100")
        order.currency = eur
        order.save()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        invoice.currency = eur
        invoice.save()
        invoice.post()

        with self.assertRaisesMessage(ValidationError, "more than one currency"):
            customer_statement(self.customer)

        self.assertEqual(
            customer_statement(self.customer, currency=self.usd)["closing_balance"],
            Decimal("1000"),
        )


class StatementDeliveryTests(SalesTestCase):
    def test_it_renders_as_a_pdf(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt(Decimal("400")), invoice, Decimal("400"))
        from .documents import render_statement_pdf

        pdf = render_statement_pdf(customer_statement(self.customer))
        self.assertTrue(pdf.startswith(b"%PDF"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_it_emails_with_the_pdf_attached(self):
        self.bill(self.make_order("10", "100"))
        email_statement(self.customer)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ap@acme.example"])
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_a_run_skips_customers_with_nothing_owing(self):
        quiet = Party.objects.create(code="C-2", name="Quiet", default_currency=self.usd,
                                     email="quiet@example.com")
        PartyRoleAssignment.objects.create(party=quiet, role=PartyRole.CUSTOMER)
        self.bill(self.make_order("10", "100"))

        sent, skipped = send_statements()

        self.assertEqual([statement["customer"] for statement in sent], [self.customer])
        self.assertEqual(skipped, [])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_an_unreachable_customer_is_reported_not_swallowed(self):
        self.customer.email = ""
        self.customer.save()
        self.bill(self.make_order("10", "100"))

        sent, skipped = send_statements()

        self.assertEqual(sent, [])
        self.assertEqual(skipped, [self.customer])
        self.assertEqual(len(mail.outbox), 0)

    def test_a_preview_run_needs_no_addresses(self):
        self.customer.email = ""
        self.customer.save()
        self.bill(self.make_order("10", "100"))

        sent, skipped = send_statements(send=False)

        self.assertEqual(len(sent), 1)
        self.assertEqual(skipped, [])
