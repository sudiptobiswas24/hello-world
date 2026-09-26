"""
Output that draws its own material, and batches put back through the
plant.

Two features a continuous line cannot run without. On an extruder the
blend is metered in and nobody stands at a store counter, so every
kilo booked as tape has to draw its own polymer. And a roll that
missed its GSM is not scrap — it is re-wound, which consumes it and
produces good fabric in its place.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Lot, TrackingMode
from apps.quality.models import (
    Characteristic,
    Disposition,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from apps.quality.release import release_status

from .bom import BillOfMaterials, BomComponent
from .orders import MaterialIssue, MaterialIssueLine, WorkOrder, WorkOrderStatus
from .tests_orders import TODAY, RunTestCase


class BackflushTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.bom.backflush = True
        self.bom.save()

    def released(self, quantity="1000"):
        order = self.order(quantity)
        order.release(TODAY)
        return order

    def test_booking_output_draws_the_material_for_it(self):
        order = self.released()
        self.assertTrue(order.backflush)
        entry = self.produce(order, "500")
        entry.post()
        # Half the run's output, so half its frozen requirement:
        # 773.195876 / 2 = 386.597938, stored at an issue line's four
        # places as 386.5979.
        component = order.components.get(item=self.virgin)
        self.assertEqual(component.quantity_issued(), Decimal("386.5979"))

    def test_scrap_ate_material_too(self):
        """
        The trap this exists to avoid. A backflush counting only good
        output under-issues by the scrap every time, and the shortfall
        turns up at close as a favourable material variance — the blend
        reading light when in fact the issue was never made.
        """
        order = self.released()
        entry = self.produce(order, "600", scrapped="50")
        entry.post()
        # 650 of 1,000 consumed: 773.195876 x 0.65 = 502.5773194.
        self.assertEqual(
            order.components.get(item=self.virgin).quantity_issued(),
            Decimal("502.5773"),
        )

    def test_it_draws_through_a_real_issue_document(self):
        """
        Its own document, so the work-in-progress balance, the
        variance and the void path read a backflushed run through
        exactly the same rows as a hand-issued one.
        """
        order = self.released()
        entry = self.produce(order, "500")
        entry.post()
        self.assertIsNotNone(entry.backflush_issue)
        self.assertTrue(entry.backflush_issue.posted)
        self.assertEqual(entry.backflush_issue.work_order, order)
        self.assertEqual(order.posted_issues().count(), 1)

    def test_voiding_the_output_puts_the_material_back(self):
        order = self.released()
        entry = self.produce(order, "500")
        entry.post()
        entry.void(TODAY)
        self.assertTrue(entry.backflush_issue.is_voided())
        self.assertEqual(
            order.components.get(item=self.virgin).quantity_issued(), Decimal("0")
        )

    def test_a_closed_backflushed_run_holds_nothing(self):
        """
        The test that matters most: everything that went in has come
        out as stock, as scrap or as variance.
        """
        order = self.released()
        self.produce(order, "1000").post()
        order.close(TODAY)
        self.assertEqual(order.wip_balance(), Decimal("0"))

    def test_a_run_that_does_not_backflush_draws_nothing_by_itself(self):
        self.bom.backflush = False
        self.bom.save()
        order = self.released()
        entry = self.produce(order, "500")
        entry.post()
        self.assertIsNone(entry.backflush_issue)
        self.assertEqual(order.posted_issues().count(), 0)

    def test_the_setting_is_frozen_at_release(self):
        """
        Turning it on halfway through a run would have the first half
        issued by hand and the second half drawn automatically, and
        the two would meet in the middle as a variance nobody can
        explain.
        """
        self.bom.backflush = False
        self.bom.save()
        order = self.released()
        self.bom.backflush = True
        self.bom.save()
        entry = self.produce(order, "500")
        entry.post()
        self.assertIsNone(entry.backflush_issue)

    def test_it_draws_against_what_the_run_was_released_on(self):
        """
        Scaled off the frozen requirement, not recomputed from the
        bill of materials — which will have moved by the time the last
        shift books its output.
        """
        order = self.released()
        self.bom.components.filter(item=self.virgin).update(
            quantity=Decimal("90")
        )
        entry = self.produce(order, "500")
        entry.post()
        self.assertEqual(
            order.components.get(item=self.virgin).quantity_issued(),
            Decimal("386.5979"),
        )

    def test_a_backflush_with_nothing_on_the_shelf_takes_the_output_with_it(self):
        order = self.released("100000")
        entry = self.produce(order, "100000")
        with self.assertRaises(ValidationError):
            entry.post()
        entry.refresh_from_db()
        self.assertFalse(entry.posted)


class ReworkTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.tape.tracking = TrackingMode.LOT
        self.tape.save()
        self.characteristic = Characteristic.objects.create(
            code="DEN", name="Denier", uom=self.kg
        )
        self.plan = InspectionPlan.objects.create(
            item=self.tape, name="Tape", is_mandatory=True
        )
        PlanLine.objects.create(
            plan=self.plan, characteristic=self.characteristic,
            lower_limit=Decimal("950"), upper_limit=Decimal("1050"),
            line_number=1,
        )
        # A rework recipe: the failed tape goes back in, plus a little
        # fresh polymer, and good tape comes out.
        self.rework_bom = BillOfMaterials.objects.create(
            item=self.tape, name="Re-draw off-spec tape", version=2,
            quantity_produced=Decimal("100"), uom=self.kg, is_default=False,
            is_rework=True,
        )
        BomComponent.objects.create(
            bom=self.rework_bom, item=self.tape, quantity=Decimal("98"),
            uom=self.kg, line_number=1,
        )
        BomComponent.objects.create(
            bom=self.rework_bom, item=self.virgin, quantity=Decimal("4"),
            uom=self.kg, line_number=2,
        )

    def failed_lot(self, quantity="500", value="1200"):
        lot = Lot.objects.create(item=self.tape, code="T-BAD")
        self.stock(self.tape, quantity, "90", lot=lot)
        inspection = Inspection.objects.create(
            lot=lot, plan=self.plan, inspected_on=TODAY,
        )
        Reading.objects.create(
            inspection=inspection, plan_line=self.plan.lines.get(),
            value=Decimal(value),
        )
        inspection.post()
        return lot

    def rework_order(self, lot, quantity="480"):
        return WorkOrder.objects.create(
            item=self.tape, bom=self.rework_bom,
            quantity_ordered=Decimal(quantity), uom=self.kg,
            warehouse=self.plant, rework_of=lot,
        )


class WhatARecipeMayConsumeTests(ReworkTestCase):
    def test_an_ordinary_recipe_may_not_eat_what_it_makes(self):
        """
        A loop the explosion cuts without saying so, leaving a run
        quietly asking for a batch of itself.
        """
        with self.assertRaises(ValidationError):
            BomComponent.objects.create(
                bom=self.bom, item=self.tape, quantity=Decimal("10"),
                uom=self.kg, line_number=9,
            )

    def test_a_rework_recipe_with_no_line_for_the_failed_batch_is_refused(self):
        """
        Without it, rework makes good stock out of nothing and leaves
        the bad roll on the shelf still carrying its value.
        """
        empty = BillOfMaterials.objects.create(
            item=self.tape, name="Bad rework", version=3,
            quantity_produced=Decimal("100"), uom=self.kg, is_default=False,
            is_rework=True,
        )
        BomComponent.objects.create(
            bom=empty, item=self.virgin, quantity=Decimal("10"), uom=self.kg,
            line_number=1,
        )
        with self.assertRaises(ValidationError):
            empty.check_rework()

    def test_a_proper_rework_recipe_passes(self):
        self.rework_bom.check_rework()


class RaisingAReworkOrderTests(ReworkTestCase):
    def test_it_names_the_batch_it_is_putting_right(self):
        lot = self.failed_lot()
        self.assertEqual(release_status(lot), "held")
        order = self.rework_order(lot)
        order.release(TODAY)
        self.assertTrue(order.is_rework())
        self.assertEqual(order.status, WorkOrderStatus.RELEASED)

    def test_a_rework_recipe_with_no_batch_named_is_refused(self):
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.rework_bom, quantity_ordered=Decimal("480"),
            uom=self.kg, warehouse=self.plant,
        )
        # By the message, not merely by something going wrong: without
        # this check the release is refused anyway, further down and
        # for an unrelated reason, so a bare assertRaises passes with
        # the guard removed.
        with self.assertRaisesMessage(
            ValidationError, "does not say which batch it is putting right"
        ):
            order.release(TODAY)

    def test_an_ordinary_recipe_with_a_batch_named_is_refused(self):
        lot = self.failed_lot()
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, rework_of=lot,
        )
        with self.assertRaises(ValidationError):
            order.release(TODAY)

    def test_reworking_a_batch_nothing_failed_is_refused(self):
        """
        Rework consumes stock at full value and books it back as good.
        Pointed at a batch nothing is wrong with, it is a way of
        laundering a shortage.
        """
        lot = self.failed_lot()
        inspection = Inspection.objects.filter(lot=lot).get()
        inspection.void("Re-measured.")
        good = Inspection.objects.create(
            lot=lot, plan=self.plan, inspected_on=TODAY
        )
        Reading.objects.create(
            inspection=good, plan_line=self.plan.lines.get(),
            value=Decimal("1000"),
        )
        good.post()
        self.assertEqual(release_status(lot), "released")
        with self.assertRaises(ValidationError):
            self.rework_order(lot).release(TODAY)

    def test_a_batch_of_something_else_is_refused(self):
        other = Lot.objects.create(item=self.regrind, code="R-1")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.rework_bom, quantity_ordered=Decimal("480"),
            uom=self.kg, warehouse=self.plant, rework_of=other,
        )
        with self.assertRaisesMessage(
            ValidationError, "is a batch of REGRIND - Reprocessed waste"
        ):
            order.release(TODAY)


class DrawingTheFailedBatchTests(ReworkTestCase):
    def issue_rework(self, order, lot, quantity="470.4"):
        issue = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=issue, item=self.tape, quantity=Decimal(quantity),
            uom=self.kg, lot=lot, line_number=1,
        )
        return issue

    def test_the_held_batch_may_go_into_the_run_that_puts_it_right(self):
        """
        The exemption the whole disposition rests on. Without it a
        rework order cannot consume the roll it was raised for.
        """
        lot = self.failed_lot()
        order = self.rework_order(lot)
        order.release(TODAY)
        self.issue_rework(order, lot).post()
        self.assertAlmostEqual(
            order.components.get(item=self.tape).quantity_issued(),
            Decimal("470.4"), places=4,
        )

    def test_it_may_not_draw_good_stock_of_the_same_item_instead(self):
        """
        The exemption is exactly one batch wide. A rework run drawing
        good stock is salvage on paper and a shortage in fact.
        """
        lot = self.failed_lot()
        good = Lot.objects.create(item=self.tape, code="T-GOOD")
        self.stock(self.tape, "500", "90", lot=good)
        order = self.rework_order(lot)
        order.release(TODAY)
        with self.assertRaises(ValidationError):
            self.issue_rework(order, good).post()

    def test_it_may_not_draw_the_item_with_no_batch_named(self):
        lot = self.failed_lot()
        order = self.rework_order(lot)
        order.release(TODAY)
        with self.assertRaises(ValidationError):
            self.issue_rework(order, None).post()

    def test_a_held_batch_still_may_not_go_into_an_ordinary_run(self):
        lot = self.failed_lot()
        order = self.order()
        order.release(TODAY)
        issue = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=issue, item=self.tape, quantity=Decimal("10"), uom=self.kg,
            lot=lot, line_number=1,
        )
        with self.assertRaises(ValidationError):
            issue.post()

    def test_fresh_material_on_a_rework_run_is_checked_as_usual(self):
        lot = self.failed_lot()
        order = self.rework_order(lot)
        order.release(TODAY)
        issue = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=issue, item=self.virgin, quantity=Decimal("19.2"),
            uom=self.kg, line_number=1,
        )
        issue.post()
        self.assertAlmostEqual(
            order.components.get(item=self.virgin).quantity_issued(),
            Decimal("19.2"), places=4,
        )
