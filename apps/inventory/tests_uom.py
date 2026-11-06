"""
One ledger, one unit.

`UnitOfMeasure.to_base_quantity()` existed, was tested, and was called by
nothing. Every document line carried a `uom` and no document converted,
so ten cases of twelve booked ten units at sixty each instead of a
hundred and twenty at five — quantity and valuation both wrong, and
nothing said so. A widget counted in eaches could be ordered by the
kilogram.

These tests pin the arithmetic in both directions and the refusals that
keep it honest.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
)
from apps.sales.models import (
    Delivery,
    DeliveryLine,
    SalesOrder,
    SalesOrderLine,
)

from .models import Item, MovementType, StockMovement, Warehouse


class UomTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        self.case = UnitOfMeasure.objects.create(
            code="case", name="Case of 12", category=UnitOfMeasureCategory.COUNT,
            base_unit=self.each, conversion_factor=Decimal("12"),
        )
        self.pallet = UnitOfMeasure.objects.create(
            code="pal", name="Pallet of 10 cases", category=UnitOfMeasureCategory.COUNT,
            base_unit=self.case, conversion_factor=Decimal("10"),
        )
        self.kilogram = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.warehouse = Warehouse.objects.create(code="W", name="Main")
        self.vendor = Party.objects.create(code="V-1", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def buy(self, quantity, price, uom=None, receive=True):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=uom or self.each,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        order.confirm()
        if not receive:
            return order, line
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        return order, line


class ConversionTests(UomTestCase):
    def test_a_unit_converts_to_one_further_down_its_chain(self):
        self.assertEqual(self.case.convert_to(Decimal("10"), self.each), Decimal("120"))

    def test_conversion_walks_the_whole_chain(self):
        self.assertEqual(
            self.pallet.convert_to(Decimal("2"), self.each), Decimal("240")
        )

    def test_a_unit_converts_upwards_too(self):
        self.assertEqual(self.each.convert_to(Decimal("120"), self.case), Decimal("10"))

    def test_converting_to_itself_changes_nothing(self):
        self.assertEqual(self.case.convert_to(Decimal("7"), self.case), Decimal("7"))

    def test_unrelated_measures_are_refused_rather_than_guessed_at(self):
        with self.assertRaises(ValidationError) as caught:
            self.case.convert_to(Decimal("1"), self.kilogram)
        self.assertIn("not the same kind of measure", str(caught.exception))

    def test_a_zero_factor_cannot_be_stored(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            UnitOfMeasure.objects.create(
                code="bad", name="Bad", category=UnitOfMeasureCategory.COUNT,
                base_unit=self.each, conversion_factor=Decimal("0"),
            )


class LedgerSpeaksOneUnitTests(UomTestCase):
    def test_ten_cases_of_twelve_is_a_hundred_and_twenty_units(self):
        self.buy("10", "60", uom=self.case)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("120"))

    def test_the_cost_per_unit_follows_the_quantity(self):
        self.buy("10", "60", uom=self.case)
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))

    def test_total_value_is_what_the_vendor_charged(self):
        self.buy("10", "60", uom=self.case)
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("600.00"))

    def test_the_ledger_holds_value_not_the_divided_price(self):
        # 7 does not divide evenly by 12, so a factor-divided unit cost
        # would round away from the 700 the vendor actually charged.
        self.buy("100", "7", uom=self.case)
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("700.00"))

    def test_the_stock_ledger_agrees_with_the_inventory_account(self):
        # The only assertion that really matters: whatever the conversion
        # and its rounding do, the two records of what the stock is worth
        # have to be the same number.
        self.buy("100", "7", uom=self.case)
        self.buy("3", "29", uom=self.pallet)
        entries = JournalLine.objects.filter(account=self.inventory)
        balance = sum(
            (line.debit - line.credit for line in entries), Decimal("0")
        )
        self.assertEqual(balance, self.item.stock_value_at(self.warehouse))

    def test_a_case_bought_and_eaches_sold_agree(self):
        self.buy("10", "60", uom=self.case)
        sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 2, 1),
            currency=self.usd,
        )
        line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.each, quantity=Decimal("30"),
            unit_price=Decimal("9"), revenue_account=self.revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 2, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.warehouse,
            quantity_shipped=Decimal("30"),
        )
        delivery.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("90"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("450.00"))

    def test_a_sale_in_cases_relieves_twelve_times_the_stock(self):
        self.buy("240", "5")
        sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 2, 1),
            currency=self.usd,
        )
        line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.case, quantity=Decimal("5"),
            unit_price=Decimal("120"), revenue_account=self.revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 2, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.warehouse,
            quantity_shipped=Decimal("5"),
        )
        delivery.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("180"))

    def test_the_document_quantity_is_kept_as_written(self):
        _order, line = self.buy("10", "60", uom=self.case)
        movement = StockMovement.objects.get(item=self.item)
        self.assertEqual(movement.document_quantity, Decimal("10.0000"))
        self.assertEqual(movement.uom, self.case)
        self.assertEqual(movement.quantity, Decimal("120.0000"))

    def test_shipping_more_than_is_on_hand_is_measured_in_the_same_unit(self):
        # 5 cases is 60 eaches and only 30 are here. Comparing 5 against 30
        # as written would let it through.
        self.buy("30", "5")
        sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 2, 1),
            currency=self.usd,
        )
        line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.case, quantity=Decimal("5"),
            unit_price=Decimal("120"), revenue_account=self.revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 2, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.warehouse,
            quantity_shipped=Decimal("5"),
        )
        with self.assertRaises(ValidationError) as caught:
            delivery.post()
        self.assertIn("on hand", str(caught.exception))


class WrongUnitIsRefusedEarlyTests(UomTestCase):
    def test_a_counted_item_cannot_be_purchased_by_weight(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        with self.assertRaises(ValidationError) as caught:
            PurchaseOrderLine.objects.create(
                order=order, item=self.item, uom=self.kilogram,
                quantity=Decimal("10"), unit_price=Decimal("6"),
            )
        self.assertIn("not the same kind of measure", str(caught.exception))

    def test_a_counted_item_cannot_be_sold_by_weight(self):
        sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 2, 1),
            currency=self.usd,
        )
        with self.assertRaises(ValidationError) as caught:
            SalesOrderLine.objects.create(
                order=sale, item=self.item, uom=self.kilogram,
                quantity=Decimal("10"), unit_price=Decimal("9"),
                revenue_account=self.revenue,
            )
        self.assertIn("not the same kind of measure", str(caught.exception))

    def test_a_movement_must_say_which_unit_it_counts_in(self):
        with self.assertRaises(ValidationError) as caught:
            StockMovement.objects.create(
                item=self.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("5"),
                unit_cost=Decimal("4"), occurred_at=timezone.now(),
            )
        self.assertIn("must say which unit", str(caught.exception))

    def test_a_subcontract_line_must_use_the_unit_its_components_assume(self):
        component = Item.objects.create(sku="C", name="Part", uom=self.each)
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.case,
            quantity=Decimal("10"), unit_price=Decimal("60"),
        )
        with self.assertRaises(ValidationError) as caught:
            SubcontractComponent.objects.create(
                order_line=line, item=component, quantity_per=Decimal("2")
            )
        self.assertIn("components are specified per", str(caught.exception))
