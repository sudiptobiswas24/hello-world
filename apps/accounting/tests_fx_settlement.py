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
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone

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

        acc = lambda c, n, t, **more: Account.objects.create(code=c, name=n, account_type=t, **more)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET, holds_money=True)
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


class ReturnedForeignPaymentTests(FxSettlementTestCase):
    """
    1,000 EUR booked at 1.20 and settled at 1.25: 50 realised. The money
    comes back on the 20th of February: the 50 was never realised, and the
    receivable or payable stands at the 1,200 the document was booked at
    again. A later delete of the allocation releases nothing twice.
    """

    def test_a_returned_receipt_realised_nothing(self):
        from apps.sales.models import InvoicePayment

        invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.25")
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "1000")
        # Applied the day it came in, so realised then and released on the day it came back.
        allocation = InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("1000"),
                                                   date=datetime.date(2026, 2, 15))
        self.assertEqual(self.balance(self.gain), Decimal("-50.00"))
        receipt.void(memo="Returned unpaid", on_date=datetime.date(2026, 2, 20))
        self.assertEqual((self.balance(self.ar), self.balance(self.gain)), (Decimal("1200.00"), Decimal("0")))
        allocation.refresh_from_db()
        self.assertEqual(allocation.fx_released_entry.date, datetime.date(2026, 2, 20))
        allocation.delete()
        self.assertEqual(self.balance(self.gain), Decimal("0"))

    def test_a_recalled_payment_realised_nothing(self):
        from apps.purchasing.models import BillPayment

        bill = self.euro_bill("1000")
        self.rate_moves_to("1.25")
        paying = self.euro_payment(PaymentDirection.DISBURSEMENT, "1000")
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("1000"))
        self.assertEqual(self.balance(self.loss), Decimal("50.00"))
        paying.void(memo="Recalled", on_date=datetime.date(2026, 2, 20))
        self.assertEqual((self.balance(self.ap), self.balance(self.loss)), (Decimal("-1200.00"), Decimal("0")))


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


class RealisedOnTheAllocationsDayTests(FxSettlementTestCase):
    """
    1,000 EUR invoiced on 1 January at 1.20 (1,200.00) and received on
    15 February at 1.10 (1,100.00): 100.00 lost (calc_stat/o50_fx.py). The
    difference is realised when the receipt is applied to the invoice, on
    the allocation's own day, not the payment's: February may have closed
    by then. The edit, the delete and the payment's return follow it.
    """

    FEB_15, MAR_1 = datetime.date(2026, 2, 15), datetime.date(2026, 3, 1)

    def setUp(self):
        super().setUp()
        self.invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        self.receipt = self.euro_payment(PaymentDirection.RECEIPT, "1000")

    def close(self, name, start, end):
        from .models import AccountingPeriod

        period = AccountingPeriod.objects.create(name=name, start_date=start, end_date=end)
        period.close()
        return period

    def close_february(self):
        return self.close("Feb", datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))

    def apply(self, amount="1000", **more):
        from apps.sales.models import InvoicePayment

        return InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=Decimal(amount),
                                             **more)

    def loss_by_day(self):
        rows = JournalLine.objects.filter(account=self.loss, entry__posted=True).values("entry__date").annotate(
            net=Sum("debit") - Sum("credit")).order_by("entry__date")
        return [(row["entry__date"], row["net"]) for row in rows]

    def test_a_foreign_receipt_from_a_closed_month_can_still_be_allocated(self):
        """The audit's probe, with its figures: 100.09 EUR at 1.12345, received at 1.09876."""
        from apps.sales.models import InvoicePayment

        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("1.12345"), valid_from=datetime.date(2026, 1, 5))
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("1.09876"), valid_from=datetime.date(2026, 2, 2))
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 5),
                                          currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal("100.09"), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 5))
        invoice.post()
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        self.close_february()
        allocation = InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("100.09"))
        self.assertEqual((allocation.date, allocation.fx_entry.date), (timezone.localdate(), timezone.localdate()))

    def test_it_is_realised_on_the_day_given(self):
        allocation = self.apply(date=self.MAR_1)
        self.assertEqual(self.loss_by_day(), [(self.MAR_1, Decimal("100.00"))])
        self.assertEqual((allocation.fx_entry.date, self.balance(self.ar)), (self.MAR_1, Decimal("0")))

    def test_left_out_it_is_today(self):
        self.close_february()
        allocation = self.apply()
        self.assertEqual(self.loss_by_day(), [(timezone.localdate(), Decimal("100.00"))])
        self.assertEqual(allocation.date, timezone.localdate())

    def test_never_in_a_closed_month_before_the_payment_nor_to_come(self):
        self.close_february()
        for day, said in ((datetime.date(2026, 2, 20), "Feb is closed"),
                          (datetime.date(2026, 2, 10), "it was received on 2026-02-15"),
                          (timezone.localdate() + datetime.timedelta(days=1), "that day has not come")):
            with self.subTest(day=day), self.assertRaisesMessage(ValidationError, said):
                self.apply(date=day)
        self.assertEqual(self.loss_by_day(), [])

    def test_an_edit_restates_it_on_the_day_of_the_edit(self):
        """
        Applied on 1 March, March then closed. An edit that gives no day keeps
        1 March (O163: it was moved to today without a word), so March refuses
        it; given today, 100 is reversed and 60 realised today.
        """
        allocation = self.apply(date=self.MAR_1)
        self.close("Mar", self.MAR_1, datetime.date(2026, 3, 31))
        allocation.amount = Decimal("600")
        with self.assertRaisesMessage(ValidationError, "Mar is closed"):
            allocation.save()
        allocation = type(allocation).objects.get(pk=allocation.pk)
        today = timezone.localdate()
        allocation.amount, allocation.date = Decimal("600"), today
        allocation.save()
        self.assertEqual(self.loss_by_day(), [(self.MAR_1, Decimal("100.00")), (today, Decimal("-40.00"))])
        self.assertEqual(allocation.date, today)
        with self.assertRaisesMessage(ValidationError, "it was last applied on"):
            allocation.date = self.MAR_1 + datetime.timedelta(days=1)
            allocation.save()

    def test_a_delete_releases_it_on_the_day_of_the_delete(self):
        allocation = self.apply(date=self.MAR_1)
        self.close("Mar", self.MAR_1, datetime.date(2026, 3, 31))
        allocation.delete()
        self.assertEqual(self.loss_by_day(), [(self.MAR_1, Decimal("100.00")),
                                              (timezone.localdate(), Decimal("-100.00"))])

    def test_a_return_dated_before_it_was_applied_releases_it_on_its_own_day(self):
        """The bank returned it on 20 February, keyed in after it was applied on 1 March."""
        allocation = self.apply(date=self.MAR_1)
        self.receipt.void(memo="Returned unpaid", on_date=datetime.date(2026, 2, 20))
        allocation.refresh_from_db()
        self.assertEqual(allocation.fx_released_entry.date, self.MAR_1)
        self.assertEqual(self.loss_by_day(), [(self.MAR_1, Decimal("0.00"))])

    def test_the_purchasing_mirror(self):
        from apps.purchasing.models import BillPayment

        ExchangeRate.objects.filter(valid_from=datetime.date(2026, 2, 1)).delete()
        bill = self.euro_bill("1000")
        self.rate_moves_to("1.10")
        paying = self.euro_payment(PaymentDirection.DISBURSEMENT, "1000")
        self.close_february()
        with self.assertRaisesMessage(ValidationError, "Feb is closed"):
            BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("1000"),
                                       date=datetime.date(2026, 2, 20))
        allocation = BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("1000"))
        self.assertEqual((allocation.fx_entry.date, self.balance(self.gain), self.balance(self.ap)),
                         (timezone.localdate(), Decimal("-100.00"), Decimal("0")))

    def test_over_the_api_as_the_ar_manager(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ar")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        self.close_february()
        url = "/api/sales/invoice-payments/"
        body = {"invoice": self.invoice.pk, "payment": self.receipt.pk, "amount": "1000"}
        response = client.post(url, body | {"date": "2026-02-20"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Feb is closed", str(response.json()))
        response = client.post(url, body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["date"], timezone.localdate().isoformat())


@tag("migration")
class AllocationsDatedAsTheyWereMigrationTests(TransactionTestCase):
    """Allocations made before they kept a day realised theirs on the payment's: that is their day."""

    before = [("sales", "0062_allocation_keeps_its_cost"), ("purchasing", "0059_landed_cost_keeps_each_shelf")]
    after = [("sales", "0063_allocation_dated_on_its_own_day"),
             ("purchasing", "0060_allocation_dated_on_its_own_day")]

    def test_each_is_dated_on_its_payments_day(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        model = apps.get_model
        party = model("core", "Party").objects.create(code="P", name="P")
        account = model("accounting", "Account")
        bank = account.objects.create(code="1010", name="Bank", account_type="asset", holds_money=True)
        ar = account.objects.create(code="1100", name="AR", account_type="asset")
        ap = account.objects.create(code="2000", name="AP", account_type="liability")

        def paid(direction, day, counterpart):
            return model("accounting", "Payment").objects.create(
                party=party, direction=direction, payment_date=day, amount=Decimal("10"), bank_account=bank,
                counterpart_account=counterpart, posted=True)

        invoice = model("sales", "Invoice").objects.create(customer=party, invoice_date=datetime.date(2026, 1, 1),
                                                           receivable_account=ar)
        bill = model("purchasing", "Bill").objects.create(vendor=party, bill_date=datetime.date(2026, 1, 1),
                                                          payable_account=ap)
        model("sales", "InvoicePayment").objects.create(
            invoice=invoice, payment=paid("receipt", datetime.date(2026, 2, 15), ar), amount=Decimal("10"))
        model("purchasing", "BillPayment").objects.create(
            bill=bill, payment=paid("disbursement", datetime.date(2026, 2, 16), ap), amount=Decimal("10"))

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        self.assertEqual(
            (list(apps.get_model("sales", "InvoicePayment").objects.values_list("date", flat=True)),
             list(apps.get_model("purchasing", "BillPayment").objects.values_list("date", flat=True))),
            ([datetime.date(2026, 2, 15)], [datetime.date(2026, 2, 16)]))
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class EachSidesBookedBaseTests(FxSettlementTestCase):
    """
    O49, O51 (FxResidueProbe). 100.09 EUR booked at 1.12345 (112.45) and
    settled at 1.09876 (109.97): 2.48 to clear, worked by
    calc_fix_money/fx.py. round(100.09 * (1.12345 - 1.09876)) is 2.47,
    a figure neither side booked, and it left a paisa on the control
    account.
    """

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("1.12345"), valid_from=datetime.date(2026, 1, 5))
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("1.09876"), valid_from=datetime.date(2026, 2, 1))

    def invoice(self, amount):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 5),
                                          currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal(amount), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 5))
        invoice.post()
        return invoice

    def bill(self, amount):
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 5),
                                   payable_account=self.ap, currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=Decimal("1"), unit_price=Decimal(amount),
                                expense_account=self.expense)
        bill.post()
        return bill

    def test_the_difference_is_each_sides_base_subtracted(self):
        self.assertEqual(settlement_difference(Decimal("100.09"), Decimal("1.12345"), Decimal("1.09876")),
                         Decimal("2.48"))

    def test_a_settled_foreign_invoice_leaves_nothing_on_the_receivable(self):
        from apps.sales.models import InvoicePayment

        invoice = self.invoice("100.09")
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("100.09"))
        self.assertEqual((invoice.amount_due(), self.balance(self.ar), self.balance(self.loss)),
                         (Decimal("0"), Decimal("0"), Decimal("2.48")), "(due, AR, FX loss)")

    def test_a_settled_foreign_bill_leaves_nothing_on_the_payable(self):
        from apps.purchasing.models import BillPayment

        bill = self.bill("100.09")
        paying = self.euro_payment(PaymentDirection.DISBURSEMENT, "100.09")
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("100.09"))
        self.assertEqual((bill.amount_due(), self.balance(self.ap), self.balance(self.gain)),
                         (Decimal("0"), Decimal("0"), Decimal("-2.48")), "(due, AP, FX gain)")

    def test_a_deposit_drawn_in_two_halves_leaves_nothing_held(self):
        """100 EUR held at 1.12345 is 112.35; drawn 50 + 50 the first clears 56.17, the last the 56.18 left."""
        from apps.sales.models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine

        deposits = Account.objects.create(code="2300", name="Deposits", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=deposits)
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 5),
                                          currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("2"),
                                      unit_price=Decimal("100"), revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, amount=Decimal("100"),
                                                    invoice_date=datetime.date(2026, 1, 5))
        deposit.post()
        for _ in range(2):
            invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 1, 5),
                                             receivable_account=self.ar, currency=self.eur)
            InvoiceLine.objects.create(invoice=invoice, description="Sacks", quantity=Decimal("1"),
                                       unit_price=Decimal("100"), revenue_account=self.revenue)
            invoice.post()
            invoice.apply_deposit(Invoice.objects.get(pk=deposit.pk), amount=Decimal("50"))
        self.assertEqual(Invoice.objects.get(pk=deposit.pk).deposit_unapplied(), Decimal("0"))
        # Each invoice's half is its slice of the 112.35 it booked: 56.18 (review F1). The first
        # half clears 56.17 held, the last the 56.18 left: 0.01 of exchange on the first only.
        self.assertEqual((self.balance(deposits), self.balance(self.loss), self.balance(self.gain)),
                         (Decimal("0"), Decimal("0.01"), Decimal("0")),
                         "(deposits, FX loss, FX gain): the last half takes the 56.18 left")

    def test_a_refunded_foreign_debit_note_clears_the_payable(self):
        """
        O70's purchasing mirror. 100 EUR billed and paid at 1.12345 (112.35), debited back
        in full, refunded at 1.09876 (109.88): the refund clears 109.88 of the note's 112.35,
        and the 2.47 left is an exchange loss, taken off the payable the right way.
        """
        from apps.purchasing.models import BillPayment

        bill = self.bill("100")
        paying = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=datetime.date(2026, 1, 6))
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("100"))
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("1")})
        refund = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 2, 15),
            amount=Decimal("100"), currency=self.eur, bank_account=self.bank, counterpart_account=self.ap)
        refund.post()
        BillPayment.objects.create(bill=note, payment=refund, amount=Decimal("100"))
        self.assertEqual((self.balance(self.ap), self.balance(self.loss)), (Decimal("0"), Decimal("2.47")),
                         "(AP, FX loss)")


# Review F1, F2 (review_money's probes, kept as regression tests with their numbers):
# a document's last part, and the note that takes the last of money held, take
# what is left of the base booked (settlement.slice_of, settlement.last_of_held).
D = Decimal
JAN5 = datetime.date(2026, 1, 5)
FEB = datetime.date(2026, 2, 15)

class PartSettlementTakesWhatIsLeftTests(FxSettlementTestCase):
    """100.09 EUR booked at 1.12345 (112.45), paid at 1.09876 in two parts 50 + 50.09."""

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    def invoice(self, amount):
        from apps.sales.models import SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                      unit_price=D(amount), revenue_account=self.revenue)
        order.confirm()
        inv = order.create_invoice(self.ar, invoice_date=JAN5)
        inv.post()
        return inv

    def bill(self, amount):
        from apps.purchasing.models import Bill, BillLine
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN5, payable_account=self.ap, currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D(amount),
                                expense_account=self.expense)
        bill.post()
        return bill

    def test_two_receipts_one_after_the_other_clear_the_receivable(self):
        # calc.py case 1b: receipts 54.94 + 55.04, AR = 112.45 - 109.98 - 2.46 = 0.01; expected 0
        from apps.sales.models import InvoicePayment
        inv = self.invoice("100.09")
        for amount in ("50", "50.09"):
            receipt = self.euro_payment(PaymentDirection.RECEIPT, amount)
            InvoicePayment.objects.create(invoice=inv, payment=receipt, amount=D(amount))
        self.assertEqual((inv.amount_due(), self.balance(self.ar)), (D("0"), D("0")), "(due, AR)")

    def test_the_payable_mirror(self):
        from apps.purchasing.models import BillPayment
        bill = self.bill("100.09")
        for amount in ("50", "50.09"):
            pay = self.euro_payment(PaymentDirection.DISBURSEMENT, amount)
            BillPayment.objects.create(bill=bill, payment=pay, amount=D(amount))
        self.assertEqual((bill.amount_due(), self.balance(self.ap)), (D("0"), D("0")), "(due, AP)")

    def test_bounced_then_paid_again_at_another_rate_clears_and_keeps_only_the_second_difference(self):
        # 100 EUR @1.12345 = 112.35 (112.345 half up). First receipt @1.09876 = 109.88 (109.876), voided.
        # Second @1.2 on 1 March = 120.00. Difference on the second: 112.35 - 120.00 = -7.65 (a gain).
        from apps.sales.models import InvoicePayment
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 1))
        inv = self.invoice("100")
        first = self.euro_payment(PaymentDirection.RECEIPT, "100")
        InvoicePayment.objects.create(invoice=inv, payment=first, amount=D("100"))
        first.void()
        second = self.euro_payment(PaymentDirection.RECEIPT, "100", on=datetime.date(2026, 3, 2))
        InvoicePayment.objects.create(invoice=inv, payment=second, amount=D("100"))
        self.assertEqual((self.balance(self.ar), self.balance(self.gain), self.balance(self.loss)),
                         (D("0"), D("-7.65"), D("0")), "(AR, gain, loss)")


class MiddleAllocationUndoneTests(FxSettlementTestCase):
    """
    O209: each allocation's base is what the document (or the payment) booked
    for all of it settled so far, less what the others standing cleared, as
    each recorded it. 100.09 EUR booked at 1.12345 (112.45), applied 30 + 30 +
    40.09 from receipts at 1.09876 (calc_fix_money/g22/o209.py).
    """

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    invoice = PartSettlementTakesWhatIsLeftTests.invoice
    bill = PartSettlementTakesWhatIsLeftTests.bill

    def three_receipts(self, inv):
        from apps.sales.models import InvoicePayment
        rows = []
        for amount in ("30", "30", "40.09"):
            receipt = self.euro_payment(PaymentDirection.RECEIPT, amount)
            rows.append(InvoicePayment.objects.create(invoice=inv, payment=receipt, amount=D(amount)))
        return rows

    def test_the_middle_deleted_and_applied_again_clears_the_receivable(self):
        # Sliced against what settled before: 33.70 + 45.04 + 33.70 = 112.44, 0.01 left on AR.
        from apps.sales.models import InvoicePayment
        inv = self.invoice("100.09")
        first, middle, last = self.three_receipts(inv)
        payment = middle.payment
        middle.delete()
        again = InvoicePayment.objects.create(invoice=inv, payment=payment, amount=D("30"))
        self.assertEqual((first.document_base, last.document_base, again.document_base),
                         (D("33.70"), D("45.04"), D("33.71")))
        self.assertEqual((inv.amount_due(), self.balance(self.ar)), (D("0"), D("0")), "(due, AR)")

    def test_the_middle_re_pointed_away_and_another_applied_clears_both(self):
        # The 30 moves to a 30 EUR invoice (33.70) and settles it; a fourth receipt of 30 settles the first.
        from apps.sales.models import InvoicePayment
        inv, other = self.invoice("100.09"), self.invoice("30")
        _first, middle, _last = self.three_receipts(inv)
        middle.invoice = other
        middle.save()
        fourth = self.euro_payment(PaymentDirection.RECEIPT, "30")
        InvoicePayment.objects.create(invoice=inv, payment=fourth, amount=D("30"))
        self.assertEqual((inv.amount_due(), other.amount_due(), self.balance(self.ar)),
                         (D("0"), D("0"), D("0")), "(due, other due, AR)")

    def test_a_payment_whose_middle_allocation_is_deleted_and_made_again_clears(self):
        # One receipt of 100.09 at 1.09876 (109.97) over 0.01 + 1.57 + 98.51: sliced against what was
        # applied before, the middle made again took 1.72 and 0.01 stayed on AR.
        from apps.sales.models import InvoicePayment
        invoices = [self.invoice(amount) for amount in ("0.01", "1.57", "98.51")]
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        rows = [InvoicePayment.objects.create(invoice=inv, payment=receipt, amount=inv.total())
                for inv in invoices]
        rows[1].delete()
        InvoicePayment.objects.create(invoice=invoices[1], payment=receipt, amount=D("1.57"))
        self.assertEqual(self.balance(self.ar), D("0"))

    def test_the_payable_mirror(self):
        from apps.purchasing.models import BillPayment
        bill = self.bill("100.09")
        rows = []
        for amount in ("30", "30", "40.09"):
            pay = self.euro_payment(PaymentDirection.DISBURSEMENT, amount)
            rows.append(BillPayment.objects.create(bill=bill, payment=pay, amount=D(amount)))
        payment = rows[1].payment
        rows[1].delete()
        BillPayment.objects.create(bill=bill, payment=payment, amount=D("30"))
        self.assertEqual((bill.amount_due(), self.balance(self.ap)), (D("0"), D("0")), "(due, AP)")


class HeldMoneyLeavesAtTheBaseLeftTests(FxSettlementTestCase):
    """A deposit and a prepayment of 100 EUR held at 1.12345 (112.35), drawn in three, with a note."""

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        self.deposits = Account.objects.create(code="2300", name="Deposits", account_type=AccountType.LIABILITY)
        self.prepaid = Account.objects.create(code="1400", name="Prepaid", account_type=AccountType.ASSET)
        Company.objects.update(customer_deposit_account=self.deposits, vendor_prepayment_account=self.prepaid)

    def deposit(self, amount="100"):
        from apps.sales.models import SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        dep = order.create_down_payment_invoice(self.ar, amount=D(amount), invoice_date=JAN5)
        dep.post()
        from apps.sales.models import InvoicePayment
        receipt = self.euro_payment(PaymentDirection.RECEIPT, amount, on=JAN5)
        InvoicePayment.objects.create(invoice=dep, payment=receipt, amount=D(amount))
        return dep

    def final_invoice(self, amount, on):
        from apps.sales.models import Invoice, InvoiceLine
        inv = Invoice.objects.create(customer=self.customer, invoice_date=on, receivable_account=self.ar,
                                     currency=self.eur)
        InvoiceLine.objects.create(invoice=inv, description="Sacks", quantity=D("1"), unit_price=D(amount),
                                   revenue_account=self.revenue)
        inv.post()
        return inv

    def test_deposit_drawn_in_three_at_a_later_rate_leaves_nothing_held(self):
        # calc.py case 2: held 112.35; draws take 37.44, 37.44, 37.47; invoices booked at 1.2: 40.00, 40.00, 40.01
        from apps.sales.models import Invoice
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        dep = self.deposit()
        for amount in ("33.33", "33.33", "33.34"):
            inv = self.final_invoice(amount, datetime.date(2026, 2, 2))
            inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D(amount))
        self.assertEqual((self.balance(self.deposits), self.balance(self.ar), self.balance(self.loss)),
                         (D("0"), D("0"), D("7.66")), "(deposits, AR, FX loss)")

    def test_deposit_credited_back_in_part_then_drawn_to_nothing(self):
        # 100 held (112.35). Note 20 EUR (22.47 at 1.12345: 22.469). Drawn 80 at 1.2: takes 112.35-22.47 = 89.88.
        from apps.sales.models import Invoice
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        dep = self.deposit()
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("20"))
        inv = self.final_invoice("80", datetime.date(2026, 2, 2))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("80"))
        self.assertEqual((self.balance(self.deposits), Invoice.objects.get(pk=dep.pk).deposit_unapplied()),
                         (D("0"), D("0")), "(deposits, left)")

    def test_deposit_drawn_then_noted_in_the_rest(self):
        # draw 60 first (takes round(60*1.12345)=67.41), then credit the other 40: note takes what is left?
        from apps.sales.models import Invoice
        dep = self.deposit()
        inv = self.final_invoice("60", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("60"))
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("40"))
        self.assertEqual(self.balance(self.deposits), D("0"), "deposits after the last 40 is credited back")

    def test_prepayment_drawn_in_three_leaves_nothing_held(self):
        from apps.purchasing.models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        from apps.purchasing.models import BillPayment
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        for amount in ("33.33", "33.33", "33.34"):
            bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 2, 2),
                                       payable_account=self.ap, currency=self.eur)
            BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D(amount),
                                    expense_account=self.expense)
            bill.post()
            bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D(amount))
        self.assertEqual((self.balance(self.prepaid), self.balance(self.ap)), (D("0"), D("0")), "(prepaid, AP)")

    def test_deposit_drawn_in_half_then_the_other_half_credited_back_leaves_nothing_held(self):
        # held 112.35. Draw 50 takes round(56.1725)=56.17. The last 50 credited back books round(56.1725)=56.17.
        # 112.35 - 56.17 - 56.17 = 0.01 stays on Deposits unless the note, as the last, takes what is left.
        from apps.sales.models import Invoice
        dep = self.deposit()
        inv = self.final_invoice("50", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("50"))
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("50"))
        self.assertEqual((Invoice.objects.get(pk=dep.pk).deposit_unapplied(), self.balance(self.deposits)),
                         (D("0"), D("0")), "(left in EUR, Deposits in base)")

    def test_prepayment_drawn_in_half_then_the_other_half_debited_back_leaves_nothing_held(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment, PurchaseOrder, PurchaseOrderLine
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 6), payable_account=self.ap,
                                   currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D("50"),
                                expense_account=self.expense)
        bill.post()
        bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D("50"))
        Bill.objects.get(pk=pre.pk).create_debit_note(amount=D("50"))
        self.assertEqual(self.balance(self.prepaid), D("0"), "Prepaid in base")

    def test_setting_moved_after_the_deposit_then_credited_back_and_drawn_leaves_both_accounts_empty(self):
        from apps.sales.models import Invoice
        dep = self.deposit()
        moved = Account.objects.create(code="2310", name="Advances", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=moved)
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("20"))
        inv = self.final_invoice("80", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("80"))
        self.assertEqual((self.balance(self.deposits), self.balance(moved)), (D("0"), D("0")), "(old, new)")

    def test_setting_moved_after_the_prepayment_then_debited_back_and_drawn_leaves_both_accounts_empty(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment, PurchaseOrder, PurchaseOrderLine
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        moved = Account.objects.create(code="1401", name="Advances", account_type=AccountType.ASSET)
        Company.objects.update(vendor_prepayment_account=moved)
        Bill.objects.get(pk=pre.pk).create_debit_note(amount=D("20"))
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 6), payable_account=self.ap,
                                   currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D("80"),
                                expense_account=self.expense)
        bill.post()
        bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D("80"))
        self.assertEqual((self.balance(self.prepaid), self.balance(moved)), (D("0"), D("0")), "(old, new)")


class OnePaymentOverTwoDocumentsTests(PartSettlementTakesWhatIsLeftTests):
    """
    Review F1, the payment side. One receipt of 100.09 EUR at 1.09876 books 109.97; applied 50
    to an invoice of 50 (56.17) and 50.09 to one of 50.09 (56.27), it gives 54.94 and the 55.03
    left of what it booked, not 55.04: exchange 1.23 + 1.24, nothing on the receivable
    (calc_fix_money/review/f1f2.py).
    """

    def test_a_receipt_split_over_two_invoices_leaves_nothing_on_the_receivable(self):
        from apps.sales.models import InvoicePayment

        first, second = self.invoice("50"), self.invoice("50.09")
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09")
        InvoicePayment.objects.create(invoice=first, payment=receipt, amount=Decimal("50"))
        InvoicePayment.objects.create(invoice=second, payment=receipt, amount=Decimal("50.09"))
        self.assertEqual((self.balance(self.ar), self.balance(self.loss)), (Decimal("0"), Decimal("2.47")),
                         "(AR, FX loss)")


class BillSettlementDiscountTests(FxSettlementTestCase):
    """
    O109, the payable mirror through the same function: 100 EUR billed 5 January
    at 1.12345 (112.35) on 2/10 net 30, 98 EUR paid 10 January (110.10), 2 EUR
    discount at the bill's rate (2.25): the payable clears.
    """

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    bill = PartSettlementTakesWhatIsLeftTests.bill

    def test_the_discount_on_the_payment_that_earned_it_clears_the_payable(self):
        from apps.core.models import PaymentTerms
        from apps.purchasing.models import Bill, BillPayment
        received = Account.objects.create(code="4991", name="Discount received", account_type=AccountType.INCOME)
        Company.objects.update(settlement_discount_received_account=received)
        terms = PaymentTerms.objects.create(code="2-10", name="2/10 net 30", net_days=30,
                                            discount_percent=D("2"), discount_days=10)
        bill = self.bill("100")
        Bill.objects.filter(pk=bill.pk).update(payment_terms=terms)
        bill.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "98", on=datetime.date(2026, 1, 10))
        BillPayment.objects.create(bill=bill, payment=pay, amount=D("98"))
        bill.take_settlement_discount(payment=pay)
        self.assertEqual((bill.amount_due(), self.balance(self.ap), self.balance(received)),
                         (D("0.00"), D("0"), D("-2.25")), "(due, AP, discount received)")



class NoteCountedAtWhatItBookedTests(FxSettlementTestCase):
    """
    O209, second half: 10.01 + 20.78 + 25.37 EUR invoiced at 1.12345 (11.25 + 23.35 + 28.50 = 63.10); a
    credit note of the first two lines books 34.60, where its slice of the invoice reads 34.59; the receipt
    for the rest (25.37 at 1.09876) takes what is left of the base, 28.50 (calc_fix_money/g22/o209b.py).
    """

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    def test_a_receipt_after_a_note_on_two_of_three_lines_clears_the_receivable(self):
        from apps.sales.models import InvoicePayment, SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        for price in ("10.01", "20.78", "25.37"):
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                          unit_price=D(price), revenue_account=self.revenue)
        order.confirm()
        inv = order.create_invoice(self.ar, invoice_date=JAN5)
        inv.post()
        first, second, _ = inv.lines.order_by("pk")
        note = inv.create_credit_note(quantities={first: D("1"), second: D("1")}, on_date=JAN5)
        receipt = self.euro_payment(PaymentDirection.RECEIPT, "25.37")
        InvoicePayment.objects.create(invoice=inv, payment=receipt, amount=D("25.37"))
        self.assertEqual((note.total(), inv.amount_due(), self.balance(self.ar)), (D("30.79"), D("0"), D("0")),
                         "(note, due, AR)")
