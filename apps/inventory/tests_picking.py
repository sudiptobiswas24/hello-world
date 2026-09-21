"""
Deciding what actually comes off the shelf.

allocate() chose lots first-expired-first-out, suggest_pick() routed a
picker through bins, and suggest_putaway() said where goods should land.
All three were built, tested, and called by nothing: a delivery refused
to post unless somebody typed in the lot and the bin by hand, so FEFO
was advisory — the system knew which batch should go first and was never
asked.

These tests are about the wiring rather than the algorithms, which have
their own. The question here is only ever: did the document ask?
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.sales.models import (
    Delivery,
    DeliveryAllocation,
    DeliveryLine,
    SalesOrder,
    SalesOrderLine,
)

from .models import (
    Item,
    Lot,
    MovementType,
    StockMovement,
    StorageBin,
    TrackingMode,
    Warehouse,
    plan_issue,
    plan_putaway,
)


class PickingTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.purchases = acc("5300", "Purchases", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
            default_purchase_expense_account=self.purchases,
        )
        self.warehouse = Warehouse.objects.create(code="W", name="Main")
        self.plain = Item.objects.create(sku="P", name="Plain", uom=self.each)
        self.batched = Item.objects.create(
            sku="B", name="Vaccine", uom=self.each, tracking=TrackingMode.LOT
        )
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def lot(self, code, expires=None, item=None):
        return Lot.objects.create(
            item=item or self.batched, code=code,
            expires_on=datetime.date.fromisoformat(expires) if expires else None,
        )

    def stock(self, quantity, cost="5", item=None, lot=None, storage_bin=None):
        target = item or (lot.item if lot else self.plain)
        return StockMovement.objects.create(
            item=target, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=target.uom, lot=lot, bin=storage_bin,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def ship(self, item, quantity, lot=None, storage_bin=None,
             on=datetime.date(2026, 6, 1)):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=on, currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=item, uom=self.each, quantity=Decimal(quantity),
            unit_price=Decimal("20"), revenue_account=self.revenue,
        )
        order.confirm()
        delivery = Delivery.objects.create(sales_order=order, delivery_date=on)
        delivery_line = DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.warehouse,
            lot=lot, bin=storage_bin, quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery, delivery_line


class TheDocumentAsksTests(PickingTestCase):
    def test_a_delivery_of_a_tracked_item_no_longer_needs_a_lot_typed_in(self):
        # Before this, posting raised: the item is tracked and the
        # movement had no batch.
        self.stock("100", lot=self.lot("L1", "2026-12-01"))
        _delivery, line = self.ship(self.batched, "10")
        self.assertEqual(self.batched.on_hand_at(self.warehouse), Decimal("90"))
        self.assertEqual(len(line.lots_shipped()), 1)

    def test_it_takes_the_soonest_to_expire_first(self):
        # The whole point. FEFO was advisory until something asked.
        later = self.lot("LATER", "2026-12-01")
        sooner = self.lot("SOONER", "2026-08-01")
        self.stock("50", lot=later)
        self.stock("50", lot=sooner)
        _delivery, line = self.ship(self.batched, "30")
        self.assertEqual([lot.code for lot, _b, _q in line.lots_shipped()], ["SOONER"])
        self.assertEqual(sooner.on_hand_at(self.warehouse), Decimal("20"))
        self.assertEqual(later.on_hand_at(self.warehouse), Decimal("50"))

    def test_a_shipment_spanning_two_batches_writes_a_movement_for_each(self):
        sooner = self.lot("SOONER", "2026-08-01")
        later = self.lot("LATER", "2026-12-01")
        self.stock("20", lot=sooner)
        self.stock("50", lot=later)
        _delivery, line = self.ship(self.batched, "35")
        self.assertEqual(
            [(lot.code, q) for lot, _b, q in line.lots_shipped()],
            [("SOONER", Decimal("20.0000")), ("LATER", Decimal("15.0000"))],
        )
        self.assertEqual(self.batched.on_hand_at(self.warehouse), Decimal("35"))

    def test_expired_stock_is_not_shipped_even_though_it_is_on_the_shelf(self):
        gone_off = self.lot("OLD", "2026-01-01")
        good = self.lot("GOOD", "2026-12-01")
        self.stock("50", lot=gone_off)
        self.stock("50", lot=good)
        _delivery, line = self.ship(self.batched, "30")
        self.assertEqual([lot.code for lot, _b, _q in line.lots_shipped()], ["GOOD"])
        self.assertEqual(gone_off.on_hand_at(self.warehouse), Decimal("50"))

    def test_a_batch_named_by_hand_is_honoured_over_the_suggestion(self):
        # A picker who says which batch they took is reporting a fact.
        sooner = self.lot("SOONER", "2026-08-01")
        later = self.lot("LATER", "2026-12-01")
        self.stock("50", lot=sooner)
        self.stock("50", lot=later)
        _delivery, line = self.ship(self.batched, "10", lot=later)
        self.assertEqual([lot.code for lot, _b, _q in line.lots_shipped()], ["LATER"])

    def test_an_untracked_item_ships_with_no_batch_and_no_fuss(self):
        self.stock("100")
        _delivery, line = self.ship(self.plain, "10")
        self.assertEqual(line.lots_shipped(), [(None, None, Decimal("10.0000"))])

    def test_there_is_a_movement_per_allocation_and_they_sum_to_the_line(self):
        self.stock("20", lot=self.lot("A", "2026-08-01"))
        self.stock("50", lot=self.lot("B", "2026-12-01"))
        delivery, line = self.ship(self.batched, "35")
        movements = StockMovement.objects.filter(reference=delivery.number)
        self.assertEqual(movements.count(), 2)
        self.assertEqual(
            sum((-m.quantity for m in movements), Decimal("0")), Decimal("35.0000")
        )

    def test_the_value_leaving_is_the_same_however_many_movements_it_takes(self):
        from apps.accounting.models import JournalLine

        self.stock("20", "4", lot=self.lot("A", "2026-08-01"))
        self.stock("50", "6", lot=self.lot("B", "2026-12-01"))
        before = self.batched.stock_value_at(self.warehouse)
        delivery, _line = self.ship(self.batched, "35")
        after = self.batched.stock_value_at(self.warehouse)
        cogs = sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(account=self.cogs)),
            Decimal("0"),
        )
        self.assertEqual(before - after, cogs)


class PickingThroughBinsTests(PickingTestCase):
    def setUp(self):
        super().setUp()
        self.warehouse.requires_bins = True
        self.warehouse.save()
        self.a1 = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-01", sequence=1
        )
        self.a2 = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-02", sequence=2
        )
        self.a10 = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-10", sequence=10
        )

    def test_a_delivery_no_longer_needs_a_bin_typed_in(self):
        self.stock("100", storage_bin=self.a1)
        _delivery, line = self.ship(self.plain, "10")
        self.assertEqual(line.lots_shipped()[0][1], self.a1)

    def test_a_pick_spanning_bins_follows_walking_order(self):
        self.stock("10", storage_bin=self.a10)
        self.stock("10", storage_bin=self.a1)
        self.stock("10", storage_bin=self.a2)
        _delivery, line = self.ship(self.plain, "25")
        self.assertEqual(
            [(b.code, q) for _lot, b, q in line.lots_shipped()],
            [("A-01", Decimal("10")), ("A-02", Decimal("10")), ("A-10", Decimal("5"))],
        )

    def test_batches_and_bins_are_chosen_together(self):
        sooner = self.lot("SOONER", "2026-08-01")
        later = self.lot("LATER", "2026-12-01")
        self.stock("10", lot=sooner, storage_bin=self.a2)
        self.stock("10", lot=later, storage_bin=self.a1)
        _delivery, line = self.ship(self.batched, "15")
        self.assertEqual(
            [(lot.code, b.code, q) for lot, b, q in line.lots_shipped()],
            [("SOONER", "A-02", Decimal("10")), ("LATER", "A-01", Decimal("5"))],
        )

    def test_a_bin_named_by_hand_is_honoured(self):
        self.stock("10", storage_bin=self.a1)
        self.stock("10", storage_bin=self.a2)
        _delivery, line = self.ship(self.plain, "5", storage_bin=self.a2)
        self.assertEqual(line.lots_shipped()[0][1], self.a2)

    def test_stock_with_no_bin_recorded_stops_a_routed_pick(self):
        # A binned warehouse cannot route around it, and silently picking
        # only the binned part would ship less than was asked for.
        self.stock("10", storage_bin=self.a1)
        # Booked in before the warehouse was binned, which is exactly how
        # loose stock comes to exist.
        self.warehouse.requires_bins = False
        self.warehouse.save()
        StockMovement.objects.create(
            item=self.plain, warehouse=self.warehouse,
            movement_type=MovementType.ADJUSTMENT, uom=self.each,
            quantity=Decimal("40"), unit_cost=Decimal("5"), occurred_at=timezone.now(),
        )
        self.warehouse.requires_bins = True
        self.warehouse.save()
        with self.assertRaises(ValidationError) as caught:
            plan_issue(self.plain, self.warehouse, Decimal("5"))
        self.assertIn("no bin recorded", str(caught.exception))

    def test_an_unbinned_warehouse_is_left_alone(self):
        loose = Warehouse.objects.create(code="L", name="Loose")
        StockMovement.objects.create(
            item=self.plain, warehouse=loose, movement_type=MovementType.RECEIPT,
            uom=self.each, quantity=Decimal("10"), unit_cost=Decimal("5"),
            occurred_at=timezone.now(),
        )
        self.assertEqual(
            plan_issue(self.plain, loose, Decimal("5")),
            [(None, None, Decimal("5"))],
        )


class PuttingGoodsAwayTests(PickingTestCase):
    def receive(self, quantity, storage_bin=None, warehouse=None):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.plain, uom=self.each,
            quantity=Decimal(quantity), unit_price=Decimal("5"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        receipt_line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line,
            warehouse=warehouse or self.warehouse,
            bin=storage_bin, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt, receipt_line

    def test_a_receipt_into_a_binned_warehouse_no_longer_needs_a_shelf_typed_in(self):
        self.warehouse.requires_bins = True
        self.warehouse.save()
        first = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-01", sequence=1
        )
        self.receive("50")
        self.assertEqual(first.on_hand(self.plain), Decimal("50"))

    def test_goods_go_back_where_the_same_item_already_lives(self):
        self.warehouse.requires_bins = True
        self.warehouse.save()
        StorageBin.objects.create(warehouse=self.warehouse, code="A-01", sequence=1)
        settled = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-09", sequence=9
        )
        self.stock("10", storage_bin=settled)
        self.receive("50")
        self.assertEqual(settled.on_hand(self.plain), Decimal("60"))

    def test_a_shelf_named_by_hand_is_honoured(self):
        self.warehouse.requires_bins = True
        self.warehouse.save()
        StorageBin.objects.create(warehouse=self.warehouse, code="A-01", sequence=1)
        chosen = StorageBin.objects.create(
            warehouse=self.warehouse, code="A-05", sequence=5
        )
        self.receive("50", storage_bin=chosen)
        self.assertEqual(chosen.on_hand(self.plain), Decimal("50"))

    def test_a_binned_warehouse_with_nowhere_to_put_anything_says_so(self):
        self.warehouse.requires_bins = True
        self.warehouse.save()
        with self.assertRaises(ValidationError) as caught:
            plan_putaway(self.plain, self.warehouse)
        self.assertIn("has none that stock can sit in", str(caught.exception))

    def test_an_unbinned_warehouse_is_given_no_shelf(self):
        self.assertIsNone(plan_putaway(self.plain, self.warehouse))
        self.receive("50")
        self.assertEqual(self.plain.on_hand_at(self.warehouse), Decimal("50"))


class ReturnsGoBackWhereTheyCameFromTests(PickingTestCase):
    def test_a_return_does_not_get_re_allocated(self):
        # It goes back to the batch it came off, which the return line
        # carries. Choosing afresh would put the goods in whichever batch
        # happens to expire soonest now.
        sooner = self.lot("SOONER", "2026-08-01")
        later = self.lot("LATER", "2026-12-01")
        self.stock("50", lot=sooner)
        self.stock("50", lot=later)
        delivery, _line = self.ship(self.batched, "10", lot=later)
        delivery.create_return()
        self.assertEqual(later.on_hand_at(self.warehouse), Decimal("50"))
        self.assertEqual(sooner.on_hand_at(self.warehouse), Decimal("50"))

    def test_a_return_of_an_auto_allocated_shipment_knows_its_batch(self):
        sooner = self.lot("SOONER", "2026-08-01")
        self.stock("50", lot=sooner)
        delivery, line = self.ship(self.batched, "10")
        self.assertEqual(line.lots_shipped()[0][0], sooner)
