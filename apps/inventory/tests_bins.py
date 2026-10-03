"""
Where in the building the stock actually is.

A warehouse was one undivided space — fine for the ledger, useless to
the person holding a pick list in a building with forty aisles.

Bins are a picking concern and these tests hold that line: valuation is
asserted to be unchanged by them, because a bin says where stock is, not
what it is worth.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Currency, UnitOfMeasure, UnitOfMeasureCategory

from .models import (
    AdjustmentReason,
    Item,
    Lot,
    MovementType,
    StockCount,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    StorageBin,
    TrackingMode,
    Warehouse,
    bins_holding,
    suggest_pick,
    suggest_putaway,
    unbinned,
)


class BinTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.shrinkage = acc("6200", "Shrinkage", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.aisle = StorageBin.objects.create(
            warehouse=self.north, code="A", name="Aisle A", is_pickable=False, sequence=0
        )
        self.a1 = StorageBin.objects.create(
            warehouse=self.north, code="A-01", parent=self.aisle, sequence=1
        )
        self.a2 = StorageBin.objects.create(
            warehouse=self.north, code="A-02", parent=self.aisle, sequence=2
        )
        self.a10 = StorageBin.objects.create(
            warehouse=self.north, code="A-10", parent=self.aisle, sequence=10
        )
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )

    def put(self, storage_bin, quantity="10", cost="5", item=None, warehouse=None):
        return StockMovement.objects.create(
            item=item or self.item, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=(item or self.item).uom,
            bin=storage_bin, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )


class BinStructureTests(BinTestCase):
    def test_a_bin_knows_its_way_home(self):
        self.assertEqual([b.code for b in self.a1.path()], ["A", "A-01"])

    def test_a_grouping_level_answers_for_what_is_inside_it(self):
        self.put(self.a1, "10")
        self.put(self.a2, "15")
        self.assertEqual(self.aisle.on_hand(self.item), Decimal("25"))
        self.assertEqual(self.aisle.on_hand(self.item, include_children=False), Decimal("0"))

    def test_stock_cannot_sit_in_a_grouping_level(self):
        with self.assertRaises(ValidationError) as caught:
            self.put(self.aisle, "10")
        self.assertIn("groups other bins", str(caught.exception))

    def test_a_bin_must_be_in_the_warehouse_the_movement_names(self):
        with self.assertRaises(ValidationError) as caught:
            self.put(self.a1, "10", warehouse=self.south)
        self.assertIn("is not in", str(caught.exception))

    def test_a_bin_cannot_sit_inside_itself(self):
        self.a1.parent = self.a1
        with self.assertRaises(ValidationError):
            self.a1.save()

    def test_a_bin_cannot_sit_inside_another_warehouse(self):
        elsewhere = StorageBin.objects.create(warehouse=self.south, code="B-01")
        self.a1.parent = elsewhere
        with self.assertRaises(ValidationError) as caught:
            self.a1.save()
        self.assertIn("another warehouse", str(caught.exception))

    def test_one_code_per_warehouse(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            StorageBin.objects.create(warehouse=self.north, code="A-01")

    def test_the_same_code_may_exist_in_another_warehouse(self):
        StorageBin.objects.create(warehouse=self.south, code="A-01")
        self.assertEqual(StorageBin.objects.filter(code="A-01").count(), 2)


class BinsAreNotValuationTests(BinTestCase):
    def test_stock_in_bins_is_valued_exactly_as_stock_without_them(self):
        self.put(self.a1, "60", "4")
        self.put(self.a2, "40", "6")
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(self.item.stock_value_at(self.north), Decimal("480.00"))
        self.assertEqual(self.item.average_cost_at(self.north), Decimal("4.8000"))

    def test_moving_between_bins_changes_no_value(self):
        self.put(self.a1, "100", "5")
        before = self.item.stock_value_at(self.north)
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.north,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each,
            from_bin=self.a1, to_bin=self.a2, quantity=Decimal("30"),
        )
        move.post()
        self.assertEqual(self.item.stock_value_at(self.north), before)
        self.assertEqual(self.a1.on_hand(self.item), Decimal("70"))
        self.assertEqual(self.a2.on_hand(self.item), Decimal("30"))


class RequiringBinsTests(BinTestCase):
    def test_an_unbinned_warehouse_asks_for_nothing(self):
        StockMovement.objects.create(
            item=self.item, warehouse=self.north, movement_type=MovementType.RECEIPT,
            uom=self.each, quantity=Decimal("10"), unit_cost=Decimal("5"),
            occurred_at=timezone.now(),
        )
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("10"))

    def test_a_binned_warehouse_insists(self):
        self.north.requires_bins = True
        self.north.save()
        with self.assertRaises(ValidationError) as caught:
            StockMovement.objects.create(
                item=self.item, warehouse=self.north, movement_type=MovementType.RECEIPT,
                uom=self.each, quantity=Decimal("10"), unit_cost=Decimal("5"),
                occurred_at=timezone.now(),
            )
        self.assertIn("must say where", str(caught.exception))

    def test_stock_booked_in_before_the_rule_is_still_findable(self):
        # Turning the rule on does not retrospectively bin what is there,
        # and pretending otherwise would hide it.
        self.put(self.a1, "10")
        StockMovement.objects.create(
            item=self.item, warehouse=self.north, movement_type=MovementType.RECEIPT,
            uom=self.each, quantity=Decimal("40"), unit_cost=Decimal("5"),
            occurred_at=timezone.now(),
        )
        self.north.requires_bins = True
        self.north.save()
        self.assertEqual(unbinned(self.item, self.north), Decimal("40"))
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("50"))


class PickingTests(BinTestCase):
    def test_bins_are_listed_in_walking_order_not_alphabetical(self):
        for storage_bin in (self.a10, self.a2, self.a1):
            self.put(storage_bin, "10")
        self.assertEqual(
            [b.code for b, _q in bins_holding(self.item, self.north)],
            ["A-01", "A-02", "A-10"],
        )

    def test_an_empty_bin_is_not_on_the_route(self):
        self.put(self.a1, "10")
        self.put(self.a2, "10")
        StockMovement.objects.create(
            item=self.item, warehouse=self.north, movement_type=MovementType.ISSUE,
            uom=self.each, bin=self.a1, quantity=Decimal("-10"),
            unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        self.assertEqual(
            [b.code for b, _q in bins_holding(self.item, self.north)], ["A-02"]
        )

    def test_a_pick_spans_bins_in_walking_order(self):
        self.put(self.a1, "10")
        self.put(self.a2, "10")
        self.put(self.a10, "10")
        plan = suggest_pick(self.item, self.north, "25")
        self.assertEqual(
            [(b.code, q) for b, q in plan],
            [("A-01", Decimal("10")), ("A-02", Decimal("10")), ("A-10", Decimal("5"))],
        )

    def test_a_short_pick_says_how_much_is_loose_on_the_floor(self):
        self.put(self.a1, "5")
        StockMovement.objects.create(
            item=self.item, warehouse=self.north, movement_type=MovementType.RECEIPT,
            uom=self.each, quantity=Decimal("40"), unit_cost=Decimal("5"),
            occurred_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as caught:
            suggest_pick(self.item, self.north, "10")
        self.assertIn("no bin recorded", str(caught.exception))

    def test_a_pick_can_be_asked_for_one_batch(self):
        tracked = Item.objects.create(
            sku="B", name="Vaccine", uom=self.each, tracking=TrackingMode.LOT
        )
        first = Lot.objects.create(item=tracked, code="L1")
        second = Lot.objects.create(item=tracked, code="L2")
        StockMovement.objects.create(
            item=tracked, warehouse=self.north, movement_type=MovementType.RECEIPT,
            uom=self.each, bin=self.a1, lot=first, quantity=Decimal("10"),
            unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        StockMovement.objects.create(
            item=tracked, warehouse=self.north, movement_type=MovementType.RECEIPT,
            uom=self.each, bin=self.a2, lot=second, quantity=Decimal("10"),
            unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        plan = suggest_pick(tracked, self.north, "10", lot=second)
        self.assertEqual([(b.code, q) for b, q in plan], [("A-02", Decimal("10"))])


class PutawayTests(BinTestCase):
    def test_goods_go_back_where_the_same_item_already_lives(self):
        self.put(self.a10, "10")
        self.assertEqual(suggest_putaway(self.item, self.north), self.a10)

    def test_a_new_item_goes_to_the_first_pickable_bin(self):
        newcomer = Item.objects.create(sku="X", name="New", uom=self.each)
        self.assertEqual(suggest_putaway(newcomer, self.north), self.a1)

    def test_a_grouping_level_is_never_suggested(self):
        suggested = suggest_putaway(self.item, self.north)
        self.assertTrue(suggested.is_pickable)

    def test_a_warehouse_with_no_bins_suggests_nothing(self):
        self.assertIsNone(suggest_putaway(self.item, self.south))


class CountingABinTests(BinTestCase):
    def test_a_sheet_counts_the_shelf_it_looked_at(self):
        self.put(self.a1, "10")
        self.put(self.a2, "15")
        sheet = StockCount.objects.create(
            count_date=datetime.date(2026, 6, 1), warehouse=self.north, reason=self.reason
        )
        line = sheet.add(self.item, "8", storage_bin=self.a1)
        self.assertEqual(line.system_quantity, Decimal("10"))
        sheet.post()
        self.assertEqual(self.a1.on_hand(self.item), Decimal("8"))
        # The other shelf was not looked at, so it was not written down.
        self.assertEqual(self.a2.on_hand(self.item), Decimal("15"))

    def test_counting_one_shelf_does_not_read_the_whole_warehouse(self):
        # Comparing a shelf count against the warehouse total reads
        # everything legitimately stored elsewhere as a variance.
        self.put(self.a1, "10")
        self.put(self.a2, "90")
        sheet = StockCount.objects.create(
            count_date=datetime.date(2026, 6, 1), warehouse=self.north, reason=self.reason
        )
        sheet.add(self.item, "10", storage_bin=self.a1)
        self.assertIsNone(sheet.post())
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))

    def test_a_sheet_may_still_count_a_whole_warehouse(self):
        self.put(self.a1, "10")
        self.put(self.a2, "90")
        sheet = StockCount.objects.create(
            count_date=datetime.date(2026, 6, 1), warehouse=self.north, reason=self.reason
        )
        line = sheet.add(self.item, "95")
        self.assertEqual(line.system_quantity, Decimal("100"))
