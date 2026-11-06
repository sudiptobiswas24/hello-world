"""
Serialising the things that read a position and then change it.

Every posting path has the same shape: read what is on the shelf,
decide, write the movements. Between the read and the write another
transaction can do the same, so two shipments of the last ten units both
find ten and both post.

The whole codebase had one select_for_update, on the document sequence.
No test could have found this — a test suite is single-threaded, so the
window never opens — which is why the real guard is the mechanical check
in `audit_invariants` and why these tests are about the lock being taken
rather than about a race being lost.
"""

import datetime
from decimal import Decimal

from django.db import transaction
from django.test import TestCase, TransactionTestCase
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

from .models import (
    AdjustmentReason,
    Item,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockMovement,
    StockPosition,
    StockTransfer,
    StockTransferLine,
    Warehouse,
    lock_position,
    lock_positions,
)


class LockingTestCase(TestCase):
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
        self.other = Item.objects.create(sku="X", name="Other", uom=self.each)
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )

    def stock(self, quantity="100", cost="5", item=None, warehouse=None):
        return StockMovement.objects.create(
            item=item or self.item, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=self.each,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )


class TheLockItselfTests(LockingTestCase):
    def test_it_creates_the_position_the_first_time(self):
        with transaction.atomic():
            held = lock_position(self.item, self.north)
        self.assertIsNotNone(held)
        self.assertEqual(StockPosition.objects.count(), 1)

    def test_it_reuses_the_position_afterwards(self):
        with transaction.atomic():
            lock_position(self.item, self.north)
            lock_position(self.item, self.north)
        self.assertEqual(StockPosition.objects.count(), 1)

    def test_taking_it_twice_in_one_transaction_is_free(self):
        # A row lock is held by the transaction, not the caller, so a
        # path that locks early and posts through something that locks
        # again pays once.
        with transaction.atomic():
            first = lock_position(self.item, self.north)
            second = lock_position(self.item, self.north)
        self.assertEqual(first.pk, second.pk)

    def test_each_item_and_warehouse_has_its_own(self):
        with transaction.atomic():
            lock_position(self.item, self.north)
            lock_position(self.item, self.south)
            lock_position(self.other, self.north)
        self.assertEqual(StockPosition.objects.count(), 3)

    def test_a_valuation_across_every_warehouse_has_no_single_position(self):
        with transaction.atomic():
            self.assertIsNone(lock_position(self.item, None))

    def test_several_positions_are_taken_in_a_fixed_order(self):
        # Two documents touching the same pair must take them in the same
        # sequence, or they wait for each other forever.
        with transaction.atomic():
            forwards = lock_positions(
                [(self.item, self.north), (self.other, self.south)]
            )
        with transaction.atomic():
            backwards = lock_positions(
                [(self.other, self.south), (self.item, self.north)]
            )
        self.assertEqual(
            [p.pk for p in forwards], [p.pk for p in backwards]
        )

    def test_duplicates_are_taken_once(self):
        with transaction.atomic():
            held = lock_positions(
                [(self.item, self.north), (self.item, self.north)]
            )
        self.assertEqual(len(held), 1)

    def test_a_missing_side_is_skipped_rather_than_crashing(self):
        # A transfer with no transit warehouse offers a None; there is
        # nothing to hold and nothing to complain about.
        with transaction.atomic():
            held = lock_positions([(self.item, None), (None, self.north)])
        self.assertEqual(held, [])


class ThePostingPathsHoldItTests(LockingTestCase):
    """
    Each of these posts a document and then asserts the position exists,
    which it only does because something took the lock.
    """

    def test_an_adjustment_holds_the_shelf_it_writes_down(self):
        self.stock("100")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each,
            quantity=Decimal("-10"),
        )
        adjustment.post()
        self.assertTrue(
            StockPosition.objects.filter(item=self.item, warehouse=self.north).exists()
        )

    def test_voiding_holds_it_too(self):
        self.stock("100")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each,
            quantity=Decimal("-10"),
        )
        adjustment.post()
        StockPosition.objects.all().delete()
        adjustment.void()
        self.assertTrue(StockPosition.objects.exists())

    def test_a_transfer_holds_both_ends(self):
        self.stock("100")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, quantity=Decimal("30")
        )
        move.post()
        self.assertEqual(
            StockPosition.objects.filter(item=self.item).count(), 2
        )

    def test_cancelling_a_transfer_holds_both_ends(self):
        self.stock("100")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.item, uom=self.each, quantity=Decimal("30")
        )
        move.post()
        StockPosition.objects.all().delete()
        move.cancel()
        self.assertEqual(StockPosition.objects.filter(item=self.item).count(), 2)

    def test_a_delivery_holds_the_shelf_it_ships_from(self):
        from apps.sales.models import (
            Delivery,
            DeliveryLine,
            SalesOrder,
            SalesOrderLine,
        )

        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        self.stock("100")
        order = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 4, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.each, quantity=Decimal("10"),
            unit_price=Decimal("20"), revenue_account=revenue,
        )
        order.confirm()
        StockPosition.objects.all().delete()
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 4, 1)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.north,
            quantity_shipped=Decimal("10"),
        )
        delivery.post()
        self.assertTrue(
            StockPosition.objects.filter(item=self.item, warehouse=self.north).exists()
        )

    def test_a_goods_receipt_holds_the_shelf_it_lands_on(self):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        company = Company.get()
        company.grni_account = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        company.save()
        vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        order = PurchaseOrder.objects.create(
            vendor=vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.each,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.north,
            quantity_received=Decimal("10"),
        )
        receipt.post()
        self.assertTrue(
            StockPosition.objects.filter(item=self.item, warehouse=self.north).exists()
        )

    def test_a_reservation_holds_the_shelf_it_claims(self):
        from apps.sales.models import SalesOrder, SalesOrderLine

        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        self.stock("100")
        StockPosition.objects.all().delete()
        order = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 4, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.each, quantity=Decimal("10"),
            unit_price=Decimal("20"), revenue_account=revenue,
            warehouse=self.north,
        )
        order.confirm()
        self.assertTrue(
            StockPosition.objects.filter(item=self.item, warehouse=self.north).exists()
        )


class OutsideATransactionTests(TransactionTestCase):
    """
    A TransactionTestCase, because the ordinary one wraps every test in
    a transaction and the guard could never be seen from inside it —
    which is itself a reminder that the thing being guarded against is
    invisible to this suite.
    """

    def test_it_refuses(self):
        # The lock is released the moment it is taken, so holding it
        # outside a transaction protects nothing, and silence there would
        # be worse than the error.
        uom = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        item = Item.objects.create(sku="W", name="Widget", uom=uom)
        warehouse = Warehouse.objects.create(code="N", name="North")
        with self.assertRaises(RuntimeError) as caught:
            lock_position(item, warehouse)
        self.assertIn("outside a transaction", str(caught.exception))
