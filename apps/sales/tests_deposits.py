"""
Down payments.

The accounting question underneath: taking money up front does not earn
it. Until the goods go out the company owes the customer either the
goods or the money back, which is a liability, not revenue.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    DepositApplication,
    InvoicePolicy,
    SettlementStatus,
    committed_balance,
    commission_report,
    outstanding_balance,
    revenue_report,
)
from .tests_base import SalesTestCase


class DownPaymentInvoiceTests(SalesTestCase):
    def test_it_credits_a_liability_not_revenue(self):
        """The goods are still in the warehouse. Nothing has been earned."""
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()

        self.assertEqual(deposit.total(), Decimal("300"))
        self.assertEqual(self.balance(self.deposits), Decimal("-300"))  # credit
        self.assertEqual(self.balance(self.revenue), Decimal("0"))
        self.assertEqual(self.balance(self.ar), Decimal("300"))

    def test_a_percent_and_an_amount_agree(self):
        order = self.make_order("10", "100")
        by_percent = order.create_down_payment_invoice(self.ar, percent=25)
        self.assertEqual(by_percent.total(), Decimal("250"))

    def test_it_needs_exactly_one_of_amount_or_percent(self):
        order = self.make_order("10", "100")
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_down_payment_invoice(self.ar, amount=Decimal("100"), percent=10)
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_down_payment_invoice(self.ar)

    def test_deposits_cannot_exceed_the_order(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, amount=Decimal("700")).post()
        with self.assertRaisesMessage(ValidationError, "exceed the order total"):
            order.create_down_payment_invoice(self.ar, amount=Decimal("400"))

    def test_a_draft_order_cannot_take_one(self):
        from .models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.create_down_payment_invoice(self.ar, percent=50)

    def test_it_needs_an_account_to_hold_the_money(self):
        from apps.core.models import Company

        company = Company.get()
        company.customer_deposit_account = None
        company.save()
        order = self.make_order("10", "100")
        with self.assertRaisesMessage(ValidationError, "no customer deposit account"):
            order.create_down_payment_invoice(self.ar, percent=30)

    def test_it_lets_a_bill_on_delivery_order_be_billed_early(self):
        """The whole point: DELIVERED refuses to invoice before shipping,
        and a deposit is how you legitimately take money anyway."""
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        with self.assertRaisesMessage(ValidationError, "ship the goods first"):
            order.create_invoice(self.ar)

        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.assertEqual(deposit.amount_due(), Decimal("300"))


class DepositDrawdownTests(SalesTestCase):
    def deposit_on(self, order, percent=30):
        deposit = order.create_down_payment_invoice(self.ar, percent=percent)
        deposit.post()
        return deposit

    def test_posting_the_real_invoice_draws_it_down(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("1000"))
        self.assertEqual(invoice.amount_deposited(), Decimal("300"))
        self.assertEqual(invoice.amount_due(), Decimal("700"))

    def test_the_ledger_ends_up_right(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        payment = self.receipt(Decimal("300"))
        self.allocate(payment, deposit, Decimal("300"))
        self.bill(order)

        # The liability is discharged, revenue is recognised once, and AR
        # carries only the balance still to collect.
        self.assertEqual(self.balance(self.deposits), Decimal("0"))
        self.assertEqual(self.balance(self.revenue), Decimal("-1000"))
        self.assertEqual(self.balance(self.ar), Decimal("700"))

    def test_the_total_still_says_what_was_sold(self):
        """Netting the deposit into the total would make every revenue
        report unpick the difference."""
        order = self.make_order("10", "100")
        self.deposit_on(order)
        invoice = self.bill(order)
        self.assertEqual(invoice.subtotal(), Decimal("1000"))

    def test_an_unpaid_deposit_still_draws_down(self):
        """Two open receivables that add up to what is owed is correct;
        blocking the final invoice on a slow payer is not."""
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        invoice = self.bill(order)

        self.assertEqual(deposit.amount_due(), Decimal("300"))
        self.assertEqual(invoice.amount_due(), Decimal("700"))
        self.assertEqual(outstanding_balance(self.customer), Decimal("1000"))

    def test_a_deposit_is_only_drawn_down_once(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        self.bill(order)
        self.assertEqual(deposit.deposit_unapplied(), Decimal("0"))

    def test_several_deposits_draw_down_in_order(self):
        order = self.make_order("10", "100")
        self.deposit_on(order, percent=20)
        self.deposit_on(order, percent=10)
        invoice = self.bill(order)
        self.assertEqual(invoice.deposit_applications.count(), 2)
        self.assertEqual(invoice.amount_due(), Decimal("700"))

    def test_a_deposit_larger_than_the_invoice_is_capped(self):
        """Partial billing: the deposit can only clear what has been billed."""
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.deposit_on(order, percent=50)
        self.ship(order, "2")
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("200"))
        self.assertEqual(invoice.amount_due(), Decimal("0"))
        self.assertEqual(order.deposits().get().deposit_unapplied(), Decimal("300"))

    def test_the_rest_of_the_deposit_survives_to_the_next_invoice(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.deposit_on(order, percent=50)
        self.ship(order, "2")
        self.bill(order)
        self.ship(order, "8")
        second = self.bill(order)

        self.assertEqual(second.total(), Decimal("800"))
        self.assertEqual(second.amount_deposited(), Decimal("300"))
        self.assertEqual(second.amount_due(), Decimal("500"))

    def test_the_drawdown_can_be_turned_off(self):
        order = self.make_order("10", "100")
        self.deposit_on(order)
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        invoice.post(apply_deposits=False)
        self.assertEqual(invoice.amount_due(), Decimal("1000"))


class DepositGuardTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.make_order("10", "100")
        self.deposit = self.order.create_down_payment_invoice(self.ar, percent=30)
        self.deposit.post()

    def test_a_draft_invoice_cannot_draw_down(self):
        invoice = self.order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        with self.assertRaisesMessage(ValidationError, "Only a posted invoice"):
            invoice.apply_deposit(self.deposit)

    def test_a_down_payment_cannot_draw_down_another(self):
        second = self.order.create_down_payment_invoice(self.ar, percent=10)
        second.post()
        with self.assertRaisesMessage(ValidationError, "cannot draw down a deposit"):
            second.apply_deposit(self.deposit)

    def test_only_a_down_payment_can_be_drawn_down(self):
        invoice = self.bill(self.order)
        with self.assertRaisesMessage(ValidationError, "Only a posted down-payment invoice"):
            invoice.apply_deposit(invoice)

    def test_it_cannot_cross_customers(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        other = Party.objects.create(code="C-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        self.customer = other
        invoice = self.bill(self.make_order("1", "500"))
        with self.assertRaisesMessage(ValidationError, "different customer"):
            invoice.apply_deposit(self.deposit)

    def test_it_cannot_be_over_applied(self):
        invoice = self.bill(self.order)  # auto-applies the 300
        with self.assertRaisesMessage(ValidationError, "nothing left to draw down"):
            invoice.apply_deposit(self.deposit)

    def test_a_down_payment_needs_an_order(self):
        invoice = self.bill(self.order)
        invoice.is_down_payment = True
        invoice.sales_order = None
        with self.assertRaisesMessage(ValidationError, "must be against a sales order"):
            invoice.clean()


class DepositReportingTests(SalesTestCase):
    def test_a_deposit_is_not_revenue(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, percent=30).post()

        rows = revenue_report()
        self.assertEqual(rows, [])

        self.bill(order)
        self.assertEqual(revenue_report()[0]["net"], Decimal("1000"))

    def test_a_deposit_earns_no_commission(self):
        from .models import CommissionBasis, CommissionPlan, SalesRep

        plan = CommissionPlan.objects.create(
            code="P1", name="Flat", percent=Decimal("10"), basis=CommissionBasis.INVOICED
        )
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        order.create_down_payment_invoice(self.ar, percent=30).post()

        self.assertEqual(commission_report(), [])

        self.bill(order)
        self.assertEqual(commission_report()[0]["commission"], Decimal("100.00"))

    def test_it_does_not_eat_the_credit_limit_twice(self):
        """Taking money up front must not consume the customer's limit as
        if it were extra exposure."""
        order = self.make_order("10", "100")
        self.assertEqual(committed_balance(self.customer), Decimal("1000"))

        order.create_down_payment_invoice(self.ar, percent=30).post()
        self.assertEqual(committed_balance(self.customer), Decimal("1000"))

    def test_a_fully_deposited_invoice_reads_as_paid(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, amount=Decimal("1000")).post()
        invoice = self.bill(order)
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PAID)
