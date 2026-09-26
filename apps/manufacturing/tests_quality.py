"""
Quality where it meets the run.

Three joins: a specification generates the plan its goods are measured
against, a batch that did not pass does not go into another run or onto
a lorry, and the measurement explains part of the money.

The last is the one the whole build has been pointing at. A run ate
more polymer than the specification said; the specification also says
the fabric should have been 87.5 grammes a square metre. If quality
weighed it at 91, the fabric was four per cent heavy and the answer is
not "the blend was wrong" but "the loom was set too tight".
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party
from apps.inventory.models import Lot, TrackingMode
from apps.quality.models import (
    Characteristic,
    Disposition,
    Evaluation,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
)
from apps.quality.release import release_status

from .explain import explains, measured, output_lots
from .tests_orders import TODAY, RunTestCase
from .tests_woven import WovenTestCase


class ASpecificationWritesItsOwnInspectionPlanTests(WovenTestCase):
    def test_a_fabric_plan_checks_what_the_customer_was_quoted(self):
        self.fabric_item.tracking = TrackingMode.LOT
        self.fabric_item.save()
        fabric = self.fabric()
        plan = fabric.inspection_plan
        self.assertIsNotNone(plan)
        self.assertTrue(plan.is_computed)
        self.assertTrue(plan.is_mandatory)
        line = plan.lines.get()
        self.assertEqual(line.characteristic.code, "GSM")
        self.assertEqual(line.derived_from, "gsm")
        # Quoted 87.5 with a 5% tolerance: 83.125 to 91.875. Against
        # what the customer was quoted, not against the 87.489 the mesh
        # actually makes — the loom's own figure is checked when the
        # specification saves; this is the promise the goods are sold on.
        self.assertEqual(line.target, Decimal("87.500000"))
        self.assertEqual(line.lower_limit, Decimal("83.125000"))
        self.assertEqual(line.upper_limit, Decimal("91.875000"))
        self.assertEqual(line.evaluation, Evaluation.MEAN)

    def test_a_tape_plan_checks_the_denier(self):
        self.tape_item.tracking = TrackingMode.LOT
        self.tape_item.save()
        tape = self.tape()
        line = tape.inspection_plan.lines.get()
        self.assertEqual(line.characteristic.code, "DENIER")
        # 1,000 denier, 5%: 950 to 1,050.
        self.assertEqual(line.lower_limit, Decimal("950.000000"))
        self.assertEqual(line.upper_limit, Decimal("1050.000000"))

    def test_a_bag_plan_checks_what_a_sack_weighs(self):
        self.bag_item.tracking = TrackingMode.LOT
        self.bag_item.save()
        bag = self.bag()
        line = bag.inspection_plan.lines.get()
        self.assertEqual(line.characteristic.code, "BAGWT")
        # 111.436 g with a 5% tolerance, ten off the stack.
        self.assertAlmostEqual(line.target, Decimal("111.4363"), places=3)
        self.assertEqual(line.sample_size, 10)

    def test_moving_the_tolerance_moves_the_plan(self):
        self.fabric_item.tracking = TrackingMode.LOT
        self.fabric_item.save()
        fabric = self.fabric()
        fabric.gsm_tolerance_percent = Decimal("2")
        fabric.save()
        line = fabric.inspection_plan.lines.get()
        self.assertEqual(line.lower_limit, Decimal("85.750000"))
        self.assertEqual(line.upper_limit, Decimal("89.250000"))

    def test_a_computed_plan_refuses_a_hand_edit(self):
        self.fabric_item.tracking = TrackingMode.LOT
        self.fabric_item.save()
        fabric = self.fabric()
        line = fabric.inspection_plan.lines.get()
        line.upper_limit = Decimal("200")
        with self.assertRaises(ValidationError):
            line.save()

    def test_an_untracked_item_gets_an_advisory_plan_rather_than_none(self):
        # A gate that cannot say which batch it is gating is not a gate,
        # and refusing outright would leave a plant with no record of
        # what it checks.
        fabric = self.fabric()
        self.assertFalse(fabric.inspection_plan.is_mandatory)


class AHeldBatchGoesNowhereTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        self.drum = Lot.objects.create(item=self.virgin, code="PP-A")
        self.stock(self.virgin, "3000", "100", lot=self.drum)
        self.gsm_unit = self.kg
        self.denier = Characteristic.objects.create(
            code="DEN", name="Denier", uom=self.kg
        )
        self.plan = InspectionPlan.objects.create(
            item=self.virgin, is_mandatory=True
        )
        self.line = PlanLine.objects.create(
            plan=self.plan, characteristic=self.denier,
            target=Decimal("1000"), lower_limit=Decimal("950"),
            upper_limit=Decimal("1050"), sample_size=1,
        )

    def inspect(self, value, **kwargs):
        inspection = Inspection.objects.create(
            lot=self.drum, plan=self.plan, inspected_on=TODAY, **kwargs
        )
        Reading.objects.create(
            inspection=inspection, plan_line=self.line, value=Decimal(str(value))
        )
        inspection.post()
        return inspection

    def issue_the_drum(self):
        run = self.order("1000")
        run.release(TODAY)
        return self.issue_with_lots(
            run, [(self.virgin, Decimal("100"), self.drum)]
        )

    def test_an_uninspected_batch_cannot_go_into_a_run(self):
        with self.assertRaises(ValidationError) as caught:
            self.issue_the_drum().post()
        self.assertIn("Not yet inspected is not passed", str(caught.exception))

    def test_a_rejected_one_cannot_either(self):
        self.inspect(1200)
        with self.assertRaises(ValidationError) as caught:
            self.issue_the_drum().post()
        self.assertIn("is held", str(caught.exception))

    def test_a_passed_one_can(self):
        self.inspect(1000)
        self.issue_the_drum().post()
        self.assertEqual(
            self.virgin.on_hand_at(self.plant), Decimal("4900.0000")
        )

    def test_and_so_can_one_taken_by_concession(self):
        manager = Party.objects.create(code="MGR", name="Plant manager")
        self.inspect(
            1200, disposition=Disposition.CONCESSION, decided_by=manager,
            decision_note="Heavy denier accepted for a woven liner.",
        )
        self.issue_the_drum().post()
        self.assertEqual(release_status(self.drum), "released")


class TheMeasurementExplainsTheMoneyTests(RunTestCase):
    def setUp(self):
        super().setUp()
        self.tape.tracking = TrackingMode.LOT
        self.tape.save()
        self.reel = Lot.objects.create(item=self.tape, code="T-001")
        gsm = Characteristic.objects.create(
            code="GSM", name="Grammes per square metre", uom=self.kg
        )
        # Advisory: this run's output is being measured, not gated.
        self.plan = InspectionPlan.objects.create(
            item=self.tape, is_mandatory=False
        )
        self.line = PlanLine.objects.create(
            plan=self.plan, characteristic=gsm, target=Decimal("87.5"),
            lower_limit=Decimal("83.125"), upper_limit=Decimal("91.875"),
            sample_size=1, evaluation=Evaluation.MEAN, derived_from="gsm",
        )

    def a_run_measured_at(self, value, over="0"):
        run = self.order("1000")
        run.release(TODAY)
        rows = [
            (component.item, component.quantity_required)
            for component in run.components.all()
        ]
        rows[0] = (self.virgin, rows[0][1] + Decimal(over))
        self.issue(run, rows).post()
        self.produce(run, "1000", lot=self.reel).post()
        inspection = Inspection.objects.create(
            lot=self.reel, plan=self.plan, inspected_on=TODAY
        )
        Reading.objects.create(
            inspection=inspection, plan_line=self.line, value=Decimal(str(value))
        )
        inspection.post()
        return run

    def test_a_run_knows_which_batches_it_made(self):
        run = self.a_run_measured_at("87.5")
        self.assertEqual(output_lots(run), [self.reel])

    def test_how_far_the_output_was_from_what_it_should_have_been(self):
        run = self.a_run_measured_at("91")
        reading = measured(run)
        self.assertEqual(reading["target"], Decimal("87.500000"))
        self.assertEqual(reading["measured"], Decimal("91.000000"))
        # 3.5 over 87.5 is four per cent.
        self.assertAlmostEqual(
            reading["deviation_percent"], Decimal("4"), places=6
        )

    def test_and_how_much_of_the_overrun_that_accounts_for(self):
        run = self.a_run_measured_at("91", over="150")
        report = explains(run)
        # Four per cent of a planned material cost of 93,195.88.
        self.assertAlmostEqual(
            report["accounted_for"], Decimal("3727.84"), places=2
        )
        self.assertGreater(report["share_percent"], Decimal("20"))
        self.assertIn("does not claim the rest", report["note"])

    def test_a_run_nobody_measured_has_nothing_to_say(self):
        unmeasured = Lot.objects.create(item=self.tape, code="T-002")
        run = self.order("1000")
        run.release(TODAY)
        self.full_issue(run).post()
        self.produce(run, "1000", lot=unmeasured).post()
        self.assertIsNone(measured(run))
        self.assertIsNone(explains(run))

    def test_a_run_that_came_out_light_claims_no_share_of_an_overrun(self):
        # It explains a saving, not an overrun, and dividing by the
        # overrun would report a confident percentage of the wrong thing.
        run = self.a_run_measured_at("84", over="150")
        report = explains(run)
        self.assertLess(report["accounted_for"], Decimal("0"))
        self.assertIsNone(report["share_percent"])
