"""
Drop-ship: the vendor delivers straight to the customer.

This breaks an invariant the rest of the system leans on — that a
delivery draws on stock you hold — so it is isolated rather than
generalised. The goods never touch a warehouse, so there is no stock
movement to make and the cost goes straight to cost of sales instead of
being capitalised and immediately released.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import MovementType, StockMovement
from apps.sales.models import (
    Delivery,
    FulfilmentStatus,
    InvoicePolicy,
    SalesOrder,
    SalesOrderLine,
)

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, VendorPrice
from .tests_lifecycle import PurchasingLifecycleTestCase


class DropShipTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.cogs = Account.objects.create(
            code="5001", name="COGS", account_type=AccountType.EXPENSE
        )
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.ar = Account.objects.create(
            code="1100", name="AR", account_type=AccountType.ASSET
        )
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.default_purchase_expense_account = self.expense
        company.save()

        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("6")
        )

    def sales_order(self, quantity="10", price="10", confirm=True,
                    policy=InvoicePolicy.DELIVERED):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 1, 1),
            currency=self.usd, invoice_policy=policy,
        )
        self.sales_line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
            unit_price=Decimal(price), revenue_account=self.revenue,
        )
        if confirm:
            order.confirm()
        return order

    def receive(self, purchase_order, quantity):
        receipt = GoodsReceipt.objects.create(
            purchase_order=purchase_order, receipt_date=datetime.date(2026, 1, 10)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=purchase_order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class DropShipCreationTests(DropShipTestCase):
    def test_it_raises_a_purchase_order_against_the_sale(self):
        sale = self.sales_order("10", "10")

        order = PurchaseOrder.create_for_drop_ship(
            sale, self.vendor, order_date=datetime.date(2026, 1, 2)
        )

        self.assertEqual(order.drop_ship_for, sale)
        self.assertTrue(order.is_drop_ship())
        self.assertEqual(order.lines.get().quantity, Decimal("10"))
        self.assertEqual(order.lines.get().sales_order_line, self.sales_line)

    def test_it_prices_from_the_agreed_vendor_price(self):
        sale = self.sales_order("10", "10")
        order = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        self.assertEqual(order.lines.get().unit_price, Decimal("6"))

    def test_it_tells_the_vendor_where_to_send_it(self):
        sale = self.sales_order()
        order = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        self.assertIn(str(self.customer), order.shipping_note)

    def test_a_draft_sales_order_cannot_be_drop_shipped(self):
        sale = self.sales_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed sales order"):
            PurchaseOrder.create_for_drop_ship(sale, self.vendor)

    def test_a_non_vendor_is_refused(self):
        sale = self.sales_order()
        with self.assertRaisesMessage(ValidationError, "does not have the Vendor role"):
            PurchaseOrder.create_for_drop_ship(sale, self.customer)

    def test_nothing_left_to_ship_is_refused(self):
        sale = self.sales_order("10")
        order = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        order.confirm()
        self.receive(order, "10")

        with self.assertRaisesMessage(ValidationError, "nothing left"):
            PurchaseOrder.create_for_drop_ship(sale, self.vendor)


class DropShipReceiptTests(DropShipTestCase):
    def drop_shipped(self, quantity="10"):
        sale = self.sales_order(quantity, "10")
        order = PurchaseOrder.create_for_drop_ship(
            sale, self.vendor, order_date=datetime.date(2026, 1, 2)
        )
        order.confirm()
        return sale, order

    def test_no_stock_ever_moves(self):
        """Inventing a movement would create quantity the company never
        held and then relieve it again."""
        sale, order = self.drop_shipped("10")
        before = StockMovement.objects.count()

        self.receive(order, "10")

        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(self.item.on_hand_at(self.warehouse), 0)

    def test_the_cost_goes_straight_to_cost_of_sales(self):
        sale, order = self.drop_shipped("10")

        self.receive(order, "10")

        self.assertEqual(self.balance(self.cogs), Decimal("60"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))
        self.assertEqual(self.balance(self.grni), Decimal("-60"))

    def test_the_customer_line_counts_as_shipped(self):
        """The customer has been shipped whether or not anyone here
        noticed."""
        sale, order = self.drop_shipped("10")

        self.receive(order, "10")

        self.assertEqual(self.sales_line.quantity_shipped(), Decimal("10"))
        self.assertEqual(sale.delivery_status(), FulfilmentStatus.FULL)

    def test_a_drop_ship_delivery_is_marked_as_one(self):
        sale, order = self.drop_shipped("10")
        self.receive(order, "10")

        delivery = Delivery.objects.get(sales_order=sale)
        self.assertTrue(delivery.is_drop_ship)
        self.assertTrue(delivery.posted)

    def test_it_can_then_be_invoiced_on_delivery(self):
        sale, order = self.drop_shipped("10")
        self.receive(order, "10")

        invoice = sale.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 11))
        invoice.post()

        self.assertEqual(invoice.total(), Decimal("100"))
        self.assertEqual(self.balance(self.revenue), Decimal("-100"))

    def test_the_margin_is_right_end_to_end(self):
        sale, order = self.drop_shipped("10")
        self.receive(order, "10")
        sale.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 11)).post()

        # Sold for 100, bought for 60, never held a thing.
        self.assertEqual(-self.balance(self.revenue) - self.balance(self.cogs),
                         Decimal("40"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))

    def test_a_partial_drop_ship_leaves_the_rest_open(self):
        sale, order = self.drop_shipped("10")

        self.receive(order, "4")

        self.assertEqual(self.sales_line.quantity_shipped(), Decimal("4"))
        self.assertEqual(sale.delivery_status(), FulfilmentStatus.PARTIAL)

    def test_billing_the_vendor_clears_the_accrual(self):
        sale, order = self.drop_shipped("10")
        self.receive(order, "10")

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-60"))
