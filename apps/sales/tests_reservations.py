"""
Stock promised to one order is not available to the next.

`available_at()` returned what was on the shelf. Two confirmed orders
for twenty-five each could both be taken against a shelf holding thirty,
and nothing said so until the second picker went looking.

A reservation is not a movement: the goods have not gone anywhere, and
writing them out of one warehouse and into a "reserved" one would make
the valuation ledger a record of intent rather than of goods. It is a
claim held beside the ledger.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.models import (
    MovementType,
    StockMovement,
    StockReservation,
    Warehouse,
)

from .models import (
    Delivery,
    DeliveryLine,
    OrderStatus,
    SalesOrder,
    SalesOrderLine,
)
from .tests_base import SalesTestCase


class ReservationTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        # tests_base seeds 500; bring it to a number small enough to fight over.
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.ISSUE,
            uom=self.item.uom, quantity=Decimal("-470"),
            unit_cost=self.item.average_cost_at(self.warehouse),
            occurred_at=timezone.now(),
        )

    def order_for(self, quantity, warehouse=True, confirm=True, uom=None):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=uom or self.item.uom,
            quantity=Decimal(quantity), unit_price=Decimal("10"),
            revenue_account=self.revenue,
            warehouse=self.warehouse if warehouse is True else warehouse,
        )
        if confirm:
            order.confirm()
        return order, line

    def ship(self, order, line, quantity, warehouse=None):
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 3, 5)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line,
            warehouse=warehouse or self.warehouse,
            quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return delivery


class HoldingStockTests(ReservationTestCase):
    def test_confirming_an_order_holds_its_stock(self):
        self.order_for("25")
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("25"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("5"))

    def test_on_hand_is_unchanged_by_a_reservation(self):
        self.order_for("25")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("30"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("120.00"))

    def test_two_orders_cannot_both_promise_the_last_units(self):
        self.order_for("25")
        _order, line = self.order_for("25")
        self.assertEqual(line.quantity_reserved(), Decimal("5"))
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("30"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("0"))

    def test_the_shortfall_is_reported_rather_than_hidden(self):
        self.order_for("25")
        order, line = self.order_for("25")
        self.assertEqual(order.reservation_shortfalls(), {line: Decimal("20")})

    def test_an_order_is_still_taken_when_the_shelf_is_empty(self):
        # Orders are taken precisely so the stock can be bought. Refusing
        # one because the shelf is bare makes the system useless at the
        # moment it matters.
        order, line = self.order_for("500")
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(line.quantity_reserved(), Decimal("30"))
        self.assertEqual(order.reservation_shortfalls(), {line: Decimal("470")})

    def test_a_line_naming_no_warehouse_holds_nothing(self):
        self.order_for("25", warehouse=None)
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))

    def test_a_line_in_cases_holds_twelve_times_as_much(self):
        case = self.item.uom.derived_units.create(
            code="case", name="Case of 12", category=self.item.uom.category,
            conversion_factor=Decimal("12"),
        )
        self.order_for("2", uom=case)
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("24"))

    def test_a_draft_order_holds_nothing(self):
        self.order_for("25", confirm=False)
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))

    def test_quarantined_stock_is_never_available_reserved_or_not(self):
        bay = Warehouse.objects.create(code="Q", name="Bay", is_quarantine=True)
        StockMovement.objects.create(
            item=self.item, warehouse=bay, movement_type=MovementType.RECEIPT,
            uom=self.item.uom, quantity=Decimal("50"), unit_cost=Decimal("4"),
            occurred_at=timezone.now(),
        )
        self.assertEqual(self.item.available_at(bay), 0)


class ShippingAgainstAHoldTests(ReservationTestCase):
    def test_an_order_may_ship_the_stock_it_reserved(self):
        # The whole point of holding it. A naive availability check would
        # count the order's own claim against its own shipment and refuse.
        order, line = self.order_for("25")
        self.ship(order, line, "25")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("5"))

    def test_shipping_spends_the_claim(self):
        order, line = self.order_for("25")
        self.ship(order, line, "25")
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))
        self.assertEqual(line.quantity_reserved(), Decimal("0"))

    def test_a_partial_shipment_spends_only_part_of_it(self):
        order, line = self.order_for("25")
        self.ship(order, line, "10")
        self.assertEqual(line.quantity_reserved(), Decimal("15"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("20"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("5"))

    def test_another_order_cannot_ship_reserved_stock(self):
        first_order, first_line = self.order_for("25")
        # A second order that names no warehouse holds nothing, so nothing
        # stopped it walking off with the first order's goods.
        second_order, second_line = self.order_for("25", warehouse=None)
        with self.assertRaises(ValidationError) as caught:
            self.ship(second_order, second_line, "25")
        self.assertIn("unreserved", str(caught.exception))

    def test_what_is_unreserved_may_still_ship(self):
        self.order_for("25")
        order, line = self.order_for("5", warehouse=None)
        self.ship(order, line, "5")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("25"))

    def test_a_backorder_warehouse_ignores_the_hold(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        StockMovement.objects.create(
            item=self.item, warehouse=backorders, movement_type=MovementType.RECEIPT,
            uom=self.item.uom, quantity=Decimal("10"), unit_cost=Decimal("4"),
            occurred_at=timezone.now(),
        )
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        holder = SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.item.uom, quantity=Decimal("10"),
            unit_price=Decimal("10"), revenue_account=self.revenue, warehouse=backorders,
        )
        order.confirm()
        other, other_line = self.order_for("8", warehouse=None)
        self.ship(other, other_line, "8", warehouse=backorders)
        self.assertEqual(self.item.on_hand_at(backorders), Decimal("2"))


class ReleasingTests(ReservationTestCase):
    def test_cancelling_an_order_frees_its_stock(self):
        order, _line = self.order_for("25")
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("5"))
        order.cancel()
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("30"))

    def test_a_freed_hold_can_be_taken_by_the_next_order(self):
        first, _ = self.order_for("25")
        second, second_line = self.order_for("25")
        self.assertEqual(second_line.quantity_reserved(), Decimal("5"))
        first.cancel()
        second.reserve_stock()
        self.assertEqual(second_line.quantity_reserved(), Decimal("25"))

    def test_a_released_claim_is_finished(self):
        order, line = self.order_for("25")
        reservation = StockReservation.objects.for_source(line).get()
        reservation.release("test")
        self.assertFalse(reservation.is_open())
        self.assertEqual(reservation.remaining(), Decimal("0"))
        with self.assertRaises(ValidationError):
            reservation.save()

    def test_releasing_twice_is_harmless(self):
        order, line = self.order_for("25")
        reservation = StockReservation.objects.for_source(line).get()
        reservation.release("once")
        reservation.release("twice")
        self.assertEqual(reservation.released_reason, "once")

    def test_a_spent_claim_cannot_be_consumed_again(self):
        order, line = self.order_for("25")
        reservation = StockReservation.objects.for_source(line).get()
        reservation.consume(Decimal("25"))
        self.assertFalse(reservation.is_open())
        with self.assertRaises(ValidationError):
            reservation.consume(Decimal("1"))

    def test_consuming_more_than_is_held_draws_only_what_is_held(self):
        order, line = self.order_for("25")
        reservation = StockReservation.objects.for_source(line).get()
        self.assertEqual(reservation.consume(Decimal("40")), Decimal("25"))
        self.assertEqual(reservation.remaining(), Decimal("0"))

    def test_the_record_still_says_what_was_promised(self):
        order, line = self.order_for("25")
        reservation = StockReservation.objects.for_source(line).get()
        reservation.consume(Decimal("10"))
        self.assertEqual(reservation.quantity, Decimal("25"))
        self.assertEqual(reservation.consumed, Decimal("10"))
        self.assertEqual(reservation.remaining(), Decimal("15"))


class ReorderSeesReservationsTests(ReservationTestCase):
    def test_stock_promised_away_does_not_count_as_stock_on_hand(self):
        # Reorder rules read available_at, so an order that has spoken for
        # the shelf should pull the reorder trigger sooner, not later.
        from apps.purchasing.models import ReorderRule, reorder_suggestions

        ReorderRule.objects.create(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal("20"), target=Decimal("100"),
        )
        self.assertEqual(reorder_suggestions(), [])
        self.order_for("25")
        suggestions = reorder_suggestions()
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["on_hand"], Decimal("5"))


class LineChangesFollowThroughTests(ReservationTestCase):
    def test_growing_a_line_takes_more_stock(self):
        _order, line = self.order_for("10")
        self.assertEqual(line.quantity_reserved(), Decimal("10"))
        line.quantity = Decimal("25")
        line.save()
        self.assertEqual(line.quantity_reserved(), Decimal("25"))

    def test_shrinking_a_line_gives_stock_back(self):
        _order, line = self.order_for("25")
        line.quantity = Decimal("10")
        line.save()
        self.assertEqual(line.quantity_reserved(), Decimal("10"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("20"))

    def test_moving_a_line_to_another_warehouse_moves_the_hold(self):
        other = Warehouse.objects.create(code="O", name="Other")
        StockMovement.objects.create(
            item=self.item, warehouse=other, movement_type=MovementType.RECEIPT,
            uom=self.item.uom, quantity=Decimal("40"), unit_cost=Decimal("4"),
            occurred_at=timezone.now(),
        )
        _order, line = self.order_for("25")
        line.warehouse = other
        line.save()
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))
        self.assertEqual(self.item.reserved_at(other), Decimal("25"))

    def test_clearing_the_warehouse_frees_the_hold(self):
        _order, line = self.order_for("25")
        line.warehouse = None
        line.save()
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))

    def test_deleting_a_line_frees_the_hold(self):
        # The claim points at the line generically, so nothing cascades:
        # without this the claim would outlive its own document.
        _order, line = self.order_for("25")
        line.delete()
        self.assertEqual(self.item.reserved_at(self.warehouse), Decimal("0"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("30"))

    def test_a_line_shrunk_below_what_shipped_is_refused(self):
        order, line = self.order_for("25")
        self.ship(order, line, "10")
        line.quantity = Decimal("5")
        with self.assertRaises(ValidationError):
            line.save()

    def test_a_return_does_not_take_the_stock_back_off_the_shelf(self):
        order, line = self.order_for("25")
        delivery = self.ship(order, line, "25")
        self.assertEqual(line.quantity_reserved(), Decimal("0"))
        delivery.create_return()
        # Returned goods are back on the shelf and promised to nobody.
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("30"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("30"))
