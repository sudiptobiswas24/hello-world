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

        receipt = self.receive(order, "10")

        # Kept on the receipt, so it is returned rather than reversed from the journal.
        self.assertEqual(receipt.journal_entry.lines.get(account=self.cogs).debit, Decimal("60.00"))
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


class WhatIsComingFromTheVendorIsNotShippedTwiceTests(DropShipTestCase):
    """
    An open drop-ship is goods on their way to the customer. Before this
    the shelf was asked to send them too: create_delivery drafted the
    whole line, the order sat on the to-ship list, a second drop-ship
    ordered the same remainder again, and the line held stock for them.
    """

    def stock(self, quantity):
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=self.uom, quantity=Decimal(quantity), unit_cost=Decimal("5"),
            occurred_at=datetime.datetime(2026, 1, 1, 9, tzinfo=datetime.timezone.utc),
        )

    def test_a_second_drop_ship_of_the_same_goods_is_refused(self):
        sale = self.sales_order("10")
        PurchaseOrder.create_for_drop_ship(sale, self.vendor)  # still a draft
        with self.assertRaisesMessage(ValidationError, "already on a drop-ship order"):
            PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        self.assertEqual(PurchaseOrder.objects.filter(drop_ship_for=sale).count(), 1)

    def test_a_drop_ship_line_cannot_be_raised_past_what_is_owed(self):
        sale = self.sales_order("10")
        line = PurchaseOrder.create_for_drop_ship(sale, self.vendor).lines.get()
        line.quantity = Decimal("11")
        with self.assertRaisesMessage(ValidationError, "would bring 11"):
            line.save()
        line.refresh_from_db()
        line.quantity = Decimal("4")
        line.save()
        second = PurchaseOrder.create_for_drop_ship(sale, self.vendor).lines.get()
        self.assertEqual(second.quantity, Decimal("6"))
        second.quantity = Decimal("7")
        with self.assertRaisesMessage(ValidationError, "would bring 7"):
            second.save()

    def test_the_shelf_ships_only_what_the_vendor_is_not_bringing(self):
        from apps.sales.models import orders_to_ship

        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        with self.assertRaisesMessage(ValidationError, "coming from the vendor"):
            sale.create_delivery(warehouse=self.warehouse)
        self.assertEqual(orders_to_ship(SalesOrder.objects.all()), [])

        line = drop.lines.get()
        line.quantity = Decimal("6")
        line.save()
        self.assertEqual(orders_to_ship(SalesOrder.objects.all()), [sale.pk])
        delivery = sale.create_delivery(warehouse=self.warehouse)
        self.assertEqual(delivery.lines.get().quantity_shipped, Decimal("4"))
        delivery.delete()

        drop.cancel()
        self.assertEqual(sale.create_delivery(warehouse=self.warehouse)
                         .lines.get().quantity_shipped, Decimal("10"))

    def test_received_in_part_the_rest_is_still_the_vendors(self):
        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        drop.confirm()
        self.receive(drop, "3")
        self.sales_line.refresh_from_db()
        self.assertEqual(self.sales_line.quantity_open(), Decimal("7"))
        self.assertEqual(self.sales_line.quantity_to_ship(), Decimal("0"))

    def test_the_shelf_stops_holding_stock_for_it_and_holds_it_again_if_called_off(self):
        self.stock("10")
        sale = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 1),
                                         currency=self.usd)
        line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("10"), revenue_account=self.revenue, warehouse=self.warehouse)
        sale.confirm()
        self.assertEqual(line.quantity_reserved(), Decimal("10"))

        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        self.assertEqual(line.quantity_reserved(), Decimal("0"))
        self.assertEqual(sale.reservation_shortfalls(), {})

        drop.cancel()
        self.assertEqual(line.quantity_reserved(), Decimal("10"))

    def test_a_drop_ship_line_delivers_only_its_own_customer_line(self):
        from .models import PurchaseOrderLine

        sale = self.sales_order("10")
        other = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        line = drop.lines.get()
        line.sales_order_line = other.lines.get()
        with self.assertRaisesMessage(ValidationError, "only a drop-ship order raised for it"):
            line.save()
        plain = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 2),
                                             currency=self.usd)
        with self.assertRaisesMessage(ValidationError, "only a drop-ship order raised for it"):
            PurchaseOrderLine.objects.create(order=plain, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                             unit_price=Decimal("6"), sales_order_line=sale.lines.get())
        from apps.inventory.models import Item

        bolt = Item.objects.create(sku="BOLT", name="Bolt", uom=self.uom)
        with self.assertRaisesMessage(ValidationError, "the same item"):
            PurchaseOrderLine.objects.create(order=drop, item=bolt, uom=self.uom, quantity=Decimal("1"),
                                             unit_price=Decimal("6"), sales_order_line=sale.lines.get())


class TheCustomersOrderWaitsForItsDropShipTests(DropShipTestCase):
    """
    A vendor still to send the customer's goods: the sale was cancelled, or its line closed
    short, with the drop-ship left standing. The goods would come all the same, and their
    receipt, delivering to an order that no longer wanted them, could never post; the
    drop-ship stood open with nobody to receive it. Refused until the drop-ship is called off.
    """

    def test_the_sale_is_cancelled_only_once_its_drop_ship_is(self):
        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        drop.confirm()
        with self.assertRaisesMessage(ValidationError, f"A vendor is still to send {self.sales_line.label()} (10)"):
            sale.cancel()
        sale.refresh_from_db()
        self.assertEqual(sale.status, "confirmed")

        drop.cancel()
        sale.cancel()
        sale.refresh_from_db()
        self.assertEqual(sale.status, "cancelled")

    def test_the_line_is_closed_short_only_once_its_drop_ship_is(self):
        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        drop.confirm()
        self.receive(drop, "4")
        with self.assertRaisesMessage(ValidationError, "(6) to the customer on a drop-ship"):
            self.sales_line.close_short("Customer wants no more")
        self.sales_line.refresh_from_db()
        self.assertFalse(self.sales_line.is_closed_short())

        drop.lines.get().close_short("Customer wants no more")
        self.sales_line.close_short("Customer wants no more")
        self.sales_line.refresh_from_db()
        self.assertEqual((self.sales_line.is_closed_short(), self.sales_line.quantity_open()),
                         (True, Decimal("0")))


class ADropShipKeepsWhatItDeliversTests(PurchasingLifecycleTestCase):
    """
    O139: rule A asked only what had moved. A drop-ship on its way had not, so the
    customer's line could change its item before the vendor delivered; and a
    received drop-ship line could be re-pointed at another customer line.
    """

    def drop_ship_fixture(self):
        self.cogs = Account.objects.create(code="5001", name="COGS", account_type=AccountType.EXPENSE)
        self.revenue = Account.objects.create(code="4000", name="Revenue", account_type=AccountType.INCOME)
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.default_purchase_expense_account = self.expense
        company.save()
        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        VendorPrice.objects.create(vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("6"))
        sale = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 1),
                                         currency=self.usd, invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("10"), revenue_account=self.revenue)
        sale.confirm()
        order = PurchaseOrder.create_for_drop_ship(sale, self.vendor, order_date=datetime.date(2026, 1, 2))
        order.confirm()
        return sale, order

    def test_a_line_awaited_from_a_vendor_keeps_its_item(self):
        from apps.inventory.models import Item

        sale, order = self.drop_ship_fixture()
        gadget = Item.objects.create(sku="GDG-1", name="Gadget", uom=self.uom)
        line = sale.lines.get()
        line.item = gadget
        with self.assertRaisesMessage(ValidationError, "has a drop-ship on its way (10); its item can no longer change"):
            line.save()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))
        receipt.post()
        line = SalesOrderLine.objects.get(pk=line.pk)
        self.assertEqual((line.item, line.quantity_shipped()), (self.item, Decimal("10")))

    def test_a_received_drop_ship_line_keeps_the_customer_line_it_delivered(self):
        from .models import PurchaseOrderLine

        sale, order = self.drop_ship_fixture()
        first = sale.lines.get()
        second = SalesOrderLine.objects.create(order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                               unit_price=Decimal("10"), revenue_account=self.revenue)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                         unit_price=Decimal("6"), sales_order_line=second)
        po_line = order.lines.get(sales_order_line=first)
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=po_line, warehouse=self.warehouse,
                                        quantity_received=Decimal("10"))
        receipt.post()
        po_line = PurchaseOrderLine.objects.get(pk=po_line.pk)
        po_line.sales_order_line = second
        with self.assertRaisesMessage(ValidationError, "its sales order line can no longer change"):
            po_line.save()
        GoodsReceipt.objects.get(pk=receipt.pk).create_return(debit_bills=False)
        self.assertEqual((SalesOrderLine.objects.get(pk=first.pk).quantity_shipped(),
                          SalesOrderLine.objects.get(pk=second.pk).quantity_shipped()), (Decimal("0"), Decimal("0")))
