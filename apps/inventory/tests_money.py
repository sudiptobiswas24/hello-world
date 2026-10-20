"""
O269: the shelf moves by exactly what each document booked to the inventory account.

The shelf replayed each movement at its rate (eight places, an average carried unrounded) while the
ledger booked each document to the paisa, so the two parted by paisas with nothing wrong in any one
document. Each movement now keeps its money, the paisa its document booked, and every method sums them.

Figures (/home/user/work/calc_fix_o269/o269.py): two kilos booked at 20.01 average 10.005. One written off,
moved or shipped takes 10.01, half up, from shelf and books alike; the last takes the 10.00 left. FIFO
layers of 2 for 20.01 and 3 for 31.00 give up 30.34 for three. A kilo received at a standard of 10.005
books 10.01, and so does the shelf.
"""

from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils import timezone

from . import models as inventory_models
from .costing import CostingMethod, cost_of_removing, replay
from .models import Item, MovementType, StockMovement, Warehouse
from .snapshots import fold_position
from .tests_adjustments import AdjustmentTestCase
from .transfers import StockTransfer, StockTransferLine

D = Decimal


class MoneyTestCase(AdjustmentTestCase):
    def setUp(self):
        super().setUp()
        self.south = Warehouse.objects.create(code="S", name="South")

    def shelf(self, item=None):
        item = item or self.item
        return sum((item.valuation_at(w)[1] for w in Warehouse.objects.all()), D("0"))

    def moved(self, step):
        """(what the shelf moved, what the inventory account moved) over `step`."""
        shelf, books = self.shelf(), self.balance(self.inventory)
        step()
        return self.shelf() - shelf, self.balance(self.inventory) - books


class AWriteOffMovesTheShelfByItsEntryTests(MoneyTestCase):
    def setUp(self):
        super().setUp()
        self.stock("2", "10.005")  # 20.01 on the shelf

    def test_one_kilo_at_an_average_of_10_005_moves_both_10_01(self):
        self.assertEqual(self.moved(lambda: self.adjust("-1", reason=self.shrink)), (D("-10.01"), D("-10.01")))

    def test_the_last_kilo_takes_what_is_left(self):
        self.adjust("-1", reason=self.shrink)
        self.assertEqual(self.moved(lambda: self.adjust("-1", reason=self.shrink)), (D("-10.00"), D("-10.00")))
        self.assertEqual(self.shelf(), D("0"))

    def test_its_void_puts_back_what_it_took(self):
        adjustment = self.adjust("-1", reason=self.shrink)
        self.stock("3", "7.3333")  # the average moves before the void
        self.assertEqual(self.moved(adjustment.void), (D("10.01"), D("10.01")))

    def test_a_write_up_at_10_005_books_and_shelves_10_01(self):
        self.assertEqual(self.moved(lambda: self.adjust("1", cost="10.005")), (D("10.01"), D("10.01")))

    def test_a_movement_says_its_money_to_the_paisa(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                item=self.item, warehouse=self.warehouse, movement_type=MovementType.ADJUSTMENT,
                uom=self.item.uom, quantity=D("-1"), value=D("-10.005"), occurred_at=timezone.now())


class ATransferCarriesItsMoneyTests(MoneyTestCase):
    def transfer(self, quantity):
        move = StockTransfer.objects.create(transfer_date=timezone.localdate(), from_warehouse=self.warehouse,
                                            to_warehouse=self.south)
        StockTransferLine.objects.create(transfer=move, item=self.item, uom=self.item.uom, quantity=D(quantity))
        move.post()
        return move

    def test_both_ends_move_the_paisa_leaving_takes(self):
        self.stock("2", "10.005")
        self.transfer("1")
        self.assertEqual(self.item.valuation_at(self.warehouse)[1], D("10.00"))
        self.assertEqual(self.item.valuation_at(self.south)[1], D("10.01"))
        out = StockMovement.objects.get(movement_type=MovementType.TRANSFER_OUT)
        into = StockMovement.objects.get(movement_type=MovementType.TRANSFER_IN)
        self.assertEqual((out.value, into.value, into.value_adjustment), (D("-10.01"), D("10.01"), None))


class EveryMethodSumsTheMoneyTests(MoneyTestCase):
    def item_costed(self, method, sku, **extra):
        item = Item.objects.create(sku=sku, name=sku, uom=self.each, costing_method=method, **extra)
        return item

    def receive(self, item, quantity, value, lot=None):
        return StockMovement.objects.create(
            item=item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT, uom=item.uom, lot=lot,
            quantity=D(quantity), unit_cost=D(value) / D(quantity), value=D(value), occurred_at=timezone.now())

    def take(self, item, quantity, lot=None):
        going = cost_of_removing(item, self.warehouse, D(quantity), lot=lot)
        StockMovement.objects.create(
            item=item, warehouse=self.warehouse, movement_type=MovementType.ISSUE, uom=item.uom, lot=lot,
            quantity=-D(quantity), value=-going, occurred_at=timezone.now())
        return going

    def reads_as_a_full_replay(self, item):
        """The fold and the walk from nothing give the same shelf: a replay by insertion order skips folds."""
        fold_position(item, self.warehouse)
        whole = replay(item, self.warehouse, before_id=10 ** 9)
        self.assertEqual(item.valuation_at(self.warehouse), whole)
        return whole

    def test_fifo_takes_whole_layers_and_one_share_rounded_once(self):
        item = self.item_costed(CostingMethod.FIFO, "F")
        self.receive(item, "2", "20.01")
        self.receive(item, "3", "31.00")
        self.assertEqual(self.take(item, "3"), D("30.34"))
        self.assertEqual(self.reads_as_a_full_replay(item), (D("2"), D("20.67")))
        self.assertEqual(self.take(item, "2"), D("20.67"))
        self.assertEqual(self.reads_as_a_full_replay(item), (D("0"), D("0")))

    def test_the_average_is_read_to_eight_places_and_the_money_to_the_paisa(self):
        item = self.item_costed(CostingMethod.AVERAGE, "A")
        self.receive(item, "3", "20.01")
        self.assertEqual(item.average_cost_at(self.warehouse), D("6.6700"))
        self.assertEqual(self.take(item, "1"), D("6.67"))
        self.assertEqual(self.reads_as_a_full_replay(item), (D("2"), D("13.34")))

    def test_the_standard_shelf_is_what_its_receipts_booked(self):
        item = self.item_costed(CostingMethod.STANDARD, "S", standard_cost=D("10.005"))
        StockMovement.objects.create(
            item=item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT, uom=item.uom,
            quantity=D("1"), unit_cost=D("12"), occurred_at=timezone.now())
        self.assertEqual(self.reads_as_a_full_replay(item), (D("1"), D("10.01")))
        self.assertEqual(self.take(item, "1"), D("10.01"))
        self.assertEqual(self.reads_as_a_full_replay(item), (D("0"), D("0")))

    def test_a_batch_keeps_its_own_money(self):
        from .models import Lot

        item = self.item_costed(CostingMethod.SPECIFIC, "B", tracking="lot")
        first = Lot.objects.create(item=item, code="L1")
        second = Lot.objects.create(item=item, code="L2")
        self.receive(item, "2", "20.01", lot=first)
        self.receive(item, "2", "30.00", lot=second)
        self.assertEqual(self.take(item, "1", lot=first), D("10.01"))
        self.assertEqual(self.reads_as_a_full_replay(item), (D("3"), D("40.00")))


class AMovementsMoneyHasTheSignOfItsQuantity(AdjustmentTestCase):
    """O327 (audit2_stores R3): an outbound +10.00 and an inbound -10.00 are refused, not taken as a fact."""

    def movement(self, movement_type, quantity, value):
        return StockMovement.objects.create(item=self.item, warehouse=self.warehouse, movement_type=movement_type,
                                            uom=self.item.uom, quantity=D(quantity), value=D(value),
                                            occurred_at=timezone.now())

    def test_an_outbound_movement_cannot_add_money(self):
        self.stock("2", "10")
        with self.assertRaises(ValidationError):
            self.movement("issue", "-1", "10.00")
        self.assertEqual(self.item.valuation_at(self.warehouse), (D("2"), D("20.00")))

    def test_an_inbound_movement_cannot_take_money(self):
        with self.assertRaises(ValidationError):
            self.movement("receipt", "1", "-10.00")

    def test_nothing_either_way_and_the_right_way_are_taken(self):
        self.stock("2", "10")
        self.movement("issue", "-1", "-10.00")
        self.movement("receipt", "1", "0.00")
        self.assertEqual(self.item.valuation_at(self.warehouse), (D("2"), D("10.00")))


# O326 (audit2_stores R2): a movement written by application code without its money is refused however the call
# is spelt, at what StockMovement.save is given; each spelling runs here as if from an application module.
APP_SOURCE = str(Path(inventory_models.__file__).with_name("example.py"))


class EverySpellingIsSeenWhereItIsWritten(AdjustmentTestCase):
    def setUp(self):
        super().setUp()
        self.stock("5", "10.005")

    def written_by_the_application(self, call):
        """Run `call` with an application module, not a test, as its writer."""
        names = {"StockMovement": StockMovement, "Movement": StockMovement, "inventory_models": inventory_models,
                 "item": self.item, "fields": {"warehouse": self.warehouse, "movement_type": "issue",
                                               "uom": self.item.uom, "quantity": D("-1"),
                                               "occurred_at": timezone.now()},
                 "kw": {"item": self.item, "warehouse": self.warehouse, "movement_type": "issue",
                        "uom": self.item.uom, "occurred_at": timezone.now()}, "D": D}
        exec(compile(call, APP_SOURCE, "exec"), names)

    def assertRefused(self, call):
        before = StockMovement.objects.count()
        with self.assertRaises(ValidationError) as caught:
            self.written_by_the_application(call)
        self.assertIn("without its money", str(caught.exception))
        self.assertEqual(StockMovement.objects.count(), before)

    def test_a_qualified_class(self):
        self.assertRefused("inventory_models.StockMovement.objects.create(quantity=D('-1'), **kw)")

    def test_the_related_manager(self):
        self.assertRefused("item.movements.create(quantity=D('-1'), **{k: v for k, v in kw.items() if k != 'item'})")

    def test_get_or_create(self):
        self.assertRefused("StockMovement.objects.get_or_create(quantity=D('-1'), **kw)")

    def test_an_alias(self):
        self.assertRefused("Movement.objects.create(quantity=D('-1'), **kw)")

    def test_value_none_is_not_saying_it(self):
        self.assertRefused("StockMovement.objects.create(quantity=D('-1'), value=None, **kw)")

    def test_a_movement_saved_by_hand(self):
        self.assertRefused("m = StockMovement(quantity=D('-1'), **kw)\nm.save()")

    def test_a_dict_of_fields_is_not_waved_through_without_value(self):
        self.assertRefused("StockMovement.objects.create(item=item, **fields)")

    def test_an_inbound_movement_too(self):
        self.assertRefused("StockMovement.objects.create(quantity=D('1'), unit_cost=D('10.005'), "
                           "**{**kw, 'movement_type': 'receipt'})")

    def test_one_that_says_its_money_passes(self):
        self.written_by_the_application("StockMovement.objects.create(quantity=D('-1'), value=D('-10.01'), **kw)")
        self.assertEqual(self.item.valuation_at(self.warehouse)[1], D("40.02"))

    def test_a_value_only_movement_says_it_in_its_adjustment(self):
        self.written_by_the_application(
            "StockMovement.objects.create(quantity=D('0'), value_adjustment=D('3'), **{**kw, 'movement_type': "
            "'adjustment'})")

    def test_a_test_fixture_may_still_leave_it_out(self):
        StockMovement.objects.create(quantity=D("-1"), **{"item": self.item, "warehouse": self.warehouse,
                                                          "movement_type": "issue", "uom": self.item.uom,
                                                          "occurred_at": timezone.now()})
