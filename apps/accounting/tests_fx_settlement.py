"""
Realised exchange differences on settlement.

A foreign invoice is booked at the rate on its own date and settled at
the rate on the payment's date. In its own currency it is square — 1,000
EUR owed, 1,000 EUR paid — but in base currency the two sides differ,
and the control account was left holding that difference forever.

It isn't an error to hide. The company genuinely received more or fewer
pounds than it expected when it booked the sale, because the rate moved
while the money was outstanding. That belongs in the P&L.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

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

from .models import Account, AccountType, JournalLine, Payment, PaymentDirection
from .settlement import settlement_difference


class FxSettlementTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.20"), valid_from=datetime.date(2026, 1, 1)
        )
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.uom)

        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.expense = acc("5000", "Purchases", AccountType.EXPENSE)
        self.gain = acc("4900", "FX Gain", AccountType.INCOME)
        self.loss = acc("5900", "FX Loss", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            fx_gain_account=self.gain, fx_loss_account=self.loss,
        )

        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.eur
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.eur
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def rate_moves_to(self, rate, on=datetime.date(2026, 2, 1)):
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal(rate), valid_from=on)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def euro_invoice(self, amount="1000"):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 1, 1), currency=self.eur
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal(amount), revenue_account=self.revenue,
        )
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 1))
        invoice.post()
        return invoice

    def euro_bill(self, amount="1000"):
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 1),
            payable_account=self.ap, currency=self.eur,
        )
        BillLine.objects.create(
            bill=bill, description="Parts", quantity=Decimal("1"),
            unit_price=Decimal(amount), expense_account=self.expense,
        )
        bill.post()
        return bill

    def euro_payment(self, direction, amount, on=datetime.date(2026, 2, 15)):
        party = self.customer if direction == PaymentDirection.RECEIPT else self.vendor
        counterpart = self.ar if direction == PaymentDirection.RECEIPT else self.ap
        payment = Payment.objects.create(
            party=party, direction=direction, payment_date=on,
            amount=Decimal(amount), currency=self.eur,
            bank_account=self.bank, counterpart_account=counterpart,
        )
        payment.post()
        return payment


class ArithmeticTests(TestCase):
    def test_equal_rates_leave_nothing(self):
        self.assertEqual(
            settlement_difference(Decimal("1000"), Decimal("1.2"), Decimal("1.2")),
            Decimal("0"),
        )

    def test_a_falling_rate_leaves_a_positive_difference(self):
        self.assertEqual(
            settlement_difference(Decimal("1000"), Decimal("1.2"), Decimal("1.1")),
            Decimal("100.00"),
        )

    def test_a_rising_rate_leaves_a_negative_one(self):
        self.assertEqual(
            settlement_difference(Decimal("1000"), Decimal("1.1"), Decimal("1.2")),
            Decimal("-100.00"),
        )


class ReceivableFxTests(FxSettlementTestCase):
    def settle(self, rate):
        from apps.sales.models import InvoicePayment

        invoice = self.euro_invoice("1000")
        self.rate_moves_to(rate)
        payment = self.euro_payment(PaymentDirection.RECEIPT, "1000")
        InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal("1000")
        )
        return invoice

    def test_collecting_at_a_worse_rate_is_a_loss(self):
        """Booked 1,200 of base currency, collected 1,100. The 100 is real:
        the rate moved while the money was outstanding."""
        self.settle("1.10")

        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.balance(self.loss), Decimal("100"))
        self.assertEqual(self.balance(self.gain), Decimal("0"))

    def test_collecting_at_a_better_rate_is_a_gain(self):
        self.settle("1.30")

        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.balance(self.gain), Decimal("-100"))
        self.assertEqual(self.balance(self.loss), Decimal("0"))

    def test_the_document_is_square_either_way(self):
        """The customer owed 1,000 EUR and paid 1,000 EUR. The rate is the
        company's problem, not theirs."""
        invoice = self.settle("1.10")
        self.assertEqual(invoice.amount_due(), Decimal("0"))

    def test_a_partial_payment_only_books_its_share(self):
        from apps.sales.models import InvoicePayment

        invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        payment = self.euro_payment(PaymentDirection.RECEIPT, "400")
        InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal("400")
        )

        self.assertEqual(self.balance(self.loss), Decimal("40"))
        self.assertEqual(self.balance(self.ar), Decimal("720"))  # 600 EUR at 1.20

    def test_a_base_currency_settlement_posts_nothing(self):
        from apps.sales.models import InvoicePayment, SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 1, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("1000"), revenue_account=self.revenue,
        )
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 1))
        invoice.post()
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 2, 15), amount=Decimal("1000"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        allocation = InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal("1000")
        )

        self.assertIsNone(allocation.fx_entry_id)
        self.assertEqual(self.balance(self.loss), Decimal("0"))


class PayableFxTests(FxSettlementTestCase):
    """A payable is the mirror: the same rate move that costs you on a
    receivable saves you on a bill."""

    def settle(self, rate):
        from apps.purchasing.models import BillPayment

        bill = self.euro_bill("1000")
        self.rate_moves_to(rate)
        payment = self.euro_payment(PaymentDirection.DISBURSEMENT, "1000")
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("1000"))
        return bill

    def test_paying_at_a_better_rate_is_a_gain(self):
        self.settle("1.10")

        self.assertEqual(self.balance(self.ap), Decimal("0"))
        self.assertEqual(self.balance(self.gain), Decimal("-100"))

    def test_paying_at_a_worse_rate_is_a_loss(self):
        self.settle("1.30")

        self.assertEqual(self.balance(self.ap), Decimal("0"))
        self.assertEqual(self.balance(self.loss), Decimal("100"))

    def test_the_bill_is_square_either_way(self):
        bill = self.settle("1.30")
        self.assertEqual(bill.amount_due(), Decimal("0"))


class FxCorrectionTests(FxSettlementTestCase):
    def test_re_sizing_an_allocation_restates_the_difference(self):
        """An allocation can be re-pointed or re-sized after the fact, and
        the difference it caused has to move with it."""
        from apps.sales.models import InvoicePayment

        invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        payment = self.euro_payment(PaymentDirection.RECEIPT, "1000")
        allocation = InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal("1000")
        )
        self.assertEqual(self.balance(self.loss), Decimal("100"))

        allocation.amount = Decimal("500")
        allocation.save()

        self.assertEqual(self.balance(self.loss), Decimal("50"))
        # The payment credits AR with its whole 1,000 EUR whatever is
        # allocated, so what is left on AR is the difference on the 500
        # still unallocated — unrealised until it is applied to something.
        self.assertEqual(self.balance(self.ar), Decimal("50"))

    def test_removing_an_allocation_releases_the_difference(self):
        from apps.sales.models import InvoicePayment

        invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        payment = self.euro_payment(PaymentDirection.RECEIPT, "1000")
        allocation = InvoicePayment.objects.create(
            invoice=invoice, payment=payment, amount=Decimal("1000")
        )

        allocation.delete()

        self.assertEqual(self.balance(self.loss), Decimal("0"))
        # Back to the whole payment sitting on account: 1,200 booked less
        # 1,100 collected, unrealised until it is allocated again.
        self.assertEqual(self.balance(self.ar), Decimal("100"))

    def test_a_missing_account_is_refused_not_absorbed(self):
        from apps.sales.models import InvoicePayment

        company = Company.get()
        company.fx_loss_account = None
        company.save()

        invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        payment = self.euro_payment(PaymentDirection.RECEIPT, "1000")

        with self.assertRaisesMessage(ValidationError, "no FX loss account"):
            InvoicePayment.objects.create(
                invoice=invoice, payment=payment, amount=Decimal("1000")
            )
