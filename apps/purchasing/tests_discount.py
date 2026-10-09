"""
A discount is taken once, when the goods arrive.

Ten at 5.00 less ten per cent: 45 is what they cost. The receipt used to
accrue the gross 50 and the bill cleared the net 45, leaving 5 in goods
received not invoiced for ever and the stock overstated by it. Found by
a probe while rewriting the clearing for foreign currency.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class DiscountTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.ppv = Account.objects.create(
            code="5150", name="Price variance", account_type=AccountType.EXPENSE
        )
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.purchase_price_tolerance_percent = Decimal("20")
        company.save()

    def order(self, discount="10"):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("5"), discount_percent=Decimal(discount),
        )
        order.confirm()
        return order

    def bill(self, order, discount="10", price="5"):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable, purchase_order=order,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, order_line=order.lines.get(),
            quantity=Decimal("10"), unit_price=Decimal(price),
            discount_percent=Decimal(discount),
        )
        bill.post()
        return bill

    def balance(self, account):
        rows = JournalLine.objects.filter(
            account=account, entry__posted=True
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class TheDiscountIsTakenOnceTests(DiscountTestCase):
    def test_the_receipt_books_what_the_goods_cost(self):
        self.receive(self.order(), "10")
        self.assertEqual(self.balance(self.inventory), Decimal("45.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-45.00"))

    def test_the_bill_clears_it_to_nothing(self):
        order = self.order()
        self.receive(order, "10")
        self.bill(order)
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-45.00"))
        self.assertEqual(self.balance(self.ppv), Decimal("0"))

    def test_a_bill_that_forgets_the_discount_is_a_variance(self):
        """Billed 50 against 45 agreed: 5 unfavourable, accrual clear."""
        order = self.order()
        self.receive(order, "10")
        self.bill(order, discount="0")
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("5.00"))

    def test_a_return_takes_back_the_discounted_cost(self):
        order = self.order()
        receipt = self.receive(order, "10")
        receipt.create_return({receipt.lines.get(): "4"}, debit_bills=False)
        self.assertEqual(self.balance(self.inventory), Decimal("27.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-27.00"))

    def test_returned_after_billing_everything_clears(self):
        order = self.order()
        receipt = self.receive(order, "10")
        self.bill(order)
        receipt.create_return()
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))


class AReceiptFromBeforeTests(DiscountTestCase):
    def test_a_gross_receipt_is_cleared_at_gross_with_the_discount_as_variance(self):
        """
        Receipts from before this accrued 50. Read back at what they
        froze — or, with nothing frozen, at the order's gross price —
        they clear exactly, and the discount they never took shows as a
        favourable variance rather than a stuck accrual.
        """
        order = self.order()
        receipt = self.receive(order, "10")
        GoodsReceiptLine.objects.filter(receipt=receipt).update(
            accrued_unit_price=None, accrued_unit_cost=None
        )
        GoodsReceipt.objects.filter(pk=receipt.pk).update(exchange_rate=None)
        (layer,) = order.lines.get().accrual_layers()
        self.assertEqual(layer, (Decimal("10"), Decimal("5"), Decimal("5")))


class TheFallbacksTakeTheirOwnDiscountTests(DiscountTestCase):
    def test_a_bill_naming_no_order_line_for_goods_on_order_is_refused(self):
        """
        45 accrued; an unlinked bill at 5.00 less ten per cent is refused, and the
        accrual stays for the order's own bill. It used to clear the 45, and the
        order's bill then paid the same goods again (O92).
        """
        order = self.order()
        self.receive(order, "10")
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal("10"),
            unit_price=Decimal("5"), discount_percent=Decimal("10"),
        )
        with self.assertRaisesMessage(ValidationError, "Name the order line this bill pays"):
            bill.post()
        self.assertEqual(self.balance(self.grni), Decimal("-45.00"))
        self.assertEqual(self.balance(self.ppv), Decimal("0"))

    def test_a_debit_note_on_a_line_with_nothing_frozen_takes_the_discount(self):
        """
        A bill line posted before accruals were frozen, debited whole:
        the goods are still here and now un-invoiced again, so the
        accrual must go back to the 45 they cost — not to 50.
        """
        order = self.order()
        self.receive(order, "10")
        bill = self.bill(order)
        BillLine.objects.filter(bill=bill).update(
            accrued_doc=None, accrued_base=None
        )
        bill.refresh_from_db()
        bill.create_debit_note()
        self.assertEqual(self.balance(self.grni), Decimal("-45.00"))
        self.assertEqual(self.balance(self.payable), Decimal("0"))
