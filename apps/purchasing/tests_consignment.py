"""
Consignment stock.

The vendor's goods standing on the company's floor. On the premises,
not on the books — booking them would put the vendor's inventory on the
company's balance sheet and accrue a bill nobody owes yet.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Warehouse
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    VendorPrice,
    consignment_on_hand,
    draw_consignment,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class ConsignmentTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.store = Warehouse.objects.create(
            code="CON", name="Vendor stock", consignment_vendor=self.vendor
        )
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("4")
        )

    def deliver(self, quantity="100"):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal("4"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.store,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        return order, receipt

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class ConsignmentDeliveryTests(ConsignmentTestCase):
    def test_delivered_stock_is_present_but_not_owned(self):
        self.deliver("100")

        self.assertEqual(self.item.on_hand_at(self.store), Decimal("100"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_it_is_not_available_to_ship(self):
        self.deliver("100")
        self.assertEqual(self.item.available_at(self.store), 0)

    def test_it_carries_no_value(self):
        self.deliver("100")
        self.assertEqual(self.item.stock_value_at(self.store), Decimal("0.00"))

    def test_the_report_shows_what_is_being_held(self):
        self.deliver("100")
        rows = consignment_on_hand()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vendor"], self.vendor)
        self.assertEqual(rows[0]["quantity"], Decimal("100"))

    def test_it_can_be_sent_back_without_a_ledger_entry(self):
        order, receipt = self.deliver("100")

        receipt.create_return()

        self.assertEqual(self.item.on_hand_at(self.store), Decimal("0"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))


class ShippingConsignmentTests(ConsignmentTestCase):
    def test_nothing_ships_straight_off_consignment(self):
        self.deliver("100")
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 1, 6), currency=self.usd
        )
        sale_line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("9"), revenue_account=revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 1, 7)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=sale_line,
            warehouse=self.store, quantity_shipped=Decimal("10"),
        )

        with self.assertRaisesMessage(ValidationError, "draw it into your own"):
            delivery.post()


class DrawTests(ConsignmentTestCase):
    def test_drawing_takes_ownership_and_raises_the_bill(self):
        """Both facts land together, or the stock appears without a
        liability or the liability without the stock."""
        self.deliver("100")

        order, bill = draw_consignment(
            self.item, self.store, self.warehouse, Decimal("30"),
            self.payable, on_date=datetime.date(2026, 2, 1),
        )

        self.assertEqual(self.item.on_hand_at(self.store), Decimal("70"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("30"))
        self.assertEqual(bill.total(), Decimal("120"))
        self.assertEqual(self.balance(self.payable), Decimal("-120"))

    def test_the_drawn_stock_is_valued(self):
        self.deliver("100")
        draw_consignment(
            self.item, self.store, self.warehouse, Decimal("30"),
            self.payable, on_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("120.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("120"))

    def test_the_accrual_clears(self):
        self.deliver("100")
        draw_consignment(
            self.item, self.store, self.warehouse, Decimal("30"),
            self.payable, on_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_it_takes_the_agreed_price(self):
        self.deliver("100")
        order, bill = draw_consignment(
            self.item, self.store, self.warehouse, Decimal("10"),
            self.payable, on_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(order.lines.get().unit_price, Decimal("4"))

    def test_an_explicit_price_wins(self):
        self.deliver("100")
        order, bill = draw_consignment(
            self.item, self.store, self.warehouse, Decimal("10"),
            self.payable, unit_price=Decimal("6"), on_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(bill.total(), Decimal("60"))

    def test_drawing_more_than_is_there_is_refused(self):
        self.deliver("100")
        with self.assertRaisesMessage(ValidationError, "on consignment at"):
            draw_consignment(
                self.item, self.store, self.warehouse, Decimal("150"), self.payable
            )

    def test_an_ordinary_warehouse_holds_no_consignment(self):
        with self.assertRaisesMessage(ValidationError, "does not hold consignment stock"):
            draw_consignment(
                self.item, self.warehouse, self.store, Decimal("1"), self.payable
            )

    def test_drawing_into_consignment_owns_nothing(self):
        second = Warehouse.objects.create(
            code="CON2", name="Other", consignment_vendor=self.vendor
        )
        self.deliver("100")
        with self.assertRaisesMessage(ValidationError, "owns nothing"):
            draw_consignment(self.item, self.store, second, Decimal("1"), self.payable)

    def test_no_agreed_price_is_refused_rather_than_guessed(self):
        VendorPrice.objects.all().delete()
        self.deliver("100")
        with self.assertRaisesMessage(ValidationError, "a price nobody has stated"):
            draw_consignment(
                self.item, self.store, self.warehouse, Decimal("10"), self.payable
            )

    def test_drawn_stock_can_then_be_shipped(self):
        self.deliver("100")
        draw_consignment(
            self.item, self.store, self.warehouse, Decimal("30"),
            self.payable, on_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("30"))
