"""
What customers owe, how late, and their credit, in rupees (O416).

ar_aging and outstanding_balance added each invoice's amount due in its own
currency: a USD customer owing 12,000 read 12,000 beside rupees where the
receivable held 9,99,000.00. Each figure is now the document's due at the
rate it posted at, through accounting.balances, which purchasing asks too.
Worked in calc_fix_balances/o416.py:

  - USD 12,000 invoiced on 1 Mar at 83.25 is 9,99,000.00; 6,000 received on
    20 Mar at 84.00 clears 4,99,500.00 of it; the 6,000 left reads
    4,99,500.00, the receivable's balance;
  - against a 10,00,000 limit, 9,99,000.00 owed and a 2,000 rupee order is
    10,01,000.00: held for credit;
  - USD 10,000 at 83.25 restated at 84.12 on 31 Mar: 8,32,500.00 and the
    restatement's 8,700.00 beside it, 8,41,200.00 as the receivable holds
    that day; reversed on 1 Apr, 8,32,500.00 again.
A statement to the customer stays in their currency: it is their document.
"""

import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db.models import Sum
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalLine, Payment, PaymentDirection
from apps.core.models import Company, Currency, ExchangeRate

from .models import (
    CustomerProfile,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
    ar_aging,
    customer_statement,
    outstanding_balance,
)
from .tests_base import SalesTestCase

MAR = lambda day: datetime.date(2026, 3, day)


class BalancesInBaseCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.inr = self.usd
        self.inr.code, self.inr.name = "INR", "Indian Rupee"
        self.inr.save()
        self.dollar = Currency.objects.create(code="USD", name="US Dollar")
        ExchangeRate.objects.create(currency=self.dollar, rate=D("83.25"), valid_from=datetime.date(2026, 1, 1))
        ExchangeRate.objects.create(currency=self.dollar, rate=D("84.00"), valid_from=MAR(15))
        self.fx_gain = Account.objects.create(code="4900", name="Exchange gain", account_type=AccountType.INCOME)
        self.fx_loss = Account.objects.create(code="5900", name="Exchange loss", account_type=AccountType.EXPENSE)
        company = Company.get()
        company.fx_gain_account, company.fx_loss_account = self.fx_gain, self.fx_loss
        company.save()

    def order_in(self, currency, quantity, price="100", confirm=True):
        order = SalesOrder.objects.create(customer=self.customer, order_date=MAR(1), currency=currency)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D(quantity),
                                      unit_price=D(price), revenue_account=self.revenue)
        if confirm:
            order.confirm()
        return order

    def dollar_invoice(self, quantity):
        return self.bill(self.order_in(self.dollar, quantity), on=MAR(1))

    def receive(self, amount, invoice, on=MAR(20)):
        payment = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=on,
                                         amount=D(amount), currency=self.dollar, bank_account=self.bank,
                                         counterpart_account=self.ar)
        payment.post()
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=D(amount), date=on)
        return payment

    def held(self, by=None):
        lines = JournalLine.objects.filter(account=self.ar, entry__posted=True)
        if by is not None:
            lines = lines.filter(entry__date__lte=by)
        rows = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or D("0")) - (rows["credit"] or D("0"))

    def total(self, aging):
        return sum((bucket["total"] for bucket in aging.values()), D("0"))


class TheAgingReadsWhatTheReceivableHoldsTests(BalancesInBaseCase):
    def test_part_paid_the_rest_is_at_the_rate_it_was_booked(self):
        invoice = self.dollar_invoice("120")
        self.assertEqual(self.held(), D("999000.00"))
        self.receive("6000", invoice)
        aging = ar_aging(as_of=datetime.date(2026, 4, 1))  # due 31 Mar on net 30: a day late
        [row] = aging["1-30"]["invoices"]
        self.assertEqual((row["currency"], row["due_in_currency"], row["amount_due"]),
                         ("USD", D("6000.00"), D("499500.00")))
        self.assertEqual((self.total(aging), outstanding_balance(self.customer), self.held()),
                         (D("499500.00"), D("499500.00"), D("499500.00")))

    def test_the_statement_stays_in_the_customer_s_currency(self):
        invoice = self.dollar_invoice("120")
        self.receive("6000", invoice)
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 4, 1))
        self.assertEqual(statement["closing_balance"], D("6000.00"))

    def test_on_a_restatement_s_day_it_is_one_more_line_and_the_next_day_none(self):
        from apps.accounting.restatement import Restatement

        self.dollar_invoice("100")
        restatement = Restatement.make(MAR(31), rates={"USD": "84.12"})
        aging = ar_aging(as_of=MAR(31))
        [line] = aging["restated"]["invoices"]
        self.assertEqual((line["restatement"], line["amount_due"]), (restatement, D("8700.00")))
        self.assertEqual((self.total(aging), self.held(by=MAR(31))), (D("841200.00"), D("841200.00")))
        after = ar_aging(as_of=datetime.date(2026, 4, 1))
        self.assertNotIn("restated", after)
        self.assertEqual((self.total(after), self.held(by=datetime.date(2026, 4, 1))),
                         (D("832500.00"), D("832500.00")))

    def test_the_ar_manager_reads_both_figures_and_the_restatement(self):
        from apps.accounting.restatement import Restatement

        self.dollar_invoice("100")
        Restatement.make(MAR(31), rates={"USD": "84.12"})
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ar-manager")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        report = client.get("/api/sales/invoices/aging/", {"as_of": "2026-03-31"})
        self.assertEqual(report.status_code, 200, report.content)
        [row] = report.json()["current"]["invoices"]
        self.assertEqual((row["currency"], row["due_in_currency"], row["amount_due"]), ("USD", "10000.00", "832500.00"))
        [line] = report.json()["restated"]["invoices"]
        self.assertEqual((line["kind"], line["amount_due"], line["description"]),
                         ("restatement", "8700.00", "Restated at the closing rate of 31-03-2026 (1100 AR)"))


class CreditIsWeighedInBaseTests(BalancesInBaseCase):
    def test_a_dollar_invoice_open_holds_a_rupee_order_over_the_limit(self):
        CustomerProfile.objects.update_or_create(party=self.customer, defaults={"credit_limit": D("1000000")})
        self.dollar_invoice("120")
        self.assertEqual(outstanding_balance(self.customer), D("999000.00"))
        order = self.order_in(self.inr, "20", confirm=False)
        self.assertEqual(order.credit_limit_breach(), (D("1001000.00"), D("1000000")))
        self.assertTrue(any("credit" in reason.lower() for reason in order.approval_reasons()), order.approval_reasons())
