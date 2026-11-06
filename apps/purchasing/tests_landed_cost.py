"""
Landed cost.

Freight that brought goods in is part of what those goods cost. Expense
it and gross margin reads better than it is, permanently — the revenue
carries the sale but the cost of getting the stock here sits somewhere
else on the P&L. Capitalising it is the reason this is different from
the sales-side charge, which really is revenue.

The property that matters: the GL and average_cost() must agree
afterwards, or the next sale posts a COGS that disagrees with the
inventory it relieved.
"""

import datetime
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import (
    Account,
    AccountType,
    ChargeType,
    JournalLine,
)
from apps.core.models import Company
from apps.inventory.models import Warehouse

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class LandedCostTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.freight_expense = Account.objects.create(
            code="5300", name="Inbound Freight", account_type=AccountType.EXPENSE
        )
        self.freight = ChargeType.objects.create(
            code="FRT", name="Inbound shipping",
            expense_account=self.freight_expense, capitalise_into_inventory=True,
        )
        self.plain_freight = ChargeType.objects.create(
            code="FRT2", name="Office courier", expense_account=self.freight_expense
        )
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class LandedCostBasicsTests(LandedCostTestCase):
    def test_it_lands_in_inventory_not_in_expense(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(self.balance(self.inventory), Decimal("80"))
        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))

    def test_the_weighted_average_rises_to_match(self):
        """The whole point: the GL and average_cost() have to agree, or
        the next sale relieves inventory it never had."""
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        order.create_bill(self.payable).post()

        self.assertEqual(self.item.average_cost(), Decimal("8.0000"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("80.00"))
        self.assertEqual(self.balance(self.inventory), self.item.stock_value_at(self.warehouse))

    def test_a_charge_that_does_not_capitalise_still_expenses(self):
        order = self.make_order("10", "5")
        order.add_charge(self.plain_freight, Decimal("30"))
        self.receive(order, "10")
        order.create_bill(self.payable).post()

        self.assertEqual(self.balance(self.freight_expense), Decimal("30"))
        self.assertEqual(self.item.average_cost(), Decimal("5.0000"))

    def test_the_entry_still_balances(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()

        lines = bill.journal_entry.lines.all()
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        self.assertEqual(self.balance(self.payable), Decimal("-80"))

    def test_a_freight_only_bill_expenses_rather_than_being_refused(self):
        """Nothing on the bill to absorb it. Refusing would block a
        legitimate bill; inventing an allocation would be worse."""
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(self.balance(self.freight_expense), Decimal("30"))


class LandedCostAllocationTests(LandedCostTestCase):
    def two_item_order(self):
        from apps.inventory.models import Item

        self.other = Item.objects.create(sku="W2", name="Gadget", uom=self.uom)
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.other, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("15"),
        )
        order.confirm()
        return order

    def receive_all(self, order, warehouse=None):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        for line in order.lines.filter(charge__isnull=True):
            GoodsReceiptLine.objects.create(
                receipt=receipt, order_line=line,
                warehouse=warehouse or self.warehouse, quantity_received=line.quantity,
            )
        receipt.post()
        return receipt

    def test_it_splits_by_value_across_the_goods(self):
        order = self.two_item_order()
        order.add_charge(self.freight, Decimal("40"))
        self.receive_all(order)

        order.create_bill(self.payable).post()

        # 50 and 150 of goods, so the 40 splits 10 / 30.
        self.assertEqual(self.item.average_cost(), Decimal("6.0000"))
        self.assertEqual(self.other.average_cost(), Decimal("18.0000"))

    def test_the_rounding_remainder_does_not_vanish(self):
        order = self.two_item_order()
        order.add_charge(self.freight, Decimal("10"))
        self.receive_all(order)

        order.create_bill(self.payable).post()

        total_value = (
            self.item.stock_value_at(self.warehouse)
            + self.other.stock_value_at(self.warehouse)
        )
        self.assertEqual(total_value, Decimal("210.00"))
        self.assertEqual(self.balance(self.inventory), total_value)

    def test_it_splits_across_the_warehouses_that_received(self):
        """Per-warehouse valuation is a real number here, not a rollup, so
        dumping the whole charge on one site would skew it."""
        second = Warehouse.objects.create(code="WH2", name="South")
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("40"))

        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        line = order.lines.filter(charge__isnull=True).get()
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("6"),
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=second,
            quantity_received=Decimal("4"),
        )
        receipt.post()

        order.create_bill(self.payable).post()

        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("54.00"))
        self.assertEqual(self.item.stock_value_at(second), Decimal("36.00"))
        self.assertEqual(self.item.average_cost(), Decimal("9.0000"))

    def test_several_charges_all_land(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("20"))
        order.add_charge(self.freight, Decimal("10"), description="Customs")
        self.receive(order, "10")

        order.create_bill(self.payable).post()

        self.assertEqual(self.item.average_cost(), Decimal("8.0000"))
        self.assertEqual(self.balance(self.inventory), Decimal("80"))


class LandedCostAndMarginTests(LandedCostTestCase):
    def test_cost_of_sales_carries_the_freight(self):
        """Expensing the freight instead would leave gross margin reading
        better than it is, permanently."""
        from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        order.create_bill(self.payable).post()

        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        cogs = Account.objects.create(
            code="5001", name="COGS", account_type=AccountType.EXPENSE
        )
        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        company = Company.get()
        company.default_cogs_account = cogs
        company.save()

        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 2, 1), currency=self.usd
        )
        sale_line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("12"), revenue_account=revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 2, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=sale_line,
            warehouse=self.warehouse, quantity_shipped=Decimal("10"),
        )
        delivery.post()

        # Sold for 120, cost 80 including the freight that brought it in.
        self.assertEqual(self.balance(cogs), Decimal("80"))
        self.assertEqual(self.balance(self.inventory), Decimal("0"))
