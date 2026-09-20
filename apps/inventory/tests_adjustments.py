"""
Stock adjustments and physical counts.

Nothing here could record the difference between what the books say is
on the shelf and what is actually on it — the one stock document every
warehouse uses. `MovementType.ADJUSTMENT` existed and only purchasing's
landed cost ever wrote it.

The assertions that matter are the agreement ones: after any adjustment,
and after voiding any adjustment, the inventory account and
`stock_value_at()` must be the same number.
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
    UnitOfMeasure,
    UnitOfMeasureCategory,
)

from .models import (
    AdjustmentDirection,
    AdjustmentReason,
    Item,
    ItemType,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMovement,
    Warehouse,
)


class AdjustmentTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        self.case = UnitOfMeasure.objects.create(
            code="case", name="Case of 12", category=UnitOfMeasureCategory.COUNT,
            base_unit=self.each, conversion_factor=Decimal("12"),
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.other_inventory = acc("1210", "Inventory - spares", AccountType.ASSET)
        self.shrinkage = acc("6200", "Stock shrinkage", AccountType.EXPENSE)
        self.gain = acc("4900", "Stock found", AccountType.INCOME)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.warehouse = Warehouse.objects.create(code="W", name="Main")
        self.shrink = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage,
            direction=AdjustmentDirection.DECREASE,
        )
        self.found = AdjustmentReason.objects.create(
            code="FOUND", name="Found on shelf", account=self.gain,
            direction=AdjustmentDirection.INCREASE,
        )
        self.recount = AdjustmentReason.objects.create(
            code="COUNT", name="Count variance", account=self.shrinkage,
        )

    def stock(self, quantity="100", cost="5", item=None, warehouse=None):
        return StockMovement.objects.create(
            item=item or self.item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, uom=(item or self.item).uom,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def balance(self, account):
        return sum(
            (line.debit - line.credit for line in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )

    def adjust(self, quantity, reason=None, uom=None, cost=None, item=None, post=True):
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 3, 1),
            warehouse=self.warehouse,
            reason=reason or self.recount,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=item or self.item, uom=uom or (item or self.item).uom,
            quantity=Decimal(quantity),
            unit_cost=Decimal(cost) if cost is not None else None,
        )
        if post:
            adjustment.post()
        return adjustment


class WritingStockDownTests(AdjustmentTestCase):
    def test_it_takes_the_quantity_off_the_shelf(self):
        self.stock("100", "5")
        self.adjust("-4", reason=self.shrink)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))

    def test_it_charges_the_reason_and_credits_inventory(self):
        self.stock("100", "5")
        self.adjust("-4", reason=self.shrink)
        self.assertEqual(self.balance(self.shrinkage), Decimal("20.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("-20.00"))

    def test_the_ledger_and_the_stock_value_agree(self):
        self.stock("100", "5")
        self.adjust("-4", reason=self.shrink)
        self.assertEqual(
            self.item.stock_value_at(self.warehouse), Decimal("480.00")
        )

    def test_it_values_the_write_off_at_the_weighted_average(self):
        self.stock("100", "5")
        self.stock("100", "7")
        # 1200 over 200 units is 6 apiece, not the 5 or the 7.
        adjustment = self.adjust("-10", reason=self.shrink)
        self.assertEqual(adjustment.lines.get().unit_cost, Decimal("6.0000"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("60.00"))

    def test_it_freezes_the_cost_it_used(self):
        self.stock("100", "5")
        adjustment = self.adjust("-10", reason=self.shrink)
        self.stock("100", "9")
        # The average has moved; what this adjustment did has not.
        self.assertEqual(adjustment.lines.get().unit_cost, Decimal("5.0000"))
        self.assertEqual(adjustment.total_value(), Decimal("-50.00"))

    def test_it_will_not_write_off_more_than_is_there(self):
        self.stock("10", "5")
        with self.assertRaises(ValidationError) as caught:
            self.adjust("-11", reason=self.shrink)
        self.assertIn("on hand", str(caught.exception))

    def test_a_warehouse_that_allows_negative_stock_may_go_below_zero(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        self.stock("10", "5", warehouse=backorders)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 3, 1),
            warehouse=backorders, reason=self.shrink,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each, quantity=Decimal("-15")
        )
        adjustment.post()
        self.assertEqual(self.item.on_hand_at(backorders), Decimal("-5"))


class WritingStockUpTests(AdjustmentTestCase):
    def test_it_puts_the_quantity_on_the_shelf(self):
        self.stock("100", "5")
        self.adjust("6", reason=self.found)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("106"))

    def test_it_debits_inventory_and_credits_the_reason(self):
        self.stock("100", "5")
        self.adjust("6", reason=self.found)
        self.assertEqual(self.balance(self.inventory), Decimal("30.00"))
        self.assertEqual(self.balance(self.gain), Decimal("-30.00"))

    def test_an_explicit_cost_is_used_over_the_average(self):
        self.stock("100", "5")
        self.adjust("10", reason=self.found, cost="8")
        self.assertEqual(self.balance(self.gain), Decimal("-80.00"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("580.00"))

    def test_stock_that_has_never_existed_needs_a_cost(self):
        with self.assertRaises(ValidationError) as caught:
            self.adjust("10", reason=self.found)
        self.assertIn("explicit unit cost", str(caught.exception))

    def test_a_line_written_in_cases_lands_in_stocking_units(self):
        self.stock("100", "5")
        self.adjust("2", reason=self.found, uom=self.case, cost="5")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("124"))
        self.assertEqual(self.balance(self.gain), Decimal("-120.00"))


class ReasonTests(AdjustmentTestCase):
    def test_a_decrease_reason_cannot_create_stock(self):
        self.stock("100", "5")
        with self.assertRaises(ValidationError) as caught:
            self.adjust("5", reason=self.shrink)
        self.assertIn("may not be used to increase", str(caught.exception))

    def test_an_increase_reason_cannot_destroy_stock(self):
        self.stock("100", "5")
        with self.assertRaises(ValidationError) as caught:
            self.adjust("-5", reason=self.found)
        self.assertIn("may not be used to decrease", str(caught.exception))

    def test_a_reason_open_both_ways_does_either(self):
        self.stock("100", "5")
        self.adjust("-5", reason=self.recount)
        self.adjust("3", reason=self.recount)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("98"))

    def test_each_item_posts_to_its_own_inventory_account(self):
        spare = Item.objects.create(
            sku="S", name="Spare", uom=self.each, inventory_account=self.other_inventory
        )
        self.stock("100", "5")
        self.stock("100", "2", item=spare)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 3, 1),
            warehouse=self.warehouse, reason=self.shrink,
        )
        for item, quantity in ((self.item, "-2"), (spare, "-5")):
            StockAdjustmentLine.objects.create(
                adjustment=adjustment, item=item, uom=self.each, quantity=Decimal(quantity)
            )
        adjustment.post()
        self.assertEqual(self.balance(self.inventory), Decimal("-10.00"))
        self.assertEqual(self.balance(self.other_inventory), Decimal("-10.00"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("20.00"))


class VoidingTests(AdjustmentTestCase):
    def test_it_puts_the_quantity_back(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        adjustment.void()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))

    def test_it_reverses_the_ledger_entry(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        adjustment.void()
        self.assertEqual(self.balance(self.shrinkage), Decimal("0.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("0.00"))

    def test_the_stock_value_comes_back_to_where_it_was(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        adjustment.void()
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("500.00"))

    def test_voiding_a_write_up_survives_the_average_moving(self):
        # The write-up put 10 on at 8. By the time it is voided the average
        # is well below that. Undoing it at the average would take less off
        # the shelf than the ledger reversal credits, and the difference
        # would never clear.
        self.stock("100", "5")
        adjustment = self.adjust("10", reason=self.found, cost="8")
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("580.00"))
        adjustment.void()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("500.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("0.00"))

    def test_the_two_records_of_stock_value_still_agree_after_a_void(self):
        self.stock("100", "5")
        opening = self.balance(self.inventory)
        adjustment = self.adjust("10", reason=self.found, cost="8")
        adjustment.void()
        self.assertEqual(
            self.balance(self.inventory) - opening,
            self.item.stock_value_at(self.warehouse) - Decimal("500.00"),
        )

    def test_it_records_what_undid_it(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        reversal = adjustment.void()
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.voided_entry, reversal)
        self.assertEqual(reversal.reverses, adjustment.journal_entry)
        self.assertTrue(adjustment.is_voided())

    def test_it_cannot_be_voided_twice(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        adjustment.void()
        with self.assertRaises(ValidationError):
            adjustment.void()

    def test_a_draft_cannot_be_voided(self):
        adjustment = self.adjust("-4", reason=self.shrink, post=False)
        with self.assertRaises(ValidationError) as caught:
            adjustment.void()
        self.assertIn("Only a posted adjustment", str(caught.exception))

    def test_voiding_a_write_up_whose_stock_has_since_gone_is_refused(self):
        self.stock("10", "5")
        adjustment = self.adjust("10", reason=self.found, cost="5")
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.ISSUE,
            uom=self.each, quantity=Decimal("-15"),
            unit_cost=self.item.average_cost_at(self.warehouse),
            occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as caught:
            adjustment.void()
        self.assertIn("remains", str(caught.exception))


class ImmutabilityTests(AdjustmentTestCase):
    def test_a_posted_adjustment_cannot_be_edited(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        adjustment.memo = "changed"
        with self.assertRaises(ValidationError):
            adjustment.save()

    def test_a_line_on_a_posted_adjustment_cannot_be_edited(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        line = adjustment.lines.get()
        line.quantity = Decimal("-9")
        with self.assertRaises(ValidationError):
            line.save()

    def test_a_line_on_a_posted_adjustment_cannot_be_deleted(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        with self.assertRaises(ValidationError):
            adjustment.lines.get().delete()

    def test_it_cannot_be_posted_twice(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        with self.assertRaises(ValidationError):
            adjustment.post()

    def test_an_empty_adjustment_posts_nothing(self):
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 3, 1),
            warehouse=self.warehouse, reason=self.shrink,
        )
        with self.assertRaises(ValidationError) as caught:
            adjustment.post()
        self.assertIn("no lines", str(caught.exception))

    def test_a_non_stocked_item_has_nothing_to_adjust(self):
        service = Item.objects.create(
            sku="SVC", name="Install", uom=self.each,
            item_type=ItemType.SERVICE, track_inventory=False,
        )
        with self.assertRaises(ValidationError) as caught:
            self.adjust("-1", reason=self.shrink, item=service)
        self.assertIn("not stocked", str(caught.exception))

    def test_it_takes_a_number_when_it_posts(self):
        self.stock("100", "5")
        adjustment = self.adjust("-4", reason=self.shrink)
        self.assertTrue(adjustment.number.startswith("ADJ-"))


class StockCountTests(AdjustmentTestCase):
    def sheet(self, **kwargs):
        return StockCount.objects.create(
            count_date=datetime.date(2026, 3, 1), warehouse=self.warehouse,
            reason=self.recount, **kwargs,
        )

    def test_a_shortfall_writes_the_shelf_down(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        count.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("20.00"))

    def test_a_surplus_writes_the_shelf_up(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "103")
        count.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("103"))
        # The opening stock was seeded straight into the movement ledger,
        # so the inventory account carries the adjustment and nothing else.
        self.assertEqual(self.balance(self.inventory), Decimal("15.00"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("-15.00"))

    def test_it_freezes_what_the_books_said_when_the_line_was_written(self):
        self.stock("100", "5")
        count = self.sheet()
        line = count.add(self.item, "96")
        self.assertEqual(line.system_quantity, Decimal("100"))

    def test_a_count_that_agrees_posts_no_adjustment_and_is_still_a_count(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "100")
        adjustment = count.post()
        self.assertIsNone(adjustment)
        count.refresh_from_db()
        self.assertTrue(count.posted)
        self.assertEqual(self.balance(self.shrinkage), Decimal("0.00"))

    def test_stock_moving_after_the_walk_stops_the_posting(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.ISSUE,
            uom=self.each, quantity=Decimal("-20"),
            unit_cost=self.item.average_cost_at(self.warehouse),
            occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as caught:
            count.post()
        self.assertIn("recount", str(caught.exception))

    def test_a_counter_may_count_in_cases(self):
        self.stock("120", "5")
        count = self.sheet()
        count.add(self.item, "9", uom=self.case)
        count.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("108"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("60.00"))

    def test_the_adjustment_it_raised_points_back_at_it(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        adjustment = count.post()
        self.assertEqual(adjustment.count, count)
        self.assertEqual(count.adjustment(), adjustment)

    def test_voiding_the_adjustment_leaves_the_sheet_standing(self):
        # The sheet is evidence that somebody walked the aisle. Undoing
        # its accounting consequence does not unwalk it.
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        adjustment = count.post()
        adjustment.void()
        count.refresh_from_db()
        self.assertTrue(count.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))

    def test_one_line_per_item(self):
        from django.db.utils import IntegrityError

        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        with self.assertRaises(IntegrityError):
            StockCountLine.objects.create(
                count=count, item=self.item, uom=self.each,
                counted_quantity=Decimal("90"), system_quantity=Decimal("100"),
            )

    def test_a_posted_sheet_cannot_be_edited(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        count.post()
        count.memo = "changed"
        with self.assertRaises(ValidationError):
            count.save()

    def test_a_posted_sheet_takes_no_more_lines(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        count.post()
        with self.assertRaises(ValidationError):
            count.add(self.item, "90")

    def test_it_cannot_be_posted_twice(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        count.post()
        with self.assertRaises(ValidationError):
            count.post()

    def test_it_takes_a_number_when_it_posts(self):
        self.stock("100", "5")
        count = self.sheet()
        count.add(self.item, "96")
        count.post()
        count.refresh_from_db()
        self.assertTrue(count.number.startswith("CNT-"))
