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
        """Applied on 1 March, March then closed: cut to 600 today, 100 reversed and 60 realised today."""
        allocation = self.apply(date=self.MAR_1)
        self.close("Mar", self.MAR_1, datetime.date(2026, 3, 31))
        allocation.amount = Decimal("600")
        allocation.save()
        today = timezone.localdate()
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
