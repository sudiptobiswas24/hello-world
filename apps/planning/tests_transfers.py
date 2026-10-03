"""
Moving what the company already owns.

A shortage in Nagpur with five tonnes sitting in Hyderabad used to
raise a purchase order — the company buying what it already has and
paying to store it twice.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import MovementType, StockMovement, Warehouse
from apps.inventory.transfers import TransferStatus

from .models import PlannedOrderKind, TransferRoute
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class TransferTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.depot = Warehouse.objects.create(code="H", name="Hyderabad")

    def route(self, days=2, source=None, priority=1):
        return TransferRoute.objects.create(
            from_warehouse=source or self.depot, to_warehouse=self.plant,
            lead_days=days, priority=priority,
        )

    def stock_at(self, warehouse, item, quantity, cost="100"):
        from django.utils import timezone

        return StockMovement.objects.create(
            item=item, warehouse=warehouse, movement_type=MovementType.RECEIPT,
            uom=self.kg, quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )

    def virgins(self):
        return list(self.plan().orders.filter(item=self.virgin))


class MoveItRatherThanBuyItTests(TransferTestCase):
    def test_spare_on_another_shelf_becomes_a_transfer(self):
        self.route()
        self.stock_at(self.depot, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(30))
        order = self.virgins()[0]
        self.assertEqual(order.kind, PlannedOrderKind.TRANSFER)
        self.assertEqual(order.from_warehouse, self.depot)
        self.assertEqual(order.lead_days, 2)

    def test_with_no_route_it_is_still_a_purchase(self):
        self.stock_at(self.depot, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.virgins()[0].kind, PlannedOrderKind.BUY)

    def test_an_empty_depot_is_still_a_purchase(self):
        self.route()
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.virgins()[0].kind, PlannedOrderKind.BUY)

    def test_what_counts_is_what_the_depot_could_promise(self):
        """
        Reading on-hand would rob Peter to pay Paul: the five tonnes
        look free right up until the order they were already covering
        ships short.
        """
        self.route()
        self.stock_at(self.depot, self.virgin, "5000")
        # Hyderabad has already promised its polymer to somebody.
        order = self.sell(self.virgin, "5000", self.day(20)).order
        line = order.lines.get()
        line.warehouse = self.depot
        line.save()
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.virgins()[0].kind, PlannedOrderKind.BUY)

    def test_a_lorry_that_cannot_get_here_in_time_is_not_the_answer(self):
        self.route(days=60)
        self.stock_at(self.depot, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(10))
        self.assertEqual(self.virgins()[0].kind, PlannedOrderKind.BUY)

    def test_routes_are_tried_in_their_stated_order(self):
        far = Warehouse.objects.create(code="N", name="Nagpur")
        self.route(days=2, source=self.depot, priority=2)
        self.route(days=1, source=far, priority=1)
        self.stock_at(self.depot, self.virgin, "5000")
        self.stock_at(far, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.virgins()[0].from_warehouse, far)

    def test_a_shelf_nobody_may_pick_from_is_not_a_source(self):
        hold = Warehouse.objects.create(
            code="Q", name="Quarantine", is_quarantine=True
        )
        self.route(source=hold)
        self.stock_at(hold, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.virgins()[0].kind, PlannedOrderKind.BUY)

    def test_a_made_item_is_not_moved(self):
        """
        Moving a made item between plants is a decision about where to
        run it, which is not this plan's to take.
        """
        self.route()
        self.stock_at(self.depot, self.tape, "50000")
        self.sell(self.fabric, "1000", self.day(30))
        tape = self.plan().orders.get(item=self.tape)
        self.assertEqual(tape.kind, PlannedOrderKind.MAKE)


class WhatATransferMustSayTests(TransferTestCase):
    def test_a_transfer_with_no_source_is_refused(self):
        from .models import PlannedOrder

        run = self.plan()
        with self.assertRaisesMessage(ValidationError, "which shelf it comes off"):
            PlannedOrder.objects.create(
                run=run, item=self.virgin, warehouse=self.plant,
                kind=PlannedOrderKind.TRANSFER, quantity=Decimal("100"),
                needed_by=TODAY, release_on=TODAY, lead_days=0,
            )

    def test_a_purchase_that_names_a_source_is_refused(self):
        from .models import PlannedOrder

        run = self.plan()
        with self.assertRaisesMessage(ValidationError, "to come from"):
            PlannedOrder.objects.create(
                run=run, item=self.virgin, warehouse=self.plant,
                kind=PlannedOrderKind.BUY, quantity=Decimal("100"),
                needed_by=TODAY, release_on=TODAY, lead_days=0,
                from_warehouse=self.depot,
            )

    def test_a_route_to_itself_is_refused(self):
        with self.assertRaises(Exception):
            TransferRoute.objects.create(
                from_warehouse=self.plant, to_warehouse=self.plant, lead_days=1
            )


class FirmingATransferTests(TransferTestCase):
    def firmed(self):
        self.route()
        self.stock_at(self.depot, self.virgin, "5000")
        self.sell(self.fabric, "1000", self.day(30))
        order = self.virgins()[0]
        return order, order.firm()

    def test_it_becomes_a_draft_transfer(self):
        """
        Draft, not dispatched. Moving stock on a planner's behalf at a
        date nobody has agreed is a fact this system does not invent.
        """
        order, transfer = self.firmed()
        self.assertEqual(transfer.status, TransferStatus.DRAFT)
        self.assertEqual(transfer.from_warehouse, self.depot)
        self.assertEqual(transfer.to_warehouse, self.plant)
        self.assertEqual(transfer.lines.get().quantity, order.quantity)
        self.assertEqual(transfer.transfer_date, order.release_on)

    def test_firming_twice_is_refused(self):
        order, _transfer = self.firmed()
        with self.assertRaises(ValidationError):
            order.firm()

    def test_a_cancelled_transfer_lapses_the_suggestion(self):
        order, transfer = self.firmed()
        self.assertFalse(order.has_lapsed())
        transfer.status = TransferStatus.CANCELLED
        transfer.save()
        self.assertTrue(order.has_lapsed())
