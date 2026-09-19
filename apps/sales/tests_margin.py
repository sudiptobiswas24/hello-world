"""
End-to-end: buy stock, sell it, and check the ledger tells the truth about
gross margin and inventory value. Before COGS posting existed, revenue was
recorded with no cost against it and inventory never appeared on the books.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
)
from apps.inventory.models import Item, Warehouse
from apps.purchasing.models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)

from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine


class BuyAndSellTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.ap = Account.objects.create(code="2000", name="AP", account_type=AccountType.LIABILITY)
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.cogs = Account.objects.create(
            code="5000", name="Cost of Sales", account_type=AccountType.EXPENSE
        )
        Company.objects.create(
            name="Test Co", default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
        )

        self.vendor = Party.objects.create(code="V-1", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def buy(self, quantity, unit_cost):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 2, 1)
        )
        order_line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(unit_cost),
        )
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 2, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse,
            quantity_received=Decimal(quantity),
        )
        receipt.post()

        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 2, 6), payable_account=self.ap
        )
        BillLine.objects.create(
            bill=bill, item=self.item, description="Widgets",
            quantity=Decimal(quantity), unit_price=Decimal(unit_cost),
            expense_account=self.cogs,
        )
        bill.post()
        return order_line

    def sell(self, quantity, unit_price):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        order_line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(unit_price),
            revenue_account=self.revenue,
        )
        order.confirm()

        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 4)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order_line, warehouse=self.warehouse,
            quantity_shipped=Decimal(quantity),
        )
        delivery.post()

        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 5))
        invoice.post()
        return delivery, invoice

    def test_buying_capitalises_stock_instead_of_expensing_it(self):
        self.buy("100", "6")

        self.assertEqual(self.balance(self.inventory), Decimal("600.00"))
        self.assertEqual(self.balance(self.ap), Decimal("-600.00"))  # a liability
        self.assertEqual(self.balance(self.grni), Decimal("0.00"))   # accrual cleared by the bill
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))   # nothing sold yet

    def test_selling_records_revenue_and_its_matching_cost(self):
        self.buy("100", "6")
        self.sell("10", "10")

        self.assertEqual(self.balance(self.revenue), Decimal("-100.00"))  # income
        self.assertEqual(self.balance(self.cogs), Decimal("60.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("540.00"))

    def test_gross_margin_is_now_computable_from_the_ledger(self):
        self.buy("100", "6")
        self.sell("10", "10")

        revenue = -self.balance(self.revenue)
        cost = self.balance(self.cogs)
        self.assertEqual(revenue - cost, Decimal("40.00"))  # 100 sold, 60 cost

    def test_inventory_on_the_books_matches_the_warehouse(self):
        self.buy("100", "6")
        self.sell("10", "10")

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("90"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("540.00"))
        self.assertEqual(self.balance(self.inventory), self.item.stock_value_at(self.warehouse))

    def test_cost_follows_the_blended_average_across_purchase_prices(self):
        self.buy("10", "5")   # 50
        self.buy("10", "7")   # 70 -> average 6
        self.sell("10", "20")

        self.assertEqual(self.balance(self.cogs), Decimal("60.00"))
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("6.0000"))

    def test_a_customer_return_reverses_cost_at_the_price_it_shipped_at(self):
        self.buy("10", "5")
        delivery, _ = self.sell("10", "20")
        self.assertEqual(self.balance(self.cogs), Decimal("50.00"))

        # Restock cheaper, then take the original goods back.
        self.buy("10", "1")
        delivery.create_return()

        # The return reverses the 5.00 it shipped at, not the new 1.00 average.
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("20"))

    def test_the_whole_cycle_leaves_the_ledger_balanced(self):
        self.buy("100", "6")
        self.sell("10", "10")

        totals = JournalLine.objects.filter(entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        self.assertEqual(totals["debit"], totals["credit"])
