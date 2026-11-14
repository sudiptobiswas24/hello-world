"""Outbound shipments: stock going out, over-shipping guards, returns."""

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
)
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine


class DeliveryTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.cogs = Account.objects.create(
            code="5000", name="Cost of Sales", account_type=AccountType.EXPENSE
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co",
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs,
            grni_account=self.grni,
        )

    def stock_up(self, quantity="100", item=None, warehouse=None, unit_cost="6"):
        StockMovement.objects.create(
            item=item or self.item,
            warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT,
            uom=(item or self.item).uom,
            quantity=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
            occurred_at=timezone.now(),
        )

    def make_order_line(self, quantity="10", item=None, confirm=True):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1)
        )
        line = SalesOrderLine.objects.create(
            order=order, item=item or self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal("5"),
        )
        if confirm:
            order.confirm()
        return line

    def make_delivery(self, order_line, quantity="4", warehouse=None, post=True):
        delivery = Delivery.objects.create(
            sales_order=order_line.order, delivery_date=datetime.date(2026, 3, 5)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=order_line,
            warehouse=warehouse or self.warehouse, quantity_shipped=Decimal(quantity),
        )
        if post:
            delivery.post()
        return delivery


class ShippingStockTests(DeliveryTestCase):
    def test_posting_decrements_stock(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")

        self.make_delivery(order_line, "4")

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))
        self.assertEqual(order_line.quantity_shipped(), Decimal("4"))

    def test_movement_is_an_issue_with_a_negative_quantity(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        self.make_delivery(order_line, "4")

        movement = StockMovement.objects.latest("id")
        self.assertEqual(movement.movement_type, MovementType.ISSUE)
        self.assertEqual(movement.quantity, Decimal("-4"))

    def test_posting_assigns_a_sequence_number(self):
        self.stock_up()
        order_line = self.make_order_line()
        delivery = self.make_delivery(order_line)
        self.assertEqual(delivery.number, "DO-2026-00001")

    def test_partial_shipments_accumulate(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        self.make_delivery(order_line, "6")
        self.make_delivery(order_line, "4")

        self.assertEqual(order_line.quantity_shipped(), Decimal("10"))
        self.assertTrue(order_line.is_fully_shipped())
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("90"))

    def test_cannot_ship_more_than_ordered(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        self.make_delivery(order_line, "8")

        with self.assertRaises(ValidationError):
            self.make_delivery(order_line, "5")

    def test_cannot_post_a_delivery_with_no_lines(self):
        order_line = self.make_order_line()
        delivery = Delivery.objects.create(
            sales_order=order_line.order, delivery_date=datetime.date(2026, 3, 5)
        )
        with self.assertRaises(ValidationError):
            delivery.post()


class StockAvailabilityTests(DeliveryTestCase):
    def test_cannot_ship_stock_you_do_not_have(self):
        self.stock_up("3")
        order_line = self.make_order_line("10")
        with self.assertRaises(ValidationError):
            self.make_delivery(order_line, "5")

    def test_stock_is_untouched_when_the_shipment_is_refused(self):
        self.stock_up("3")
        order_line = self.make_order_line("10")
        with self.assertRaises(ValidationError):
            self.make_delivery(order_line, "5")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("3"))

    def test_warehouse_can_opt_into_negative_stock(self):
        backorder_warehouse = Warehouse.objects.create(
            code="WH2", name="Backorders", allow_negative_stock=True
        )
        order_line = self.make_order_line("10")
        self.make_delivery(order_line, "5", warehouse=backorder_warehouse)
        self.assertEqual(self.item.on_hand_at(backorder_warehouse), Decimal("-5"))

    def test_availability_is_checked_per_warehouse(self):
        other = Warehouse.objects.create(code="WH3", name="Other")
        self.stock_up("100", warehouse=other)
        order_line = self.make_order_line("10")
        with self.assertRaises(ValidationError):
            self.make_delivery(order_line, "5")  # stock is in the other warehouse


class NonStockedItemTests(DeliveryTestCase):
    def test_shipping_a_service_creates_no_stock_movement(self):
        service = Item.objects.create(
            sku="SVC-1", name="Installation", uom=self.uom,
            item_type="service", track_inventory=False,
        )
        order_line = self.make_order_line("5", item=service)
        before = StockMovement.objects.count()

        self.make_delivery(order_line, "5")

        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(order_line.quantity_shipped(), Decimal("5"))

    def test_a_service_is_not_blocked_by_stock_availability(self):
        service = Item.objects.create(
            sku="SVC-2", name="Consulting", uom=self.uom,
            item_type="service", track_inventory=False,
        )
        order_line = self.make_order_line("99", item=service)
        self.make_delivery(order_line, "99")  # no stock exists; must not raise


class DeliveryImmutabilityTests(DeliveryTestCase):
    def test_posted_delivery_cannot_be_edited(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        delivery.reference = "changed"
        with self.assertRaises(ValidationError):
            delivery.save()

    def test_posted_delivery_cannot_be_deleted(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        with self.assertRaises(ValidationError):
            delivery.delete()

    def test_line_on_posted_delivery_cannot_be_edited(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        line = delivery.lines.first()
        line.quantity_shipped = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()


class CustomerReturnTests(DeliveryTestCase):
    def test_return_puts_the_stock_back(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        delivery = self.make_delivery(order_line, "4")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))

        customer_return = delivery.create_return()

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("100"))
        self.assertEqual(customer_return.reverses, delivery)
        self.assertTrue(customer_return.posted)

    def test_return_uses_its_own_sequence(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        customer_return = delivery.create_return()
        self.assertTrue(customer_return.number.startswith("RET-"))

    def test_returned_quantity_frees_the_order_line_again(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        delivery = self.make_delivery(order_line, "10")
        self.assertTrue(order_line.is_fully_shipped())

        delivery.create_return()

        self.assertEqual(order_line.quantity_shipped(), Decimal("0"))
        self.make_delivery(order_line, "10")  # may ship again; must not raise

    def test_original_delivery_is_untouched_by_the_return(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line(), "4")
        delivery.create_return()
        delivery.refresh_from_db()
        self.assertTrue(delivery.posted)
        self.assertEqual(delivery.lines.first().quantity_shipped, Decimal("4"))

    def test_cannot_return_twice(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        delivery.create_return()
        with self.assertRaises(ValidationError):
            delivery.create_return()

    def test_cannot_return_a_return(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line())
        customer_return = delivery.create_return()
        with self.assertRaises(ValidationError):
            customer_return.create_return()

    def test_cannot_return_an_unposted_delivery(self):
        self.stock_up()
        delivery = self.make_delivery(self.make_order_line(), post=False)
        with self.assertRaises(ValidationError):
            delivery.create_return()


class ConcurrentDraftDeliveryTests(DeliveryTestCase):
    def test_several_draft_deliveries_can_coexist(self):
        self.stock_up("100")
        order_line = self.make_order_line("10")
        first = self.make_delivery(order_line, "3", post=False)
        second = self.make_delivery(order_line, "4", post=False)

        self.assertEqual(Delivery.objects.filter(number="").count(), 2)

        first.post()
        second.post()
        self.assertNotEqual(first.number, second.number)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("93"))


class ExpiredGoodsDoNotGoOnALorryTests(DeliveryTestCase):
    """
    The guard used to sit inside the negative-stock branch, so a
    warehouse that allowed backorders shipped expired goods — and
    nothing tested it, which is why the nesting survived. Mutation
    testing found it: deleting the check entirely broke no test.
    """

    def setUp(self):
        super().setUp()
        from apps.inventory.models import Lot, TrackingMode

        self.item.tracking = TrackingMode.LOT
        self.item.save()
        self.fresh = Lot.objects.create(
            item=self.item, code="GOOD", expires_on=datetime.date(2027, 1, 1)
        )
        self.stale = Lot.objects.create(
            item=self.item, code="OLD", expires_on=datetime.date(2026, 1, 1)
        )

    def stock_lot(self, lot, quantity="100"):
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse,
            movement_type=MovementType.RECEIPT, uom=self.uom,
            quantity=Decimal(quantity), unit_cost=Decimal("6"), lot=lot,
            occurred_at=timezone.now(),
        )

    def ship(self, lot, quantity="4"):
        line = self.make_order_line("10")
        delivery = Delivery.objects.create(
            sales_order=line.order, delivery_date=datetime.date(2026, 3, 5)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.warehouse,
            quantity_shipped=Decimal(quantity), lot=lot,
        )
        return delivery

    def test_an_expired_batch_is_refused(self):
        self.stock_lot(self.stale)
        with self.assertRaises(ValidationError) as caught:
            self.ship(self.stale).post()
        self.assertIn("expired on 2026-01-01", str(caught.exception))

    def test_and_still_refused_where_backorders_are_allowed(self):
        # This is the case that used to go through: the only thing
        # standing between a customer and expired goods was a setting
        # about something else entirely.
        self.warehouse.allow_negative_stock = True
        self.warehouse.save()
        self.stock_lot(self.stale)
        with self.assertRaises(ValidationError) as caught:
            self.ship(self.stale).post()
        self.assertIn("expired on 2026-01-01", str(caught.exception))

    def test_a_batch_in_date_goes(self):
        self.stock_lot(self.fresh)
        self.ship(self.fresh).post()
        self.assertEqual(self.fresh.on_hand_at(self.warehouse), Decimal("96"))

    def test_a_customer_may_still_send_one_back(self):
        # A return is goods coming the other way; refusing it would
        # leave the customer holding stock the books say is ours.
        self.warehouse.allow_negative_stock = True
        self.warehouse.save()
        self.stock_lot(self.stale)
        line = self.make_order_line("10")
        out = Delivery.objects.create(
            sales_order=line.order, delivery_date=datetime.date(2026, 3, 5)
        )
        DeliveryLine.objects.create(
            delivery=out, order_line=line, warehouse=self.warehouse,
            quantity_shipped=Decimal("4"), lot=self.fresh,
        )
        self.stock_lot(self.fresh)
        out.post()
        self.assertEqual(self.fresh.on_hand_at(self.warehouse), Decimal("96"))
