"""
Buying in a foreign currency: booked at the rate the goods arrived at,
cleared at exactly that, and the difference called what it is.

Before this a receipt booked the order's figures as though they were
base currency, and the bill — which does convert — cleared a different
amount from the accrual than the receipt had put in it. A euro purchase
of ten at 5.00 went on the shelf at 50, the bill at 1.2 took 60 out of
the accrual, and the 10 sat there for ever while the stock was
understated by it.

Euro rates throughout: 1.2 from 1 January, 1.3 from 8 January. Ten at
€5.00 is €50.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, ExchangeRate

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from .tests_lifecycle import PurchasingLifecycleTestCase

JAN = lambda day: datetime.date(2026, 1, day)


class ForeignTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.2"), valid_from=JAN(1)
        )
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.3"), valid_from=JAN(8)
        )
        self.vendor.default_currency = self.eur
        self.vendor.save()
        acc = lambda code, name, kind: Account.objects.create(
            code=code, name=name, account_type=kind
        )
        self.ppv = acc("5150", "Price variance", AccountType.EXPENSE)
        self.fx_gain = acc("4900", "Exchange gain", AccountType.INCOME)
        self.fx_loss = acc("5900", "Exchange loss", AccountType.EXPENSE)
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.fx_gain_account = self.fx_gain
        company.fx_loss_account = self.fx_loss
        company.save()

    def order(self, quantity="10", price="5", currency=None):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=JAN(1),
            currency=currency or self.eur,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        order.confirm()
        return order

    def receive_on(self, order, quantity, day):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=JAN(day)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.get(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def bill_on(self, order, quantity, day, price="5"):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=JAN(day),
            payable_account=self.payable, purchase_order=order,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, order_line=order.lines.get(),
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        bill.post()
        return bill

    def balance(self, account):
        rows = JournalLine.objects.filter(
            account=account, entry__posted=True
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class TheReceiptIsBookedInBaseCurrencyTests(ForeignTestCase):
    def test_the_stock_and_the_accrual_go_in_at_the_days_rate(self):
        receipt = self.receive_on(self.order(), "10", 5)
        self.assertEqual(receipt.exchange_rate, Decimal("1.2"))
        self.assertEqual(self.balance(self.inventory), Decimal("60.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-60.00"))
        line = receipt.lines.get()
        self.assertEqual(line.accrued_unit_price, Decimal("5"))
        self.assertEqual(line.accrued_unit_cost, Decimal("6.000000"))

    def test_a_base_currency_order_is_untouched(self):
        receipt = self.receive_on(self.order(currency=self.usd), "10", 5)
        self.assertEqual(receipt.exchange_rate, Decimal("1"))
        self.assertEqual(self.balance(self.inventory), Decimal("50.00"))


class TheBillClearsWhatWasBookedTests(ForeignTestCase):
    def test_at_the_same_rate_nothing_is_left_and_nothing_is_exchange(self):
        order = self.order()
        self.receive_on(order, "10", 5)
        self.bill_on(order, "10", 6)
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-60.00"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("0"))

    def test_a_rate_that_rose_is_an_exchange_loss_not_a_price(self):
        """€50 booked at 1.2 is 60; billed at 1.3 it is owed as 65."""
        order = self.order()
        self.receive_on(order, "10", 5)
        self.bill_on(order, "10", 10)
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-65.00"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("5.00"))
        self.assertEqual(self.balance(self.ppv), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("60.00"))

    def test_a_rate_that_fell_is_an_exchange_gain(self):
        ExchangeRate.objects.create(
            currency=self.eur, rate=Decimal("1.1"), valid_from=JAN(9)
        )
        order = self.order()
        self.receive_on(order, "10", 5)
        self.bill_on(order, "10", 10)
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-55.00"))
        self.assertEqual(self.balance(self.fx_gain), Decimal("-5.00"))

    def test_a_price_difference_is_still_a_price_difference(self):
        """
        Billed at €5.50 on the day the goods came: 5 of price variance
        in euros, 6 in base at 1.2, and no exchange difference at all.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("20")
        company.save()
        order = self.order()
        self.receive_on(order, "10", 5)
        self.bill_on(order, "10", 6, price="5.50")
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("6.00"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("0"))

    def test_without_an_exchange_account_the_bill_says_so(self):
        company = Company.get()
        company.fx_loss_account = None
        company.save()
        order = self.order()
        self.receive_on(order, "10", 5)
        with self.assertRaisesMessage(ValidationError, "exchange loss account"):
            self.bill_on(order, "10", 10)


class ReceiptsAtTwoRatesTests(ForeignTestCase):
    """
    Five on 5 January at 1.2 (30) and five on 9 January at 1.3 (32.50):
    62.50 in the accrual. Two of something, because one receipt cannot
    tell a first-in-first-out walk from any other.
    """

    def test_one_bill_for_both_clears_both(self):
        order = self.order()
        self.receive_on(order, "5", 5)
        self.receive_on(order, "5", 9)
        self.bill_on(order, "10", 10)
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("2.50"))

    def test_two_bills_clear_the_oldest_first(self):
        order = self.order()
        self.receive_on(order, "5", 5)
        self.receive_on(order, "5", 9)
        first = self.bill_on(order, "5", 10)
        self.assertEqual(first.lines.get().accrued_base, Decimal("30.000000"))
        self.assertEqual(self.balance(self.grni), Decimal("-32.50"))
        second = self.bill_on(order, "5", 10)
        self.assertEqual(second.lines.get().accrued_base, Decimal("32.500000"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_two_lines_on_one_bill_do_not_clear_the_same_receipt(self):
        """
        The bill is not yet posted while it is built, so it does not
        count itself as billed. Without carrying what its first line
        took, the second would clear the first receipt again.
        """
        order = self.order()
        self.receive_on(order, "5", 5)
        self.receive_on(order, "5", 9)
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=JAN(10),
            payable_account=self.payable, purchase_order=order,
        )
        for _ in range(2):
            BillLine.objects.create(
                bill=bill, item=self.item, order_line=order.lines.get(),
                quantity=Decimal("5"), unit_price=Decimal("5"),
            )
        bill.post()
        self.assertEqual(
            sorted(line.accrued_base for line in bill.lines.all()),
            [Decimal("30.000000"), Decimal("32.500000")],
        )
        self.assertEqual(self.balance(self.grni), Decimal("0"))


class SendingItBackTests(ForeignTestCase):
    def test_a_return_before_the_bill_goes_back_at_the_rate_it_came(self):
        """
        Ten in at 1.2, four back after the rate has moved to 1.3: the
        four leave at 24, not 26, or the accrual keeps two.
        """
        order = self.order()
        receipt = self.receive_on(order, "10", 5)
        returned = receipt.create_return(
            {receipt.lines.get(): "4"}, debit_bills=False
        )
        self.assertEqual(returned.exchange_rate, Decimal("1.2"))
        self.assertEqual(self.balance(self.inventory), Decimal("36.00"))
        self.bill_on(order, "6", 10)
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_a_return_after_the_bill_is_debited_at_what_it_booked(self):
        """
        Five at 1.2 and five at 1.3, all billed at 1.3; the first five
        go back. The return takes 30 out of the accrual, so the debit
        note must put back 30 — not the 31.25 the bill line averaged
        over both receipts, which would leave 1.25 there for ever. What
        the vendor owes back is at the bill's rate, 32.50; the 2.50
        between is the exchange loss on those five, reversed.
        """
        order = self.order()
        first = self.receive_on(order, "5", 5)
        self.receive_on(order, "5", 9)
        self.bill_on(order, "10", 10)
        first.create_return()
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-32.50"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("32.50"))


class APriceRevisedAfterTheGoodsCameTests(ForeignTestCase):
    def test_the_bill_clears_what_the_receipt_booked_not_the_new_price(self):
        """
        Received at €5.00 (60 at 1.2), the order then re-priced to
        €5.50 and billed at that the same day. The accrual holds 60 and
        must be cleared at 60; the €5 difference is price, 6 at 1.2.
        """
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("20")
        company.save()
        order = self.order()
        self.receive_on(order, "10", 5)
        line = order.lines.get()
        line.unit_price = Decimal("5.50")
        line.save()
        self.bill_on(order, "10", 6, price="5.50")
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("6.00"))


class OlderReceiptsReadAsTheyBookedTests(ForeignTestCase):
    def test_a_receipt_with_nothing_frozen_is_read_at_one(self):
        """
        Receipts from before any of this booked the order's figure as
        base currency. Read back at the receipt's rate they would be
        cleared at a figure they never put in.
        """
        order = self.order()
        receipt = self.receive_on(order, "10", 5)
        GoodsReceipt.objects.filter(pk=receipt.pk).update(exchange_rate=None)
        GoodsReceiptLine.objects.filter(receipt=receipt).update(
            accrued_unit_price=None, accrued_unit_cost=None
        )
        (layer,) = order.lines.get().accrual_layers()
        self.assertEqual(layer, (Decimal("10"), Decimal("5"), Decimal("5")))


class TheWalkThroughReceiptsTests(ForeignTestCase):
    """Five at 1.2 (6.00 a unit) and five at 1.3 (6.50), as above."""

    def two_receipts(self):
        order = self.order()
        first = self.receive_on(order, "5", 5)
        self.receive_on(order, "5", 9)
        return order, first

    def test_a_bill_that_ran_past_a_receipt_leaves_the_next_where_it_stopped(self):
        """
        Seven billed first: all of the first receipt and two of the
        second. The next three are the rest of the second, 3 x 6.50 =
        19.50 — not an overdrawn first receipt.
        """
        order, _first = self.two_receipts()
        self.bill_on(order, "7", 10)
        second = self.bill_on(order, "3", 10)
        self.assertEqual(second.lines.get().accrued_base, Decimal("19.500000"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_a_receipt_sent_back_before_billing_is_not_billed_against(self):
        """
        The first five go back before any bill. The five that remain
        came at 1.3, so the bill clears 32.50 — clearing the returned
        receipt's 30 instead would leave 2.50 in the accrual.
        """
        order, first = self.two_receipts()
        first.create_return(debit_bills=False)
        bill = self.bill_on(order, "5", 10)
        self.assertEqual(bill.lines.get().accrued_base, Decimal("32.500000"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_goods_returned_after_a_reprice_go_back_at_the_old_price(self):
        """
        Received at €5.00 at 1.2, the order re-priced to €5.50, then
        four sent back: they leave at the 6.00 they came in at, not at
        6.60. The shelf and the accrual both keep 36.
        """
        order = self.order()
        receipt = self.receive_on(order, "10", 5)
        line = order.lines.get()
        line.unit_price = Decimal("5.50")
        line.save()
        receipt.create_return({receipt.lines.get(): "4"}, debit_bills=False)
        self.assertEqual(self.balance(self.inventory), Decimal("36.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-36.00"))


class ABillThatNamesNoOrderLineTests(ForeignTestCase):
    def test_it_is_cleared_at_its_own_price_and_rate_as_before(self):
        """
        No link, no agreed price and no particular receipt: cleared at
        what it bills, at its own rate, with no variance and no
        exchange line — the rule these bills always had. Received at
        1.2 and billed unlinked at 1.3, the accrual keeps the 5 the
        link would have explained, which is why a foreign purchase
        should be billed against its order line.
        """
        order = self.order()
        self.receive_on(order, "10", 5)
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=JAN(10), payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal("10"),
            unit_price=Decimal("5"),
        )
        bill.post()
        self.assertEqual(self.balance(self.payable), Decimal("-65.00"))
        self.assertEqual(self.balance(self.grni), Decimal("5.00"))
        self.assertEqual(self.balance(self.fx_loss), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("0"))
