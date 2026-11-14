"""
Lots, serial numbers and expiry.

A widget was a widget. Which batch it came from, when it goes off, and
which specific unit went to which customer were questions this system
could not answer — and they are exactly the questions asked when
something goes wrong.

Tracking here is quantity and traceability, not costing: stock stays
valued at weighted average per item and warehouse. These tests say so
explicitly, so the next reader does not assume otherwise.
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
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    TrackingMode,
    Warehouse,
    allocate,
    expiring,
    lots_at,
    traceability,
)


class TrackingTestCase(TestCase):
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
        self.plain = Item.objects.create(sku="P", name="Plain", uom=self.each)
        self.batched = Item.objects.create(
            sku="B", name="Vaccine", uom=self.each, tracking=TrackingMode.LOT
        )
        self.serialised = Item.objects.create(
            sku="S", name="Engine", uom=self.each, tracking=TrackingMode.SERIAL
        )
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )

    def lot(self, code, expires=None, item=None):
        return Lot.objects.create(
            item=item or self.batched, code=code,
            expires_on=datetime.date.fromisoformat(expires) if expires else None,
        )

    def receive(self, lot=None, quantity="100", cost="5", item=None, warehouse=None):
        return StockMovement.objects.create(
            item=item or (lot.item if lot else self.plain),
            warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT,
            uom=(item or (lot.item if lot else self.plain)).uom,
            lot=lot, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )


class TheGuardTests(TrackingTestCase):
    def test_a_tracked_item_must_say_which_batch(self):
        with self.assertRaises(ValidationError) as caught:
            self.receive(item=self.batched)
        self.assertIn("must say which", str(caught.exception))

    def test_an_untracked_item_may_not_name_one(self):
        lot = self.lot("L1")
        with self.assertRaises(ValidationError) as caught:
            StockMovement.objects.create(
                item=self.plain, warehouse=self.north,
                movement_type=MovementType.RECEIPT, uom=self.each, lot=lot,
                quantity=Decimal("10"), unit_cost=Decimal("5"),
                occurred_at=timezone.now(),
            )
        self.assertIn("not tracked", str(caught.exception))

    def test_a_lot_belongs_to_its_own_item(self):
        other = Item.objects.create(
            sku="X", name="Other", uom=self.each, tracking=TrackingMode.LOT
        )
        lot = self.lot("L1")
        with self.assertRaises(ValidationError) as caught:
            self.receive(lot=lot, item=other)
        self.assertIn("does not belong", str(caught.exception))

    def test_one_code_per_item(self):
        from django.db.utils import IntegrityError

        self.lot("L1")
        with self.assertRaises(IntegrityError):
            self.lot("L1")

    def test_the_same_code_may_name_a_batch_of_another_item(self):
        self.lot("L1")
        other = Item.objects.create(
            sku="X", name="Other", uom=self.each, tracking=TrackingMode.LOT
        )
        self.lot("L1", item=other)  # no error
        self.assertEqual(Lot.objects.filter(code="L1").count(), 2)

    def test_defaulting_to_untracked_leaves_the_existing_ledger_alone(self):
        self.assertEqual(self.plain.tracking, TrackingMode.NONE)
        self.receive(quantity="10")
        self.assertEqual(self.plain.on_hand_at(self.north), Decimal("10"))


class BatchQuantityTests(TrackingTestCase):
    def test_stock_is_counted_per_batch_as_well_as_per_shelf(self):
        first, second = self.lot("L1"), self.lot("L2")
        self.receive(first, "40")
        self.receive(second, "60")
        self.assertEqual(self.batched.on_hand_at(self.north), Decimal("100"))
        self.assertEqual(first.on_hand_at(self.north), Decimal("40"))
        self.assertEqual(second.on_hand_at(self.north), Decimal("60"))

    def test_a_batch_knows_where_it_is(self):
        lot = self.lot("L1")
        self.receive(lot, "40")
        self.receive(lot, "10", warehouse=self.south)
        self.assertEqual(
            sorted((w.code, q) for w, q in lot.warehouses()),
            [("N", Decimal("40")), ("S", Decimal("10"))],
        )

    def test_valuation_stays_per_item_and_warehouse(self):
        # Stated deliberately: tracking is traceability, not costing.
        first, second = self.lot("L1"), self.lot("L2")
        self.receive(first, "100", "4")
        self.receive(second, "100", "6")
        self.assertEqual(self.batched.average_cost_at(self.north), Decimal("5.0000"))
        self.assertEqual(self.batched.stock_value_at(self.north), Decimal("1000.00"))

    def test_lots_on_a_shelf_are_listed_soonest_to_expire_first(self):
        late = self.lot("LATE", "2026-12-01")
        early = self.lot("EARLY", "2026-06-01")
        never = self.lot("NEVER")
        for lot in (late, early, never):
            self.receive(lot, "10")
        self.assertEqual(
            [lot.code for lot, _q in lots_at(self.batched, self.north)],
            ["EARLY", "LATE", "NEVER"],
        )

    def test_an_emptied_batch_drops_off_the_shelf(self):
        lot = self.lot("L1")
        self.receive(lot, "10")
        StockMovement.objects.create(
            item=self.batched, warehouse=self.north, movement_type=MovementType.ISSUE,
            uom=self.each, lot=lot, quantity=Decimal("-10"),
            unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        self.assertEqual(lots_at(self.batched, self.north), [])
        self.assertEqual(len(lots_at(self.batched, self.north, include_empty=True)), 1)


class AllocationTests(TrackingTestCase):
    def test_the_soonest_to_expire_goes_first(self):
        late = self.lot("LATE", "2026-12-01")
        early = self.lot("EARLY", "2026-08-01")
        self.receive(late, "50")
        self.receive(early, "50")
        chosen = allocate(self.batched, self.north, "30", on_date="2026-06-01")
        self.assertEqual([(lot.code, q) for lot, q in chosen], [("EARLY", Decimal("30"))])

    def test_it_spills_into_the_next_batch(self):
        early = self.lot("EARLY", "2026-08-01")
        late = self.lot("LATE", "2026-12-01")
        self.receive(early, "20")
        self.receive(late, "50")
        chosen = allocate(self.batched, self.north, "35", on_date="2026-06-01")
        self.assertEqual(
            [(lot.code, q) for lot, q in chosen],
            [("EARLY", Decimal("20")), ("LATE", Decimal("15"))],
        )

    def test_expired_stock_is_not_allocated(self):
        gone_off = self.lot("OLD", "2026-01-01")
        good = self.lot("GOOD", "2026-12-01")
        self.receive(gone_off, "50")
        self.receive(good, "50")
        chosen = allocate(self.batched, self.north, "30", on_date="2026-06-01")
        self.assertEqual([lot.code for lot, _q in chosen], ["GOOD"])

    def test_a_shortfall_says_how_much_of_it_is_expired(self):
        gone_off = self.lot("OLD", "2026-01-01")
        self.receive(gone_off, "50")
        with self.assertRaises(ValidationError) as caught:
            allocate(self.batched, self.north, "10", on_date="2026-06-01")
        self.assertIn("expired", str(caught.exception))

    def test_expired_stock_can_be_taken_deliberately(self):
        # Writing it off is a legitimate reason to touch it.
        gone_off = self.lot("OLD", "2026-01-01")
        self.receive(gone_off, "50")
        chosen = allocate(
            self.batched, self.north, "50", on_date="2026-06-01", allow_expired=True
        )
        self.assertEqual([lot.code for lot, _q in chosen], ["OLD"])

    def test_a_plain_shortfall_refuses_rather_than_part_allocating(self):
        lot = self.lot("L1")
        self.receive(lot, "5")
        with self.assertRaises(ValidationError) as caught:
            allocate(self.batched, self.north, "10")
        self.assertIn("short", str(caught.exception))


class SerialNumberTests(TrackingTestCase):
    def serial(self, code):
        return Lot.objects.create(item=self.serialised, code=code)

    def test_a_serial_moves_one_unit_at_a_time(self):
        engine = self.serial("SN-1")
        with self.assertRaises(ValidationError) as caught:
            self.receive(engine, "2", item=self.serialised)
        self.assertIn("one unit at a time", str(caught.exception))

    def test_the_same_unit_cannot_arrive_twice(self):
        engine = self.serial("SN-1")
        self.receive(engine, "1", item=self.serialised)
        with self.assertRaises(ValidationError) as caught:
            self.receive(engine, "1", item=self.serialised)
        self.assertIn("cannot arrive twice", str(caught.exception))

    def test_a_unit_cannot_leave_a_shelf_it_is_not_on(self):
        engine = self.serial("SN-1")
        self.receive(engine, "1", item=self.serialised)
        with self.assertRaises(ValidationError) as caught:
            StockMovement.objects.create(
                item=self.serialised, warehouse=self.south,
                movement_type=MovementType.ISSUE, uom=self.each, lot=engine,
                quantity=Decimal("-1"), unit_cost=Decimal("100"),
                occurred_at=timezone.now(),
            )
        self.assertIn("is not at", str(caught.exception))

    def test_a_unit_that_has_shipped_can_come_back(self):
        engine = self.serial("SN-1")
        self.receive(engine, "1", item=self.serialised)
        StockMovement.objects.create(
            item=self.serialised, warehouse=self.north,
            movement_type=MovementType.ISSUE, uom=self.each, lot=engine,
            quantity=Decimal("-1"), unit_cost=Decimal("100"),
            occurred_at=timezone.now(),
        )
        self.assertEqual(engine.on_hand_at(), Decimal("0"))
        self.receive(engine, "1", item=self.serialised)
        self.assertEqual(engine.on_hand_at(self.north), Decimal("1"))

    def test_serials_allocate_one_at_a_time(self):
        for code in ("SN-1", "SN-2", "SN-3"):
            self.receive(self.serial(code), "1", "100", item=self.serialised)
        chosen = allocate(self.serialised, self.north, "2")
        self.assertEqual(len(chosen), 2)
        self.assertEqual({q for _lot, q in chosen}, {Decimal("1")})


class ExpiryReportTests(TrackingTestCase):
    def test_it_lists_what_goes_off_before_a_date(self):
        soon = self.lot("SOON", "2026-07-01")
        later = self.lot("LATER", "2027-01-01")
        self.receive(soon, "10")
        self.receive(later, "10")
        rows = expiring("2026-09-01")
        self.assertEqual([row["lot"].code for row in rows], ["SOON"])
        self.assertEqual(rows[0]["quantity"], Decimal("10"))

    def test_a_batch_with_nothing_left_is_not_reported(self):
        soon = self.lot("SOON", "2026-07-01")
        self.receive(soon, "10")
        StockMovement.objects.create(
            item=self.batched, warehouse=self.north, movement_type=MovementType.ISSUE,
            uom=self.each, lot=soon, quantity=Decimal("-10"),
            unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        self.assertEqual(expiring("2026-09-01"), [])

    def test_a_batch_that_never_expires_is_never_reported(self):
        self.receive(self.lot("FOREVER"), "10")
        self.assertEqual(expiring("2099-01-01"), [])

    def test_it_can_be_asked_about_one_shelf(self):
        soon = self.lot("SOON", "2026-07-01")
        self.receive(soon, "10")
        self.receive(soon, "4", warehouse=self.south)
        rows = expiring("2026-09-01", warehouse=self.south)
        self.assertEqual(rows[0]["quantity"], Decimal("4"))

    def test_it_reports_soonest_first(self):
        for code, date in (("C", "2026-08-01"), ("A", "2026-06-01"), ("B", "2026-07-01")):
            self.receive(self.lot(code, date), "5")
        self.assertEqual(
            [row["lot"].code for row in expiring("2026-09-01")], ["A", "B", "C"]
        )


class TraceabilityTests(TrackingTestCase):
    def test_a_batch_can_say_everywhere_it_has_been(self):
        lot = self.lot("L1")
        self.receive(lot, "100")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.batched, uom=self.each, lot=lot,
            quantity=Decimal("30"),
        )
        move.post()
        trail = traceability(lot)
        self.assertEqual(
            [(row["warehouse"].code, row["quantity"]) for row in trail],
            [("N", Decimal("100.0000")), ("N", Decimal("-30.0000")), ("S", Decimal("30.0000"))],
        )

    def test_a_transfer_cannot_move_more_of_a_batch_than_is_there(self):
        first, second = self.lot("L1"), self.lot("L2")
        self.receive(first, "10")
        self.receive(second, "90")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.batched, uom=self.each, lot=first,
            quantity=Decimal("20"),
        )
        # 100 are on the shelf, but only 10 of this batch.
        with self.assertRaises(ValidationError) as caught:
            move.post()
        self.assertIn("L1", str(caught.exception))


class AdjustingABatchTests(TrackingTestCase):
    def test_a_write_off_names_the_batch_it_destroyed(self):
        lot = self.lot("L1", "2026-01-01")
        self.receive(lot, "50")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.batched, uom=self.each, lot=lot,
            quantity=Decimal("-50"),
        )
        adjustment.post()
        self.assertEqual(lot.on_hand_at(self.north), Decimal("0"))
        self.assertEqual(self.batched.on_hand_at(self.north), Decimal("0"))

    def test_a_count_sheet_counts_batch_by_batch(self):
        first, second = self.lot("L1"), self.lot("L2")
        self.receive(first, "40")
        self.receive(second, "60")
        sheet = StockCount.objects.create(
            count_date=datetime.date(2026, 6, 1), warehouse=self.north, reason=self.reason
        )
        sheet.add(self.batched, "38", lot=first)
        sheet.add(self.batched, "60", lot=second)
        sheet.post()
        self.assertEqual(first.on_hand_at(self.north), Decimal("38"))
        self.assertEqual(second.on_hand_at(self.north), Decimal("60"))

    def test_a_count_of_a_tracked_item_must_name_a_batch(self):
        self.receive(self.lot("L1"), "40")
        sheet = StockCount.objects.create(
            count_date=datetime.date(2026, 6, 1), warehouse=self.north, reason=self.reason
        )
        with self.assertRaises(ValidationError) as caught:
            sheet.add(self.batched, "38")
        self.assertIn("say which batch", str(caught.exception))


class ShippingATrackedItemTests(TrackingTestCase):
    """The delivery path, end to end, for stock that has a batch."""

    def setUp(self):
        super().setUp()
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def ship(self, lot, quantity, on=datetime.date(2026, 6, 1)):
        from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=on, currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.batched, uom=self.each,
            quantity=Decimal(quantity), unit_price=Decimal("20"),
            revenue_account=self.revenue,
        )
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=on)
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.north,
            lot=lot, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery

    def test_a_shipment_records_which_batch_the_customer_got(self):
        lot = self.lot("L1", "2026-12-01")
        self.receive(lot, "100")
        delivery = self.ship(lot, "10")
        self.assertEqual(lot.on_hand_at(self.north), Decimal("90"))
        trail = traceability(lot)
        self.assertEqual(trail[-1]["reference"], delivery.number)

    def test_expired_stock_cannot_be_shipped(self):
        lot = self.lot("OLD", "2026-01-01")
        self.receive(lot, "100")
        with self.assertRaises(ValidationError) as caught:
            self.ship(lot, "10")
        self.assertIn("cannot be shipped", str(caught.exception))

    def test_a_batch_that_is_still_in_date_ships(self):
        lot = self.lot("GOOD", "2026-07-01")
        self.receive(lot, "100")
        self.ship(lot, "10", on=datetime.date(2026, 6, 1))
        self.assertEqual(lot.on_hand_at(self.north), Decimal("90"))

    def test_a_batch_cannot_ship_more_than_it_holds(self):
        small, large = self.lot("SMALL"), self.lot("LARGE")
        self.receive(small, "10")
        self.receive(large, "90")
        # The shelf holds a hundred; this batch holds ten.
        with self.assertRaises(ValidationError) as caught:
            self.ship(small, "20")
        self.assertIn("batch SMALL", str(caught.exception))

    def test_a_write_off_may_still_touch_expired_stock(self):
        lot = self.lot("OLD", "2026-01-01")
        self.receive(lot, "100")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 6, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.batched, uom=self.each, lot=lot,
            quantity=Decimal("-100"),
        )
        adjustment.post()
        self.assertEqual(lot.on_hand_at(self.north), Decimal("0"))
