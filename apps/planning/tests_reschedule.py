"""
Orders that already exist and are dated wrong.

A plant a year old has hundreds of open orders, and most of what goes
wrong is not that something was never ordered — it is that what was
ordered is coming at the wrong time. These are the two sentences a
planner acts on every morning: pull this in, push that out.
"""

import datetime
from decimal import Decimal

from apps.manufacturing.orders import WorkOrder
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)

from .models import RescheduleAction, SupplySource
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class RescheduleTestCase(PlanningTestCase):
    def purchase(self, item, quantity, expected, confirm=True):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=item, uom=self.kg, quantity=Decimal(quantity),
            unit_price=Decimal("90"), expected_date=expected,
        )
        if confirm:
            order.confirm()
        return line

    def actions(self, run=None):
        run = run or self.plan()
        return list(run.actions.all())


class PullItInTests(RescheduleTestCase):
    def test_a_purchase_landing_after_the_run_that_wants_it(self):
        """
        The message the plant notices first: the polymer is ordered,
        it is coming, and it is coming too late.
        """
        self.sell(self.fabric, "1000", self.day(30))
        # The tape run wants polymer on 29 June — day 28, since day 0
        # is the 1st — and the order lands on 11 July.
        self.purchase(self.virgin, "5000", self.day(40))
        expedite = [
            a for a in self.actions() if a.action == RescheduleAction.EXPEDITE
        ]
        self.assertEqual(len(expedite), 1)
        action = expedite[0]
        self.assertEqual(action.item, self.virgin)
        self.assertEqual(action.source, SupplySource.PURCHASE)
        self.assertEqual(action.scheduled_on, self.day(40))
        self.assertEqual(action.wanted_on, datetime.date(2026, 6, 29))
        self.assertEqual(action.days, 12)
        self.assertIn("pull in", action.sentence())

    def test_the_message_names_what_wants_it(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.purchase(self.virgin, "5000", self.day(40))
        action = self.plan().expedites().get(item=self.virgin)
        self.assertIn("planned", action.because)
        self.assertIn("TAPE-1000", action.because)

    def test_a_customer_line_is_named_when_it_is_the_driver(self):
        self.purchase(self.fabric, "1000", self.day(40))
        line = self.sell(self.fabric, "1000", self.day(20))
        action = self.plan().expedites().get(item=self.fabric)
        self.assertIn(line.order.number, action.because)
        self.assertEqual(action.wanted_on, self.day(20))

    def test_a_late_work_order_is_pulled_in_too(self):
        self.sell(self.tape, "1000", self.day(10))
        WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=self.day(20),
            scheduled_end=self.day(25),
        )
        action = self.plan().expedites().get(item=self.tape)
        self.assertEqual(action.source, SupplySource.WORK_ORDER)
        self.assertEqual(action.scheduled_on, self.day(25))
        self.assertEqual(action.wanted_on, self.day(10))
        self.assertEqual(action.days, 15)

    def test_a_requisition_can_be_pulled_in(self):
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.buyer, request_date=TODAY, needed_by=self.day(40),
        )
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.fabric, uom=self.kg,
            quantity=Decimal("1000"),
        )
        self.sell(self.fabric, "1000", self.day(20))
        action = self.plan().expedites().get(item=self.fabric)
        self.assertEqual(action.source, SupplySource.REQUISITION)


class PushItOutTests(RescheduleTestCase):
    def test_buying_too_early_is_money_in_a_warehouse(self):
        self.purchase(self.fabric, "1000", self.day(5))
        self.sell(self.fabric, "1000", self.day(60))
        action = self.plan().defers().get(item=self.fabric)
        self.assertEqual(action.scheduled_on, self.day(5))
        self.assertEqual(action.wanted_on, self.day(60))
        self.assertEqual(action.days, 55)
        self.assertIn("push out", action.sentence())

    def test_a_date_inside_the_tolerance_is_not_worth_saying(self):
        """
        Nought tolerance produces a message for every order in the
        plant every morning, which is how action messages come to be
        ignored.
        """
        self.purchase(self.fabric, "1000", self.day(28))
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.actions(), [])

    def test_tightening_the_tolerance_surfaces_it(self):
        self.settings.reschedule_tolerance_days = 0
        self.settings.save()
        self.purchase(self.fabric, "1000", self.day(28))
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.plan().defers().count(), 1)


class CancelTests(RescheduleTestCase):
    def test_an_order_nothing_wants_is_reported(self):
        self.purchase(self.fabric, "1000", self.day(20))
        actions = self.actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, RescheduleAction.CANCEL)
        self.assertEqual(actions[0].quantity, Decimal("1000"))
        self.assertIsNone(actions[0].wanted_on)
        self.assertIn("covers nothing", actions[0].sentence())

    def test_stock_kept_deliberately_is_not_stock_nobody_wants(self):
        """
        A plan that told a buyer to cancel the order holding the safety
        floor would spend the next month telling them to raise it
        again.
        """
        self.rule(self.fabric, minimum="1000")
        self.purchase(self.fabric, "1000", self.day(20))
        self.assertEqual(self.actions(), [])

    def test_only_the_excess_over_the_floor_is_cancellable(self):
        self.rule(self.fabric, minimum="600")
        self.purchase(self.fabric, "1000", self.day(20))
        action = self.plan().cancels().get()
        self.assertEqual(action.quantity, Decimal("400"))

    def test_part_of_an_order_covering_demand_is_not_cancelled(self):
        self.purchase(self.fabric, "1000", self.day(20))
        self.sell(self.fabric, "1000", self.day(20))
        self.assertEqual(self.plan().cancels().count(), 0)

    def test_an_order_half_wanted_reports_both_halves(self):
        """
        Twenty tonnes of which six is wanted and fourteen is not has
        two separate things wrong with it. Saying only that the six
        should move leaves the fourteen on a lorry.
        """
        self.purchase(self.fabric, "1000", self.day(5))
        self.sell(self.fabric, "400", self.day(60))
        run = self.plan()
        cancel = run.cancels().get()
        self.assertEqual(cancel.quantity, Decimal("600"))
        self.assertIn("the other 400.0000 of it is spoken for", cancel.because)
        defer = run.defers().get()
        self.assertEqual(defer.quantity, Decimal("400"))


class WhatCannotBeMovedTests(RescheduleTestCase):
    def test_a_byproduct_is_never_expedited(self):
        """
        Reground trim comes off when the run comes off. Telling a
        planner to pull it in is telling them to pull in something
        that does not exist yet.
        """
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
            scheduled_end=self.day(40),
        )
        order.release(TODAY)
        self.issue_everything(order)
        self.sell(self.regrind, "4870", self.day(5))
        actions = [a for a in self.actions() if a.item == self.regrind]
        self.assertEqual(actions, [])

    def test_a_suggestion_this_run_made_is_not_an_order_to_move(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        self.assertTrue(run.orders.exists())
        self.assertEqual(run.actions.count(), 0)

    def test_a_draft_sale_drives_nothing(self):
        self.purchase(self.fabric, "1000", self.day(20))
        self.assertEqual(self.plan().cancels().count(), 1)


class EarliestServesEarliestTests(RescheduleTestCase):
    def test_two_orders_are_matched_to_two_call_offs_in_turn(self):
        """
        Earliest supply to earliest demand, which is what a store
        does. Matching them any other way reports the wrong one.
        """
        self.purchase(self.fabric, "1000", self.day(50))
        self.purchase(self.fabric, "1000", self.day(55))
        self.sell(self.fabric, "1000", self.day(20))
        self.sell(self.fabric, "1000", self.day(25))
        actions = sorted(self.plan().expedites(), key=lambda a: a.scheduled_on)
        self.assertEqual(len(actions), 2)
        self.assertEqual(
            [(a.scheduled_on, a.wanted_on) for a in actions],
            [(self.day(50), self.day(20)), (self.day(55), self.day(25))],
        )

    def test_one_order_serving_two_call_offs_is_urgent_by_the_first(self):
        """
        A single order covering several call-offs is late by the
        earliest of them, not the latest. Read the other way it
        understates the lateness by the gap between the two, which is
        exactly the number a planner is trying to see.
        """
        self.purchase(self.fabric, "2000", self.day(50))
        self.sell(self.fabric, "1000", self.day(20))
        self.sell(self.fabric, "1000", self.day(30))
        action = self.plan().expedites().get()
        self.assertEqual(action.wanted_on, self.day(20))
        self.assertEqual(action.days, 30)
        self.assertEqual(action.quantity, Decimal("2000"))

    def test_the_earlier_order_takes_the_earlier_call_off(self):
        """
        Matched by date rather than in whatever order the query
        returned them. Unordered, the later order is pegged to the
        earlier demand and both messages name the wrong distance.
        """
        # Written later-first on purpose, so an unsorted walk sees the
        # far order before the near one.
        late = self.purchase(self.fabric, "1000", self.day(80))
        early = self.purchase(self.fabric, "1000", self.day(50))
        self.sell(self.fabric, "1000", self.day(20))
        self.sell(self.fabric, "1000", self.day(60))
        by_line = {
            a.purchase_order_line_id: a for a in self.plan().expedites()
        }
        self.assertEqual(by_line[early.pk].wanted_on, self.day(20))
        self.assertEqual(by_line[late.pk].wanted_on, self.day(60))

    def test_stock_on_the_shelf_serves_the_first_call_off(self):
        self.stock(self.fabric, "1000")
        self.purchase(self.fabric, "1000", self.day(50))
        self.sell(self.fabric, "1000", self.day(20))
        self.sell(self.fabric, "1000", self.day(25))
        # The shelf covers the first, so the order is wanted by the
        # second — not by the first.
        action = self.plan().expedites().get()
        self.assertEqual(action.wanted_on, self.day(25))
