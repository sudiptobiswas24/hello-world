"""
FIFO and standard cost.

Weighted average was the only answer. It is a good default — no layer
bookkeeping, and it cannot be gamed by choosing which physical unit to
ship — but a company reporting FIFO, or running to a standard with
variances analysed monthly, cannot use it and cannot bolt either on
later without restating every period.

The assertion that matters in every one of these: whatever the method,
the inventory account and stock_value_at() are the same number. A
costing method that gets the shelf right and the ledger wrong is worse
than no costing method, because it looks right.
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

from .models import (
    AdjustmentReason,
    CostingMethod,
    Item,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    Warehouse,
    set_standard_cost,
)


class CostingTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.variance = acc("5100", "Purchase price variance", AccountType.EXPENSE)
        self.revaluation = acc("5200", "Stock revaluation", AccountType.EXPENSE)
        self.purchases = acc("5300", "Purchases", AccountType.EXPENSE)
        company = Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
            grni_account=self.grni, purchase_price_variance_account=self.variance,
            default_purchase_expense_account=self.purchases,
        )
        self.warehouse = Warehouse.objects.create(code="W", name="Main")
        self.other = Warehouse.objects.create(code="O", name="Other")
        self.vendor = Party.objects.create(code="V-1", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.reason = AdjustmentReason.objects.create(
            code="REVAL", name="Revaluation", account=self.revaluation
        )

    def item(self, method, sku="W", standard=None):
        return Item.objects.create(
            sku=sku, name="Widget", uom=self.each,
            costing_method=method, standard_cost=standard,
        )

    def receive(self, item, quantity, cost, warehouse=None):
        return StockMovement.objects.create(
            item=item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, uom=item.uom,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def issue(self, item, quantity, warehouse=None):
        warehouse = warehouse or self.warehouse
        quantity = Decimal(quantity)
        return StockMovement.objects.create(
            item=item, warehouse=warehouse, movement_type=MovementType.ISSUE,
            uom=item.uom, quantity=-quantity,
            unit_cost=item.removal_unit_cost(warehouse, quantity),
            occurred_at=timezone.now(),
        )

    def balance(self, account):
        return sum(
            (line.debit - line.credit for line in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )


class AverageIsUnchangedTests(CostingTestCase):
    def test_the_default_is_weighted_average(self):
        self.assertEqual(self.item(CostingMethod.AVERAGE).costing_method, "average")
        self.assertEqual(Item.objects.create(sku="X", name="X", uom=self.each).costing_method,
                         "average")

    def test_it_still_blends_every_receipt(self):
        widget = self.item(CostingMethod.AVERAGE)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        self.assertEqual(widget.average_cost_at(self.warehouse), Decimal("5.0000"))
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("50")), Decimal("250")
        )


class FifoTests(CostingTestCase):
    def test_the_oldest_layer_goes_first(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        # Average would say 250. FIFO says the first fifty cost four.
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("50")), Decimal("200")
        )

    def test_a_withdrawal_spanning_layers_pays_both_prices(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        # A hundred at four, then fifty at six.
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("150")), Decimal("700")
        )

    def test_what_is_left_is_worth_the_newer_layers(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        self.issue(widget, "100")
        self.assertEqual(widget.on_hand_at(self.warehouse), Decimal("100"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("600.00"))

    def test_the_two_methods_really_do_differ(self):
        fifo = self.item(CostingMethod.FIFO, sku="F")
        average = self.item(CostingMethod.AVERAGE, sku="A")
        for item in (fifo, average):
            self.receive(item, "100", "4")
            self.receive(item, "100", "6")
            self.issue(item, "100")
        self.assertEqual(fifo.stock_value_at(self.warehouse), Decimal("600.00"))
        self.assertEqual(average.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_layers_are_consumed_in_arrival_order_not_price_order(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "10", "10")
        self.receive(widget, "10", "1")
        self.receive(widget, "10", "5")
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("15")), Decimal("105")
        )

    def test_shipping_below_zero_prices_at_the_last_known_cost(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "10", "7", warehouse=backorders)
        self.assertEqual(
            widget.cost_of_removing(backorders, Decimal("15")), Decimal("105")
        )

    def test_a_transfer_moves_the_layer_cost_not_an_average(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.warehouse, to_warehouse=self.other,
        )
        StockTransferLine.objects.create(
            transfer=move, item=widget, uom=self.each, quantity=Decimal("50")
        )
        move.post()
        self.assertEqual(widget.stock_value_at(self.other), Decimal("200.00"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("800.00"))

    def test_a_write_off_is_costed_from_the_oldest_layer(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=widget, uom=self.each, quantity=Decimal("-50")
        )
        adjustment.post()
        self.assertEqual(self.balance(self.revaluation), Decimal("200.00"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("800.00"))

    def test_voiding_that_write_off_puts_the_same_value_back(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        before = widget.stock_value_at(self.warehouse)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=widget, uom=self.each, quantity=Decimal("-50")
        )
        adjustment.post()
        adjustment.void()
        self.assertEqual(widget.stock_value_at(self.warehouse), before)
        self.assertEqual(self.balance(self.revaluation), Decimal("0.00"))


class StandardCostTests(CostingTestCase):
    def test_the_shelf_is_worth_quantity_times_the_standard(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("1000.00"))

    def test_what_was_paid_does_not_touch_the_shelf(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "9")
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_what_leaves_costs_the_standard(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "9")
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("30")), Decimal("150")
        )

    def test_a_receipt_throws_the_difference_to_variance(self):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=widget, uom=self.each,
            quantity=Decimal("100"), unit_price=Decimal("6"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("100"),
        )
        receipt.post()
        # The shelf took 500, the vendor is owed 600, and the 100 is a
        # variance for somebody to explain rather than an inflated asset.
        self.assertEqual(self.balance(self.inventory), Decimal("500.00"))
        self.assertEqual(self.balance(self.grni), Decimal("-600.00"))
        self.assertEqual(self.balance(self.variance), Decimal("100.00"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_the_ledger_and_the_shelf_agree_after_a_standard_receipt(self):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=widget, uom=self.each,
            quantity=Decimal("40"), unit_price=Decimal("3"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("40"),
        )
        receipt.post()
        # Bought under standard, so the variance is a credit.
        self.assertEqual(self.balance(self.variance), Decimal("-80.00"))
        self.assertEqual(
            self.balance(self.inventory), widget.stock_value_at(self.warehouse)
        )


class ChangingTheStandardTests(CostingTestCase):
    def test_it_revalues_the_stock_on_hand(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        set_standard_cost(widget, Decimal("7"), reason=self.reason)
        widget.refresh_from_db()
        self.assertEqual(widget.standard_cost, Decimal("7.0000"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("700.00"))

    def test_the_revaluation_is_posted_not_assumed(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        set_standard_cost(widget, Decimal("7"), reason=self.reason)
        self.assertEqual(self.balance(self.inventory), Decimal("200.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("-200.00"))

    def test_it_revalues_every_shelf(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        self.receive(widget, "40", "5", warehouse=self.other)
        raised = set_standard_cost(widget, Decimal("6"), reason=self.reason)
        self.assertEqual(len(raised), 2)
        self.assertEqual(self.balance(self.inventory), Decimal("140.00"))

    def test_a_revaluation_moves_value_and_no_quantity(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        set_standard_cost(widget, Decimal("7"), reason=self.reason)
        self.assertEqual(widget.on_hand_at(self.warehouse), Decimal("100"))

    def test_a_revaluation_cannot_be_voided_on_its_own(self):
        """
        Found by an audit probe, not by the suite.

        Voiding it reversed the ledger and left standard_cost where it
        was, so the shelf stayed valued at the new figure with nothing
        posted behind it — ledger -60 against a shelf of 800. The
        revaluation is a consequence of the standard rather than an event
        of its own, and the way to undo it is to set the standard back.
        """
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        raised = set_standard_cost(widget, Decimal("8"), reason=self.reason)
        with self.assertRaises(ValidationError) as caught:
            raised[0].void()
        self.assertIn("Set the standard back", str(caught.exception))

    def test_setting_the_standard_back_unwinds_both_records(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        set_standard_cost(widget, Decimal("8"), reason=self.reason)
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("800.00"))
        set_standard_cost(widget, Decimal("5"), reason=self.reason)
        self.assertEqual(self.balance(self.inventory), Decimal("0.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("0.00"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_an_ordinary_revaluation_can_still_be_voided(self):
        """
        The refusal is specific to a standard-cost revaluation, not to
        revaluations in general — and writing this turned up a second
        defect the probe had missed.

        reverse() negated the line's quantity, which on a value-only line
        is zero, and wrote a movement carrying nothing. The ledger
        reversal landed and the shelf kept the revaluation: 400 against a
        ledger of 500, permanently.
        """
        widget = self.item(CostingMethod.AVERAGE)
        self.receive(widget, "100", "5")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=widget, uom=self.each,
            quantity=Decimal("0"), revaluation=Decimal("-100"),
        )
        adjustment.post()
        adjustment.void()
        self.assertEqual(self.balance(self.inventory), Decimal("0.00"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_an_empty_shelf_needs_no_revaluation(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.assertEqual(set_standard_cost(widget, Decimal("7"), reason=self.reason), [])
        widget.refresh_from_db()
        self.assertEqual(widget.standard_cost, Decimal("7.0000"))

    def test_an_averaged_item_has_no_standard_to_change(self):
        widget = self.item(CostingMethod.AVERAGE)
        with self.assertRaises(ValidationError) as caught:
            set_standard_cost(widget, Decimal("7"), reason=self.reason)
        self.assertIn("no standard to change", str(caught.exception))

    def test_revaluing_without_saying_where_it_posts_is_refused(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        self.receive(widget, "100", "5")
        with self.assertRaises(ValidationError) as caught:
            set_standard_cost(widget, Decimal("7"))
        self.assertIn("which reason account", str(caught.exception))

    def test_a_standard_must_be_positive(self):
        widget = self.item(CostingMethod.STANDARD, standard=Decimal("5"))
        with self.assertRaises(ValidationError):
            set_standard_cost(widget, Decimal("0"), reason=self.reason)


class RevaluationLineTests(CostingTestCase):
    def test_a_line_carries_quantity_or_value_and_not_both(self):
        from django.db.utils import IntegrityError

        widget = self.item(CostingMethod.AVERAGE)
        self.receive(widget, "100", "5")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        with self.assertRaises(IntegrityError):
            StockAdjustmentLine.objects.create(
                adjustment=adjustment, item=widget, uom=self.each,
                quantity=Decimal("-5"), revaluation=Decimal("10"),
            )

    def test_a_line_that_does_nothing_is_not_a_line(self):
        from django.db.utils import IntegrityError

        widget = self.item(CostingMethod.AVERAGE)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        with self.assertRaises(IntegrityError):
            StockAdjustmentLine.objects.create(
                adjustment=adjustment, item=widget, uom=self.each, quantity=Decimal("0")
            )

    def test_a_write_down_to_net_realisable_value_is_a_revaluation(self):
        widget = self.item(CostingMethod.AVERAGE)
        self.receive(widget, "100", "5")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.warehouse, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=widget, uom=self.each,
            quantity=Decimal("0"), revaluation=Decimal("-120"),
        )
        adjustment.post()
        self.assertEqual(widget.on_hand_at(self.warehouse), Decimal("100"))
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("380.00"))
        self.assertEqual(self.balance(self.revaluation), Decimal("120.00"))


class LandedCostUnderFifoTests(CostingTestCase):
    """
    A landed cost belongs to the goods it was incurred on.

    Under average it makes no difference where it lands — the shelf has
    one price. Under FIFO it decides which layer it raises, and spreading
    it over the shelf would misprice everything that was already standing
    there and then ship that mispricing out in arrival order.
    """

    def test_it_raises_the_layer_it_was_incurred_on(self):
        widget = self.item(CostingMethod.FIFO)
        old = self.receive(widget, "100", "4")
        new = self.receive(widget, "100", "6")
        StockMovement.objects.create(
            item=widget, warehouse=self.warehouse,
            movement_type=MovementType.ADJUSTMENT, uom=self.each,
            quantity=Decimal("0"), value_adjustment=Decimal("200"),
            adjusts=new, occurred_at=timezone.now(),
        )
        # The older layer is untouched, so the first hundred still cost 4.
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("100")), Decimal("400")
        )
        # The freight went on the newer one: 600 plus 200 over a hundred.
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("200")), Decimal("1200")
        )

    def test_an_untargeted_adjustment_spreads_over_what_is_left(self):
        widget = self.item(CostingMethod.FIFO)
        self.receive(widget, "100", "4")
        self.receive(widget, "100", "6")
        StockMovement.objects.create(
            item=widget, warehouse=self.warehouse,
            movement_type=MovementType.ADJUSTMENT, uom=self.each,
            quantity=Decimal("0"), value_adjustment=Decimal("100"),
            occurred_at=timezone.now(),
        )
        # 1000 on the shelf becomes 1100, split 400:600 by value.
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("1100.00"))
        self.assertEqual(
            widget.cost_of_removing(self.warehouse, Decimal("100")), Decimal("440")
        )

    def test_cost_that_arrives_after_its_goods_have_shipped_stays_off_the_shelf(self):
        # The layer's cost went out with the goods. There is nothing left
        # here to adjust, and raising a different layer would overstate
        # stock the freight had nothing to do with.
        widget = self.item(CostingMethod.FIFO)
        old = self.receive(widget, "100", "4")
        self.issue(widget, "100")
        self.receive(widget, "100", "6")
        StockMovement.objects.create(
            item=widget, warehouse=self.warehouse,
            movement_type=MovementType.ADJUSTMENT, uom=self.each,
            quantity=Decimal("0"), value_adjustment=Decimal("200"),
            adjusts=old, occurred_at=timezone.now(),
        )
        self.assertEqual(widget.stock_value_at(self.warehouse), Decimal("600.00"))

    def test_a_real_landed_cost_names_the_receipt_it_came_from(self):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        widget = self.item(CostingMethod.FIFO)
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=widget, uom=self.each,
            quantity=Decimal("100"), unit_price=Decimal("4"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        receipt_line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("100"),
        )
        receipt.post()
        receipt_line.refresh_from_db()
        self.assertIsNotNone(receipt_line.stock_movement)
        self.assertEqual(receipt_line.stock_movement.quantity, Decimal("100.0000"))
