"""
Turning a suggestion into something that can be acted on — and, just
as importantly, what happens the next time the plan runs.

The test that matters most here is the last one in the first class:
firm everything, re-plan, and nothing is suggested twice. A planner
handed Monday's plan again on Tuesday stops reading it on Wednesday.
"""

from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command

from apps.manufacturing.orders import WorkOrder, WorkOrderStatus
from apps.purchasing.models import PurchaseRequisition

from .models import PlannedOrderStatus
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class FirmingTests(PlanningTestCase):
    def test_a_make_becomes_a_draft_run_carrying_its_dates(self):
        """
        Draft, not released.

        Release freezes the requirements and the planned cost against
        the shelf as it stands, and doing that on a planner's behalf at
        a date they have not yet agreed to invents a fact.
        """
        line = self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        order = fabric.firm()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT)
        self.assertEqual(order.quantity_ordered, Decimal("1000"))
        self.assertEqual(order.bom, self.fabric_bom)
        self.assertEqual(order.routing, self.fabric_bom.routing)
        self.assertEqual(order.scheduled_start, fabric.release_on)
        self.assertEqual(order.scheduled_end, fabric.needed_by)
        self.assertEqual(order.sales_order_line, line)

    def test_a_run_covering_two_customers_belongs_to_neither(self):
        """
        Picking the first would make the coverage report say something
        false about the second.
        """
        self.sell(self.fabric, "600", self.day(30))
        self.sell(self.fabric, "400", self.day(30))
        order = self.orders()["FAB-10X10"].firm()
        self.assertIsNone(order.sales_order_line)

    def test_a_buy_becomes_a_requisition_line(self):
        self.sell(self.fabric, "1000", self.day(30))
        virgin = self.orders()["PP-RAFFIA"]
        line = virgin.firm()
        self.assertEqual(line.item, self.virgin)
        self.assertEqual(line.quantity, virgin.quantity)
        self.assertEqual(line.requisition.needed_by, virgin.release_on)
        self.assertEqual(line.requisition.requested_by, self.buyer)

    def test_two_buys_for_the_same_day_share_one_requisition(self):
        self.sell(self.fabric, "1000", self.day(30))
        found = self.orders()
        first = found["PP-RAFFIA"].firm()
        second = found["REGRIND"].firm()
        self.assertEqual(first.requisition, second.requisition)
        self.assertEqual(PurchaseRequisition.objects.count(), 1)

    def test_a_buy_with_nobody_to_ask_is_refused(self):
        self.settings.requisition_requester = None
        self.settings.save()
        self.sell(self.fabric, "1000", self.day(30))
        with self.assertRaises(ValidationError):
            self.orders()["PP-RAFFIA"].firm()

    def test_firming_the_whole_plan_and_running_it_again_suggests_nothing(self):
        self.sell(self.fabric, "1000", self.day(30))
        for order in list(self.plan().suggestions()):
            order.firm()
        self.assertEqual(self.plan().orders.count(), 0)


class SayingNoTests(PlanningTestCase):
    def test_firming_twice_is_refused(self):
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        fabric.firm()
        with self.assertRaises(ValidationError):
            fabric.firm()
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_cancelling_a_firmed_order_is_refused(self):
        """
        The work order is the fact now, and cancelling the suggestion
        behind it would leave the fact standing with nothing pointing
        at it.
        """
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        fabric.firm()
        with self.assertRaises(ValidationError):
            fabric.cancel()

    def test_a_cancelled_suggestion_cannot_be_firmed(self):
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        fabric.cancel()
        self.assertEqual(fabric.status, PlannedOrderStatus.CANCELLED)
        with self.assertRaises(ValidationError):
            fabric.firm()

    def test_a_cancelled_suggestion_drops_out_of_the_run(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        fabric = run.orders.get(item=self.fabric)
        fabric.cancel()
        self.assertNotIn(fabric, run.suggestions())
        self.assertIn(fabric, run.orders.all())


class WhenTheFirmedDocumentGoesAwayTests(PlanningTestCase):
    """
    The mirror of firming, and the one nobody looks for.

    A suggestion firmed into a run that is later cancelled reads as
    handled from the planning side and does not exist on the shop
    floor. Neither end shows it, which is exactly how a shortage
    survives a planning system.
    """

    def firmed_fabric(self):
        self.sell(self.fabric, "1000", self.day(30))
        order = self.orders()["FAB-10X10"]
        order.firm()
        return order

    def test_a_cancelled_run_leaves_the_suggestion_lapsed(self):
        order = self.firmed_fabric()
        self.assertFalse(order.has_lapsed())
        order.work_order.cancel()
        self.assertTrue(order.has_lapsed())
        self.assertIsNone(order.firmed_into())

    def test_a_lapsed_suggestion_can_be_firmed_again(self):
        order = self.firmed_fabric()
        order.work_order.cancel()
        again = order.firm()
        self.assertNotEqual(again, order.work_order_id)
        self.assertEqual(again.status, WorkOrderStatus.DRAFT)

    def test_a_lapsed_suggestion_can_be_cancelled(self):
        order = self.firmed_fabric()
        order.work_order.cancel()
        order.cancel()
        self.assertEqual(order.status, PlannedOrderStatus.CANCELLED)

    def test_a_cancelled_requisition_lapses_a_firmed_buy(self):
        self.sell(self.fabric, "1000", self.day(30))
        virgin = self.orders()["PP-RAFFIA"]
        virgin.firm()
        self.assertFalse(virgin.has_lapsed())
        virgin.requisition_line.requisition.cancel()
        self.assertTrue(virgin.has_lapsed())

    def test_the_run_lists_what_has_lapsed(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        order = run.orders.get(item=self.fabric)
        order.firm()
        self.assertEqual(run.lapsed(), [])
        order.work_order.cancel()
        self.assertEqual([o.pk for o in run.lapsed()], [order.pk])


class StalePlansTests(PlanningTestCase):
    def test_firming_from_a_plan_a_later_one_has_replaced_is_refused(self):
        """
        Two plans run before either is firmed both find the same
        shortage, and firming both orders it twice. The second plan
        knows about the first's suggestions; the first knows nothing
        about the second.
        """
        self.sell(self.fabric, "1000", self.day(30))
        first = self.plan()
        self.plan()
        with self.assertRaises(ValidationError):
            first.orders.get(item=self.fabric).firm()

    def test_the_latest_plan_firms_normally(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.plan()
        latest = self.plan()
        self.assertIsNotNone(latest.orders.get(item=self.fabric).firm())

    def test_a_plan_for_another_warehouse_does_not_make_this_one_stale(self):
        from apps.inventory.models import Warehouse

        self.sell(self.fabric, "1000", self.day(30))
        mine = self.plan()
        self.plan(Warehouse.objects.create(code="N", name="Nagpur"))
        self.assertIsNotNone(mine.orders.get(item=self.fabric).firm())


class RunsAreSnapshotsTests(PlanningTestCase):
    def test_a_suggestion_is_not_supply_for_the_next_run(self):
        """
        Nothing happened in the world when a suggestion was written,
        so the next run must find the same shortage.
        """
        self.sell(self.fabric, "1000", self.day(30))
        first, second = self.plan(), self.plan()
        self.assertEqual(first.orders.count(), second.orders.count())
        self.assertNotEqual(first.pk, second.pk)

    def test_an_old_run_is_left_as_it_was(self):
        self.sell(self.fabric, "1000", self.day(30))
        first = self.plan()
        before = [(o.item_id, o.quantity) for o in first.orders.all()]
        self.stock(self.fabric, "5000")
        self.assertEqual(self.plan().orders.count(), 0)
        first.refresh_from_db()
        self.assertEqual(
            [(o.item_id, o.quantity) for o in first.orders.all()], before
        )

    def test_a_run_says_whether_everything_was_netted(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        self.assertTrue(run.is_complete())
        self.assertEqual(run.deferred_demand, "")

    def test_a_run_over_a_looping_graph_says_what_it_could_not_net(self):
        from apps.manufacturing.bom import BillOfMaterials, BomComponent

        reprocess = BillOfMaterials.objects.create(
            item=self.regrind, name="Reprocess",
            quantity_produced=Decimal("100"), uom=self.kg,
        )
        BomComponent.objects.create(
            bom=reprocess, item=self.fabric, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )
        self.sell(self.fabric, "1000", self.day(60))
        run = self.plan()
        self.assertIn("not planned through", run.cut_links)
        self.assertFalse(run.is_complete())
        self.assertIn("FAB-10X10", run.deferred_demand)

    def test_what_is_late_is_listed(self):
        self.sell(self.fabric, "1000", self.day(3))
        run = self.plan()
        self.assertEqual(
            {order.item.sku for order in run.late()}, {"PP-RAFFIA", "REGRIND"}
        )


class TheCommandTests(PlanningTestCase):
    def run_it(self, *args):
        out = StringIO()
        call_command("run_mrp", "P", "--on", str(TODAY), *args, stdout=out)
        return out.getvalue()

    def test_it_prints_each_order_and_the_reason_for_it(self):
        line = self.sell(self.fabric, "1000", self.day(30))
        report = self.run_it()
        self.assertIn("Make 1000.0000 kg FAB-10X10", report)
        self.assertIn("Buy 788.9755 kg PP-RAFFIA", report)
        self.assertIn(line.order.number, report)

    def test_it_leads_with_what_is_late(self):
        self.sell(self.fabric, "1000", self.day(3))
        report = self.run_it()
        self.assertIn("LATE by 6 days", report)
        self.assertLess(report.index("LATE"), report.index("FAB-10X10"))

    def test_it_says_when_there_is_nothing_to_do(self):
        self.assertIn("Nothing to raise.", self.run_it())

    def test_it_can_firm_what_it_found(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.assertIn("Firmed 4", self.run_it("--firm"))
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(PurchaseRequisition.objects.count(), 1)

    def test_it_refuses_a_warehouse_it_cannot_find(self):
        with self.assertRaises(CommandError):
            call_command("run_mrp", "NOPE")
