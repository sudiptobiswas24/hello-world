"""
Specific identification.

Each batch keeps its own cost, and what leaves costs what *that* batch
cost. It is the method for goods that are individually identifiable and
individually priced — a serial-numbered machine, a numbered artwork, a
vintage — where an average across the shelf describes nothing that was
ever bought or sold.

Only available to an item tracked by lot or serial number, because the
method's entire premise is knowing which physical goods left. Within one
batch the cost is still an average: a batch that arrives twice at two
prices has two prices and one identity. For serial tracking that average
is over one unit, which is the case the method is really for.
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
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .models import (
    AdjustmentReason,
    CostingMethod,
    Item,
    Lot,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    TrackingMode,
    Warehouse,
)


class SpecificCostingTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.shrinkage = acc("6200", "Shrinkage", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
        )
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.item = Item.objects.create(
            sku="ART", name="Print", uom=self.each,
            tracking=TrackingMode.LOT, costing_method=CostingMethod.SPECIFIC,
        )
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def lot(self, code, expires=None):
        return Lot.objects.create(
            item=self.item, code=code,
            expires_on=datetime.date.fromisoformat(expires) if expires else None,
        )

    def stock(self, lot, quantity, cost, warehouse=None):
        return StockMovement.objects.create(
            item=self.item, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=self.each, lot=lot,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def ship(self, quantity, lot=None, on=datetime.date(2026, 6, 1)):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=on, currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.each, quantity=Decimal(quantity),
            unit_price=Decimal("500"), revenue_account=self.revenue,
        )
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=on)
        delivery_line = DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.north,
            lot=lot, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery, delivery_line

    def balance(self, account):
        return sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )


class EachBatchKeepsItsOwnCostTests(SpecificCostingTestCase):
    def test_what_leaves_costs_what_that_batch_cost(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("5"), lot=cheap),
            Decimal("500"),
        )
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("5"), lot=dear),
            Decimal("4500"),
        )

    def test_the_average_would_have_said_something_else_entirely(self):
        # 100 and 900 average to 500. Neither batch cost that, and
        # nothing was ever bought or sold at it.
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        averaged = Item.objects.create(
            sku="AVG", name="Averaged", uom=self.each,
            tracking=TrackingMode.LOT, costing_method=CostingMethod.AVERAGE,
        )
        for code, cost in (("A", "100"), ("B", "900")):
            StockMovement.objects.create(
                item=averaged, warehouse=self.north,
                movement_type=MovementType.RECEIPT, uom=self.each,
                lot=Lot.objects.create(item=averaged, code=code),
                quantity=Decimal("10"), unit_cost=Decimal(cost),
                occurred_at=timezone.now(),
            )
        self.assertEqual(
            averaged.cost_of_removing(self.north, Decimal("5")), Decimal("2500")
        )

    def test_the_shelf_is_worth_every_batch_added_up(self):
        self.stock(self.lot("CHEAP"), "10", "100")
        self.stock(self.lot("DEAR"), "10", "900")
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("10000.00"))

    def test_a_batch_arriving_twice_averages_within_itself(self):
        # A batch that arrives twice at two prices has two prices and one
        # identity; there is nothing else it can mean.
        batch = self.lot("B1")
        self.stock(batch, "10", "100")
        self.stock(batch, "10", "200")
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("1"), lot=batch),
            Decimal("150"),
        )

    def test_a_serial_number_is_exact(self):
        serial = Item.objects.create(
            sku="M", name="Machine", uom=self.each,
            tracking=TrackingMode.SERIAL, costing_method=CostingMethod.SPECIFIC,
        )
        first = Lot.objects.create(item=serial, code="SN-1")
        second = Lot.objects.create(item=serial, code="SN-2")
        for unit, cost in ((first, "1200"), (second, "1750")):
            StockMovement.objects.create(
                item=serial, warehouse=self.north,
                movement_type=MovementType.RECEIPT, uom=self.each, lot=unit,
                quantity=Decimal("1"), unit_cost=Decimal(cost),
                occurred_at=timezone.now(),
            )
        self.assertEqual(
            serial.cost_of_removing(self.north, Decimal("1"), lot=second),
            Decimal("1750"),
        )
        self.assertEqual(serial.stock_value_at(self.north), Decimal("2950.00"))

    def test_taking_from_one_batch_leaves_the_others_alone(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        StockMovement.objects.create(
            item=self.item, warehouse=self.north, movement_type=MovementType.ISSUE,
            uom=self.each, lot=cheap, quantity=Decimal("-10"),
            unit_cost=Decimal("100"), occurred_at=timezone.now(),
        )
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("9000.00"))
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("1"), lot=dear),
            Decimal("900"),
        )


class ItCannotGuessTests(SpecificCostingTestCase):
    def test_it_refuses_to_price_a_withdrawal_with_no_batch(self):
        # Guessing would be weighted average wearing another name, and
        # wrong by exactly the amount the method exists to get right.
        self.stock(self.lot("B1"), "10", "100")
        with self.assertRaises(ValidationError) as caught:
            self.item.cost_of_removing(self.north, Decimal("1"))
        self.assertIn("Say which", str(caught.exception))

    def test_an_untracked_item_cannot_be_costed_this_way(self):
        with self.assertRaises(ValidationError) as caught:
            Item.objects.create(
                sku="X", name="Untracked", uom=self.each,
                costing_method=CostingMethod.SPECIFIC,
            )
        self.assertIn("which batch left", str(caught.exception))

    def test_tracking_cannot_be_turned_off_underneath_it(self):
        with self.assertRaises(ValidationError):
            self.item.tracking = TrackingMode.NONE
            self.item.save()

    def test_going_below_zero_prices_at_what_the_batch_last_cost(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        batch = self.lot("B1")
        self.stock(batch, "5", "700", warehouse=backorders)
        self.assertEqual(
            self.item.cost_of_removing(backorders, Decimal("8"), lot=batch),
            Decimal("5600"),
        )


class ShippingUnderSpecificCostingTests(SpecificCostingTestCase):
    def test_cost_of_sales_is_what_the_chosen_batch_cost(self):
        cheap, dear = self.lot("CHEAP", "2026-12-01"), self.lot("DEAR", "2027-12-01")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        self.ship("4", lot=dear)
        self.assertEqual(self.balance(self.cogs), Decimal("3600.00"))

    def test_an_auto_allocated_shipment_is_costed_batch_by_batch(self):
        # First expired first out picks the batches; specific
        # identification prices each at its own cost, not at a blend.
        sooner, later = self.lot("SOONER", "2026-08-01"), self.lot("LATER", "2026-12-01")
        self.stock(sooner, "3", "100")
        self.stock(later, "10", "900")
        self.ship("5")
        # Three at 100, two at 900.
        self.assertEqual(self.balance(self.cogs), Decimal("2100.00"))

    def test_the_ledger_and_the_shelf_still_agree(self):
        sooner, later = self.lot("SOONER", "2026-08-01"), self.lot("LATER", "2026-12-01")
        self.stock(sooner, "3", "100")
        self.stock(later, "10", "900")
        before = self.item.stock_value_at(self.north)
        self.ship("5")
        after = self.item.stock_value_at(self.north)
        self.assertEqual(before - after, self.balance(self.cogs))

    def test_a_return_gives_back_what_the_shipment_took(self):
        sooner, later = self.lot("SOONER", "2026-08-01"), self.lot("LATER", "2026-12-01")
        self.stock(sooner, "3", "100")
        self.stock(later, "10", "900")
        before = self.item.stock_value_at(self.north)
        delivery, _line = self.ship("5")
        delivery.create_return()
        self.assertEqual(self.item.stock_value_at(self.north), before)
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))

    def test_the_returned_goods_go_back_to_their_own_batches(self):
        sooner, later = self.lot("SOONER", "2026-08-01"), self.lot("LATER", "2026-12-01")
        self.stock(sooner, "3", "100")
        self.stock(later, "10", "900")
        delivery, _line = self.ship("5")
        delivery.create_return()
        self.assertEqual(sooner.on_hand_at(self.north), Decimal("3"))
        self.assertEqual(later.on_hand_at(self.north), Decimal("10"))


class AdjustingAndMovingTests(SpecificCostingTestCase):
    def test_a_write_off_costs_the_batch_it_destroyed(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each, lot=dear,
            quantity=Decimal("-2"),
        )
        adjustment.post()
        self.assertEqual(self.balance(self.shrinkage), Decimal("1800.00"))
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("8200.00"))

    def test_voiding_that_write_off_puts_the_same_value_back(self):
        dear = self.lot("DEAR")
        self.stock(self.lot("CHEAP"), "10", "100")
        self.stock(dear, "10", "900")
        before = self.item.stock_value_at(self.north)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each, lot=dear,
            quantity=Decimal("-2"),
        )
        adjustment.post()
        adjustment.void()
        self.assertEqual(self.item.stock_value_at(self.north), before)
        self.assertEqual(self.balance(self.shrinkage), Decimal("0.00"))

    def test_a_transfer_carries_the_batchs_own_cost(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 6, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, lot=dear,
            quantity=Decimal("4"),
        )
        move.post()
        self.assertEqual(self.item.stock_value_at(self.south), Decimal("3600.00"))
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("6400.00"))

    def test_cancelling_that_transfer_leaves_the_total_alone(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        before = sum(
            (self.item.valuation_at(w)[1] for w in Warehouse.objects.all()),
            Decimal("0"),
        )
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 6, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, lot=dear,
            quantity=Decimal("4"),
        )
        move.post()
        move.cancel()
        after = sum(
            (self.item.valuation_at(w)[1] for w in Warehouse.objects.all()),
            Decimal("0"),
        )
        self.assertEqual(after, before)
        self.assertEqual(dear.on_hand_at(self.north), Decimal("10"))

    def test_a_landed_cost_raises_only_the_batch_it_was_incurred_on(self):
        cheap, dear = self.lot("CHEAP"), self.lot("DEAR")
        arrival = self.stock(cheap, "10", "100")
        self.stock(dear, "10", "900")
        StockMovement.objects.create(
            item=self.item, warehouse=self.north,
            movement_type=MovementType.ADJUSTMENT, uom=self.each, lot=cheap,
            quantity=Decimal("0"), value_adjustment=Decimal("500"),
            adjusts=arrival, occurred_at=timezone.now(),
        )
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("1"), lot=cheap),
            Decimal("150"),
        )
        self.assertEqual(
            self.item.cost_of_removing(self.north, Decimal("1"), lot=dear),
            Decimal("900"),
        )


class ReportingTests(SpecificCostingTestCase):
    def test_the_subledger_still_foots_to_the_ledger(self):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )
        from .models import reconcile_to_ledger

        company = Company.get()
        company.grni_account = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        company.default_purchase_expense_account = Account.objects.create(
            code="5300", name="Purchases", account_type=AccountType.EXPENSE
        )
        company.save()
        vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)

        batch = self.lot("B1", "2027-01-01")
        order = PurchaseOrder.objects.create(
            vendor=vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.each,
            quantity=Decimal("10"), unit_price=Decimal("120"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.north,
            lot=batch, quantity_received=Decimal("10"),
        )
        receipt.post()
        self.ship("4")
        report = reconcile_to_ledger()
        self.assertTrue(report["balanced"], report["rows"])
