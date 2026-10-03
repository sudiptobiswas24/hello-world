"""
A starting point, so valuation does not begin at the beginning of time.

The replay is linear in movements — 678 ms for a weighted average over
twenty thousand, measured rather than guessed — and a delivery spanning
three batches pays it three times. A fold is not a second source of
truth: it is the movements up to a point, added up, and thrown away the
moment anything lands at or before that point.

So the test that matters is not that it is fast. It is that the answer
is identical either way, on every method, after every kind of
correction. Everything else here is about the fold being discarded when
it stops being true.
"""

import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)

from .costing import replay
from .models import (
    AdjustmentReason,
    CostingMethod,
    Item,
    Lot,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockMovement,
    StockValuationSnapshot,
    TrackingMode,
    Warehouse,
)
from .snapshots import fold_position


class SnapshotTestCase(TestCase):
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
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )

    def item(self, method=CostingMethod.AVERAGE, sku=None, tracking=TrackingMode.NONE,
             standard=None):
        return Item.objects.create(
            sku=sku or f"S-{method}", name=str(method), uom=self.each,
            costing_method=method, tracking=tracking, standard_cost=standard,
        )

    def move(self, item, quantity, cost="5", warehouse=None, lot=None, when=None,
             adjustment=None, adjusts=None):
        moment = when or timezone.now()
        if isinstance(moment, datetime.date) and not isinstance(moment, datetime.datetime):
            moment = timezone.make_aware(
                datetime.datetime.combine(moment, datetime.time(9, 0))
            )
        quantity = Decimal(quantity)
        return StockMovement.objects.create(
            item=item, warehouse=warehouse or self.north,
            movement_type=(
                MovementType.RECEIPT if quantity > 0 else MovementType.ISSUE
            ),
            uom=self.each, lot=lot, quantity=quantity,
            unit_cost=Decimal(cost) if quantity else None,
            value_adjustment=Decimal(adjustment) if adjustment else None,
            adjusts=adjusts, occurred_at=moment,
        )

    def unfolded(self, item, warehouse=None, **kwargs):
        """The answer with every fold thrown away."""
        kept = list(StockValuationSnapshot.objects.filter(item=item))
        StockValuationSnapshot.objects.filter(item=item).delete()
        try:
            return replay(item, warehouse, **kwargs)
        finally:
            for snapshot in kept:
                snapshot.pk = None
                snapshot.save()

    def assertSameEitherWay(self, item, warehouse=None, **kwargs):
        fold_position(item, warehouse or self.north)
        folded = replay(item, warehouse, **kwargs)
        self.assertEqual(folded, self.unfolded(item, warehouse, **kwargs))
        return folded


class TheAnswerIsTheSameEitherWayTests(SnapshotTestCase):
    """The only thing a fold may change is where the replay starts."""

    def busy(self, item, receipts=6, issues=4):
        for index in range(receipts):
            self.move(item, "100", str(4 + index))
        for _ in range(issues):
            self.move(item, "-30")

    def test_weighted_average(self):
        item = self.item(CostingMethod.AVERAGE)
        self.busy(item)
        self.assertSameEitherWay(item, self.north)

    def test_fifo(self):
        item = self.item(CostingMethod.FIFO, sku="F")
        self.busy(item)
        self.assertSameEitherWay(item, self.north)

    def test_standard(self):
        item = self.item(CostingMethod.STANDARD, sku="ST", standard=Decimal("7"))
        self.busy(item)
        self.assertSameEitherWay(item, self.north)

    def test_specific_identification(self):
        item = self.item(
            CostingMethod.SPECIFIC, sku="SP", tracking=TrackingMode.LOT
        )
        first = Lot.objects.create(item=item, code="A")
        second = Lot.objects.create(item=item, code="B")
        self.move(item, "50", "4", lot=first)
        self.move(item, "50", "9", lot=second)
        self.move(item, "-20", lot=first)
        self.assertSameEitherWay(item, self.north)

    def test_across_every_warehouse(self):
        # A valuation with no warehouse pools them into one running
        # average, which is not the sum of the separate ones, so it has
        # its own fold.
        item = self.item(CostingMethod.AVERAGE, sku="G")
        self.move(item, "100", "4")
        self.move(item, "100", "8", warehouse=self.south)
        self.move(item, "-50")
        fold_position(item, self.north)
        self.assertEqual(replay(item, None), self.unfolded(item, None))

    def test_after_a_write_off(self):
        item = self.item(CostingMethod.FIFO, sku="F2")
        self.busy(item)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=item, uom=self.each, quantity=Decimal("-40")
        )
        adjustment.post()
        self.assertSameEitherWay(item, self.north)

    def test_after_that_write_off_is_voided(self):
        item = self.item(CostingMethod.AVERAGE, sku="A2")
        self.busy(item)
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=item, uom=self.each, quantity=Decimal("-40")
        )
        adjustment.post()
        adjustment.void()
        self.assertSameEitherWay(item, self.north)

    def test_a_shelf_that_went_below_zero(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        item = self.item(CostingMethod.FIFO, sku="F3")
        self.move(item, "10", "5", warehouse=backorders)
        self.move(item, "-25", warehouse=backorders)
        self.move(item, "40", "6", warehouse=backorders)
        self.assertSameEitherWay(item, backorders)

    def test_as_at_a_date_after_the_fold(self):
        item = self.item(CostingMethod.AVERAGE, sku="D")
        self.move(item, "100", "4", when=datetime.date(2026, 1, 10))
        self.move(item, "100", "8", when=datetime.date(2026, 3, 10))
        fold_position(item, self.north)
        self.assertEqual(
            replay(item, self.north, as_of=datetime.date(2026, 6, 1)),
            self.unfolded(item, self.north, as_of=datetime.date(2026, 6, 1)),
        )

    def test_as_at_a_date_before_the_fold_ignores_it(self):
        # The fold counts movements the caller has not asked about, so it
        # is no use and the walk starts from the beginning.
        item = self.item(CostingMethod.AVERAGE, sku="D2")
        self.move(item, "100", "4", when=datetime.date(2026, 1, 10))
        self.move(item, "100", "8", when=datetime.date(2026, 3, 10))
        fold_position(item, self.north)
        self.assertEqual(
            replay(item, self.north, as_of=datetime.date(2026, 2, 1)),
            (Decimal("100"), Decimal("400")),
        )


class ALandedCostFindsItsLayerTests(SnapshotTestCase):
    """
    A landed cost names the receipt it was incurred on, and that invoice
    can arrive months after the receipt has been folded in. The first
    version of the fold dropped the layer's identity, so the cost found
    no layer, was silently discarded, and the shelf disagreed with the
    ledger by the freight.
    """

    def test_freight_still_raises_the_layer_it_paid_for(self):
        item = self.item(CostingMethod.FIFO, sku="L")
        arrival = self.move(item, "100", "5", when=datetime.date(2026, 1, 10))
        for _ in range(3):
            self.move(item, "50", "6")
        fold_position(item, self.north)
        self.assertTrue(StockValuationSnapshot.objects.filter(item=item).exists())
        self.move(item, "0", "0", adjustment="300", adjusts=arrival)
        self.assertEqual(replay(item, self.north), self.unfolded(item, self.north))
        self.assertEqual(replay(item, self.north)[1], Decimal("1700"))

    def test_the_cost_is_not_lost(self):
        item = self.item(CostingMethod.FIFO, sku="L2")
        arrival = self.move(item, "100", "5", when=datetime.date(2026, 1, 10))
        for _ in range(3):
            self.move(item, "50", "6")
        fold_position(item, self.north)
        before = replay(item, self.north)[1]
        self.move(item, "0", "0", adjustment="300", adjusts=arrival)
        self.assertEqual(replay(item, self.north)[1] - before, Decimal("300"))


class AFoldIsThrownAwayWhenItStopsBeingTrueTests(SnapshotTestCase):
    def test_a_backdated_movement_discards_it(self):
        # A receipt entered on Monday for Friday lands before the fold,
        # which is then answering about a ledger that no longer exists.
        item = self.item(CostingMethod.AVERAGE, sku="B1")
        self.move(item, "100", "5", when=datetime.date(2026, 3, 1))
        fold_position(item, self.north)
        self.assertTrue(StockValuationSnapshot.objects.filter(item=item).exists())
        self.move(item, "100", "9", when=datetime.date(2026, 1, 1))
        self.assertFalse(StockValuationSnapshot.objects.filter(item=item).exists())

    def test_and_the_answer_is_still_right(self):
        item = self.item(CostingMethod.AVERAGE, sku="B2")
        self.move(item, "100", "5", when=datetime.date(2026, 3, 1))
        fold_position(item, self.north)
        self.move(item, "100", "9", when=datetime.date(2026, 1, 1))
        self.assertEqual(replay(item, self.north), self.unfolded(item, self.north))

    def test_a_later_movement_leaves_it_alone(self):
        item = self.item(CostingMethod.AVERAGE, sku="B3")
        self.move(item, "100", "5", when=datetime.date(2026, 3, 1))
        fold_position(item, self.north)
        self.move(item, "100", "9", when=datetime.date(2026, 5, 1))
        self.assertTrue(StockValuationSnapshot.objects.filter(item=item).exists())

    def test_a_fold_for_one_method_is_no_use_to_another(self):
        item = self.item(CostingMethod.AVERAGE, sku="M1")
        self.move(item, "100", "4")
        self.move(item, "100", "8")
        fold_position(item, self.north)
        item.costing_method = CostingMethod.FIFO
        item.save()
        self.assertEqual(replay(item, self.north), self.unfolded(item, self.north))

    def test_a_fold_for_one_warehouse_is_no_use_to_another(self):
        item = self.item(CostingMethod.AVERAGE, sku="W1")
        self.move(item, "100", "4")
        fold_position(item, self.north)
        self.move(item, "50", "10", warehouse=self.south)
        self.assertEqual(
            replay(item, self.south), self.unfolded(item, self.south)
        )


class FoldingHappensOnTheWritePathTests(SnapshotTestCase):
    def test_nothing_is_folded_before_there_is_enough_to_fold(self):
        item = self.item(CostingMethod.AVERAGE, sku="Q1")
        for _ in range(5):
            self.move(item, "10", "5")
        self.assertFalse(StockValuationSnapshot.objects.filter(item=item).exists())

    def test_reading_a_valuation_writes_nothing(self):
        # Housekeeping belongs on the write path: a report should not pay
        # for it, and a rolled-back transaction should not leave a fold.
        item = self.item(CostingMethod.AVERAGE, sku="Q2")
        self.move(item, "10", "5")
        StockValuationSnapshot.objects.all().delete()
        replay(item, self.north)
        item.stock_value_at(self.north)
        item.cost_of_removing(self.north, Decimal("1"))
        self.assertFalse(StockValuationSnapshot.objects.exists())

    def test_a_fold_records_where_the_walk_reached(self):
        item = self.item(CostingMethod.AVERAGE, sku="Q3")
        for _ in range(3):
            self.move(item, "10", "5")
        last = self.move(item, "10", "5")
        fold_position(item, self.north)
        snapshot = StockValuationSnapshot.objects.get(
            item=item, warehouse=self.north
        )
        self.assertEqual(snapshot.boundary_id, last.pk)
        self.assertEqual(snapshot.quantity, Decimal("40"))
        self.assertEqual(snapshot.value, Decimal("200"))

    def test_the_write_path_folds_once_enough_has_accumulated(self):
        # `fold_position` is the deliberate fold; what production runs is
        # the threshold, and if it never fires the whole thing is dead
        # code that the other tests here would not notice.
        item = self.item(CostingMethod.AVERAGE, sku="Q4")
        with patch("apps.inventory.snapshots.SNAPSHOT_EVERY", 3), \
                patch("apps.inventory.snapshots.FOLD_CHECK_EVERY", 1):
            for _ in range(2):
                self.move(item, "10", "5")
            self.assertFalse(
                StockValuationSnapshot.objects.filter(item=item).exists()
            )
            self.move(item, "10", "5")
        snapshot = StockValuationSnapshot.objects.get(item=item, warehouse=self.north)
        self.assertEqual(snapshot.quantity, Decimal("30"))
        self.assertEqual(snapshot.value, Decimal("150"))

    def test_the_threshold_counts_from_the_last_fold_not_from_nothing(self):
        item = self.item(CostingMethod.AVERAGE, sku="Q5")
        with patch("apps.inventory.snapshots.SNAPSHOT_EVERY", 3), \
                patch("apps.inventory.snapshots.FOLD_CHECK_EVERY", 1):
            for _ in range(3):
                self.move(item, "10", "5")
            first = StockValuationSnapshot.objects.get(
                item=item, warehouse=self.north
            )
            self.move(item, "10", "5")
            unchanged = StockValuationSnapshot.objects.get(
                item=item, warehouse=self.north
            )
            self.assertEqual(unchanged.boundary_id, first.boundary_id)
            self.move(item, "10", "5")
            last = self.move(item, "10", "5")
        moved_on = StockValuationSnapshot.objects.get(item=item, warehouse=self.north)
        self.assertEqual(moved_on.boundary_id, last.pk)


class TheBackfillCommandTests(SnapshotTestCase):
    """
    Folding on the write path leaves an existing ledger with no folds at
    all, and an item that has stopped moving never gets one.
    """

    def test_it_folds_a_position_that_is_not_being_written_to(self):
        item = self.item(CostingMethod.AVERAGE, sku="C1")
        self.move(item, "100", "5")
        self.move(item, "-40")
        StockValuationSnapshot.objects.all().delete()
        call_command("fold_valuations", stdout=StringIO())
        snapshot = StockValuationSnapshot.objects.get(item=item, warehouse=self.north)
        self.assertEqual(snapshot.quantity, Decimal("60"))
        self.assertEqual(replay(item, self.north), self.unfolded(item, self.north))

    def test_it_can_be_told_to_leave_the_short_ones_alone(self):
        item = self.item(CostingMethod.AVERAGE, sku="C2")
        self.move(item, "100", "5")
        StockValuationSnapshot.objects.all().delete()
        call_command("fold_valuations", at_least=5, stdout=StringIO())
        self.assertFalse(StockValuationSnapshot.objects.exists())


class AHistoricalValuationReadsTheBoundaryTheSameWayTests(SnapshotTestCase):
    """
    `as_of` is a date, and the movements are filtered on
    `occurred_at__date`, which the database evaluates in the active
    timezone. Reading the fold's boundary in UTC instead agrees only
    where the two are the same, and silently folds in a movement past
    the date asked about where they are not.
    """

    @override_settings(TIME_ZONE="Asia/Tokyo")
    def test_a_movement_past_the_date_is_not_folded_in(self):
        item = self.item(CostingMethod.AVERAGE, sku="TZ")
        self.move(
            item, "100", "5",
            when=timezone.make_aware(
                datetime.datetime(2026, 2, 20, 9, 0), datetime.timezone.utc
            ),
        )
        # Late on the first in UTC is already the second in Tokyo, so
        # this movement is not part of an as-at-the-first valuation.
        self.move(
            item, "100", "9",
            when=timezone.make_aware(
                datetime.datetime(2026, 3, 1, 20, 0), datetime.timezone.utc
            ),
        )
        fold_position(item, self.north)
        self.assertEqual(
            replay(item, self.north, as_of=datetime.date(2026, 3, 1)),
            (Decimal("100"), Decimal("500")),
        )


class TheLedgerIsAppendOnlyTests(SnapshotTestCase):
    """
    The fold is only safe because a written movement never changes. It
    said so in a docstring and nothing made it true, which is this
    project's first defect shape; an edited row would make every fold
    past it wrong, with the replay no longer able to notice.
    """

    def test_a_written_movement_cannot_be_edited(self):
        item = self.item(CostingMethod.AVERAGE, sku="I1")
        movement = self.move(item, "100", "5")
        movement.unit_cost = Decimal("9")
        with self.assertRaises(ValidationError):
            movement.save()
        movement.refresh_from_db()
        self.assertEqual(movement.unit_cost, Decimal("5"))

    def test_a_written_movement_cannot_be_deleted(self):
        item = self.item(CostingMethod.AVERAGE, sku="I2")
        movement = self.move(item, "100", "5")
        with self.assertRaises(ValidationError):
            movement.delete()
        self.assertEqual(replay(item, self.north), (Decimal("100"), Decimal("500")))


class AskingWhetherToFoldIsItselfSampledTests(SnapshotTestCase):
    """
    Counting what has landed since the last fold was the most expensive
    thing on the write path — 5.3 ms a movement against 0.34 ms for the
    insert — so the question is asked on a sample of writes. The answer
    to a valuation must not depend on which writes those were.
    """

    def test_most_writes_do_not_even_ask(self):
        item = self.item(CostingMethod.AVERAGE, sku="P1")
        with patch("apps.inventory.snapshots.movements_past") as counted:
            counted.return_value = 0
            for _ in range(20):
                self.move(item, "1", "5")
        # Twenty movements, two scopes each: asking every time would be
        # forty counts, and at a stride of sixty-four it is usually none.
        self.assertLess(counted.call_count, 40)

    def test_invalidating_is_not_sampled(self):
        # The sampling is a cost decision about when to fold. Throwing
        # away a fold that has stopped being true is correctness, and
        # happens on every write whatever its id.
        item = self.item(CostingMethod.AVERAGE, sku="P2")
        self.move(item, "100", "5", when=datetime.date(2026, 3, 1))
        fold_position(item, self.north)
        for offset in range(1, 8):
            self.move(item, "10", "9", when=datetime.date(2026, 1, offset))
            self.assertFalse(
                StockValuationSnapshot.objects.filter(item=item).exists(),
                "a backdated movement left a fold standing",
            )
            fold_position(item, self.north)
        self.assertEqual(replay(item, self.north), self.unfolded(item, self.north))
