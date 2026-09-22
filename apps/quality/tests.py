"""
What was measured, what it should have been, and what was decided.

The worked example is a woven fabric quoted at 87.5 grammes a square
metre with a five per cent tolerance, so the limits are 83.125 and
91.875. Three readings of 80, 95 and 87.5 average to exactly 87.5 and
contain two that are nowhere near it — which is the case that decides
whether a plan evaluates every reading or their mean, and the two
disagree.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import Party, UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import Item, Lot, TrackingMode

from .models import (
    Characteristic,
    CharacteristicKind,
    Disposition,
    Evaluation,
    Inspection,
    InspectionPlan,
    PlanLine,
    QualitySettings,
    Reading,
    ReleaseStatus,
    Result,
)
from .release import check_released, plan_for, release_status

TODAY = datetime.date(2026, 6, 1)


class QualityTestCase(TestCase):
    def setUp(self):
        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        self.gsm = UnitOfMeasure.objects.create(
            code="gsm", name="Grammes per square metre",
            category=UnitOfMeasureCategory.OTHER,
        )
        self.fabric = Item.objects.create(
            sku="FAB-60-87", name="Woven fabric", uom=self.kg,
            tracking=TrackingMode.LOT,
        )
        self.roll = Lot.objects.create(item=self.fabric, code="R-001")
        self.gsm_check = Characteristic.objects.create(
            code="GSM", name="Grammes per square metre", uom=self.gsm
        )
        self.inspector = Party.objects.create(code="QC1", name="Meera")
        self.manager = Party.objects.create(code="MGR", name="Plant manager")

    def plan(self, evaluation=Evaluation.MEAN, samples=3, mandatory=True,
             item=None, lower="83.125", upper="91.875"):
        plan = InspectionPlan.objects.create(
            item=item or self.fabric, is_mandatory=mandatory
        )
        PlanLine.objects.create(
            plan=plan, characteristic=self.gsm_check, target=Decimal("87.5"),
            lower_limit=Decimal(lower), upper_limit=Decimal(upper),
            sample_size=samples, evaluation=evaluation, line_number=1,
        )
        return plan

    def inspect(self, plan, values, lot=None, **kwargs):
        inspection = Inspection.objects.create(
            lot=lot or self.roll, plan=plan, inspected_on=TODAY,
            inspected_by=self.inspector, **kwargs
        )
        line = plan.lines.get()
        for index, value in enumerate(values, start=1):
            Reading.objects.create(
                inspection=inspection, plan_line=line,
                value=Decimal(str(value)), sample_reference=f"S{index}",
            )
        return inspection


class ANumberWithNoUnitTests(QualityTestCase):
    def test_a_measured_characteristic_must_say_in_what(self):
        with self.assertRaises(ValidationError) as caught:
            Characteristic.objects.create(code="TENSILE", name="Tensile strength")
        self.assertIn("does not say in what", str(caught.exception))

    def test_and_one_that_is_simply_present_must_not(self):
        with self.assertRaises(ValidationError):
            Characteristic.objects.create(
                code="PRINT", name="Print registration",
                kind=CharacteristicKind.ATTRIBUTE, uom=self.gsm,
            )

    def test_an_attribute_needs_no_unit(self):
        characteristic = Characteristic.objects.create(
            code="PRINT", name="Print registration",
            kind=CharacteristicKind.ATTRIBUTE,
        )
        self.assertFalse(characteristic.is_measured())


class ALineThatCouldNeverFailTests(QualityTestCase):
    def test_no_floor_and_no_ceiling(self):
        plan = InspectionPlan.objects.create(item=self.fabric)
        with self.assertRaises(ValidationError) as caught:
            PlanLine.objects.create(
                plan=plan, characteristic=self.gsm_check, target=Decimal("87.5")
            )
        self.assertIn("no reading could ever fail", str(caught.exception))

    def test_a_floor_above_the_ceiling(self):
        plan = InspectionPlan.objects.create(item=self.fabric)
        with self.assertRaises(ValidationError) as caught:
            PlanLine.objects.create(
                plan=plan, characteristic=self.gsm_check,
                lower_limit=Decimal("95"), upper_limit=Decimal("85"),
            )
        self.assertIn("nothing can be inside it", str(caught.exception))

    def test_one_sided_is_fine(self):
        # A tensile strength has a floor and no ceiling.
        plan = InspectionPlan.objects.create(item=self.fabric)
        line = PlanLine.objects.create(
            plan=plan, characteristic=self.gsm_check, lower_limit=Decimal("83")
        )
        self.assertTrue(line.passes(Decimal("200")))
        self.assertFalse(line.passes(Decimal("82")))

    def test_a_mandatory_plan_needs_something_to_gate(self):
        loose = Item.objects.create(sku="LOOSE", name="Untracked", uom=self.kg)
        with self.assertRaises(ValidationError) as caught:
            InspectionPlan.objects.create(item=loose, is_mandatory=True)
        self.assertIn("nothing for an inspection to be about", str(caught.exception))

    def test_but_an_advisory_one_does_not(self):
        loose = Item.objects.create(sku="LOOSE", name="Untracked", uom=self.kg)
        InspectionPlan.objects.create(item=loose, is_mandatory=False)


class EveryReadingOrTheirMeanTests(QualityTestCase):
    """
    80, 95 and 87.5 average to exactly 87.5 and contain two readings
    nowhere near it. Both rules are used in practice and here they give
    opposite answers, which is why the plan says which rather than the
    reader guessing.
    """

    VALUES = [80, 95, "87.5"]

    def test_the_mean_passes_it(self):
        inspection = self.inspect(self.plan(Evaluation.MEAN), self.VALUES)
        inspection.post()
        self.assertEqual(inspection.result, Result.PASS)

    def test_every_reading_does_not(self):
        inspection = self.inspect(self.plan(Evaluation.EVERY), self.VALUES)
        inspection.post()
        self.assertEqual(inspection.result, Result.FAIL)

    def test_a_roll_that_is_simply_heavy_fails_either_way(self):
        for rule in (Evaluation.MEAN, Evaluation.EVERY):
            plan = self.plan(rule, item=Item.objects.create(
                sku=f"F-{rule}", name="Fabric", uom=self.kg,
                tracking=TrackingMode.LOT,
            ))
            lot = Lot.objects.create(item=plan.item, code=f"R-{rule}")
            inspection = self.inspect(plan, [92, 92, 92], lot=lot)
            inspection.post()
            self.assertEqual(inspection.result, Result.FAIL, rule)

    def test_a_sample_smaller_than_the_plans_is_not_the_plans_sample(self):
        inspection = self.inspect(self.plan(samples=3), [87, 88])
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("asks for 3 reading(s)", str(caught.exception))

    def test_an_inspection_with_no_readings_at_all(self):
        plan = self.plan()
        inspection = Inspection.objects.create(
            lot=self.roll, plan=plan, inspected_on=TODAY
        )
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("walked past the pallet", str(caught.exception))

    def test_a_plan_for_a_different_item(self):
        other = Item.objects.create(
            sku="TAPE", name="Tape", uom=self.kg, tracking=TrackingMode.LOT
        )
        plan = self.plan(item=other)
        inspection = self.inspect(plan, [87, 88, 89])
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("is a batch of", str(caught.exception))


class WhatItWasJudgedAgainstIsFrozenTests(QualityTestCase):
    def test_tightening_a_tolerance_does_not_retrospectively_fail_a_batch(self):
        plan = self.plan()
        inspection = self.inspect(plan, [90, 90, 90])
        inspection.post()
        self.assertEqual(inspection.result, Result.PASS)
        reading = inspection.readings.first()
        self.assertEqual(reading.upper_limit, Decimal("91.875000"))
        self.assertTrue(reading.passed)

        line = plan.lines.get()
        line.upper_limit = Decimal("88")
        line.save()

        inspection.refresh_from_db()
        reading.refresh_from_db()
        self.assertEqual(inspection.result, Result.PASS)
        self.assertEqual(reading.upper_limit, Decimal("91.875000"))

    def test_a_posted_inspection_cannot_be_edited(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        inspection.notes = "second thoughts"
        with self.assertRaises(ValidationError):
            inspection.save()

    def test_nor_can_a_reading_on_one(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        reading = inspection.readings.first()
        reading.value = Decimal("99")
        with self.assertRaises(ValidationError):
            reading.save()

    def test_nor_removed(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        with self.assertRaises(ValidationError):
            inspection.readings.first().delete()


class AFailureIsNotADispositionTests(QualityTestCase):
    def test_a_failed_batch_cannot_simply_be_accepted(self):
        inspection = self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.ACCEPT
        )
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("cannot simply be accepted", str(caught.exception))

    def test_a_concession_needs_a_name_and_a_reason(self):
        inspection = self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.CONCESSION
        )
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("nobody in particular", str(caught.exception))

    def test_and_then_it_may_be_taken(self):
        inspection = self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.CONCESSION,
            decided_by=self.manager,
            decision_note="Customer accepts at a 3% discount, order 44812.",
        )
        inspection.post()
        self.assertEqual(inspection.result, Result.FAIL)
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)

    def test_there_is_nothing_to_concede_on_a_pass(self):
        inspection = self.inspect(
            self.plan(), [87, 88, 89], disposition=Disposition.CONCESSION,
            decided_by=self.manager, decision_note="Why?",
        )
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("nothing to concede", str(caught.exception))

    def test_a_failure_with_nothing_said_is_a_rejection(self):
        inspection = self.inspect(self.plan(), [92, 92, 92])
        inspection.post()
        self.assertEqual(inspection.disposition, Disposition.REJECT)


class NotYetInspectedIsNotPassedTests(QualityTestCase):
    def test_a_batch_nobody_has_looked_at(self):
        self.plan()
        self.assertEqual(release_status(self.roll), ReleaseStatus.UNINSPECTED)
        with self.assertRaises(ValidationError) as caught:
            check_released(self.fabric, self.roll)
        self.assertIn("Not yet inspected is not passed", str(caught.exception))

    def test_a_passed_batch_goes(self):
        plan = self.plan()
        self.inspect(plan, [87, 88, 89]).post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)
        check_released(self.fabric, self.roll)

    def test_a_rejected_one_does_not(self):
        plan = self.plan()
        self.inspect(plan, [92, 92, 92]).post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.HELD)
        with self.assertRaises(ValidationError) as caught:
            check_released(self.fabric, self.roll)
        self.assertIn("is held", str(caught.exception))

    def test_nor_does_one_waiting_to_be_reworked(self):
        plan = self.plan()
        self.inspect(
            plan, [92, 92, 92], disposition=Disposition.REWORK
        ).post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.HELD)

    def test_an_item_with_no_plan_passes_straight_through(self):
        # Most of what a plant moves is not inspected and this must not
        # stand in its way.
        check_released(self.fabric, self.roll)

    def test_and_so_does_one_with_an_advisory_plan(self):
        self.plan(mandatory=False)
        check_released(self.fabric, self.roll)

    def test_a_mandatory_plan_and_a_line_naming_no_batch(self):
        self.plan()
        with self.assertRaises(ValidationError) as caught:
            check_released(self.fabric, None)
        self.assertIn("does not name one", str(caught.exception))


class VoidingHandsTheQuestionBackTests(QualityTestCase):
    def test_voiding_the_only_inspection_leaves_it_uninspected(self):
        plan = self.plan()
        inspection = self.inspect(plan, [87, 88, 89])
        inspection.post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)
        inspection.void("Balance was out of calibration.")
        self.assertEqual(release_status(self.roll), ReleaseStatus.UNINSPECTED)

    def test_voiding_needs_a_reason(self):
        plan = self.plan()
        inspection = self.inspect(plan, [87, 88, 89])
        inspection.post()
        with self.assertRaises(ValidationError) as caught:
            inspection.void()
        self.assertIn("nobody said why", str(caught.exception))

    def test_the_latest_standing_inspection_speaks(self):
        plan = self.plan()
        first = self.inspect(plan, [92, 92, 92])
        first.post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.HELD)
        second = self.inspect(plan, [87, 88, 89])
        second.inspected_on = TODAY + datetime.timedelta(days=1)
        second.save()
        second.post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)

    def test_and_voiding_it_hands_back_to_the_one_before(self):
        plan = self.plan()
        first = self.inspect(plan, [92, 92, 92])
        first.post()
        second = self.inspect(plan, [87, 88, 89])
        second.inspected_on = TODAY + datetime.timedelta(days=1)
        second.save()
        second.post()
        second.void("Wrong roll.")
        self.assertEqual(release_status(self.roll), ReleaseStatus.HELD)


class LimitsThePlantHasWithdrawnTests(QualityTestCase):
    """
    Found by probing. An inspection posted happily against a retired
    plan and released the batch against a promise nobody was making any
    more.
    """

    def test_a_retired_plan_cannot_be_measured_against(self):
        plan = self.plan()
        plan.is_active = False
        plan.save()
        inspection = self.inspect(plan, [87, 88, 89])
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("has been retired", str(caught.exception))
        self.assertEqual(release_status(self.roll), ReleaseStatus.UNINSPECTED)

    def test_but_retiring_it_afterwards_does_not_unpick_history(self):
        plan = self.plan()
        inspection = self.inspect(plan, [87, 88, 89])
        inspection.post()
        plan.is_active = False
        plan.save()
        inspection.refresh_from_db()
        self.assertEqual(inspection.result, Result.PASS)
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)

    def test_a_retired_characteristic_cannot_be_put_on_a_new_line(self):
        self.gsm_check.is_active = False
        self.gsm_check.save()
        plan = InspectionPlan.objects.create(item=self.fabric)
        with self.assertRaises(ValidationError) as caught:
            PlanLine.objects.create(
                plan=plan, characteristic=self.gsm_check,
                lower_limit=Decimal("83"), upper_limit=Decimal("92"),
            )
        self.assertIn("has been retired", str(caught.exception))


class NothingIsMeasuredOnADayThatHasNotHappenedTests(QualityTestCase):
    def test_an_inspection_dated_next_year(self):
        plan = self.plan()
        inspection = self.inspect(plan, [87, 88, 89])
        inspection.inspected_on = datetime.date(2030, 1, 1)
        inspection.save()
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("has not happened", str(caught.exception))

    def test_today_is_fine(self):
        from django.utils import timezone

        plan = self.plan()
        inspection = self.inspect(plan, [87, 88, 89])
        inspection.inspected_on = timezone.localdate()
        inspection.save()
        inspection.post()
        self.assertEqual(inspection.result, Result.PASS)


class WhoSignedItOffTests(QualityTestCase):
    """
    A concession exists so there is something to answer with. "Meera
    measured it and Meera accepted it" is a weaker answer than a second
    name, and it is still true — so it is recorded rather than refused,
    and a plant that wants the segregation turns it on.
    """

    def concede(self, by):
        return self.inspect(
            self.plan(), [92, 92, 92], disposition=Disposition.CONCESSION,
            decided_by=by, decision_note="Customer takes it at a discount.",
        )

    def test_self_approval_is_recorded_rather_than_refused(self):
        inspection = self.concede(self.inspector)
        inspection.post()
        self.assertTrue(inspection.self_approved())
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)

    def test_a_second_person_is_not_self_approval(self):
        inspection = self.concede(self.manager)
        inspection.post()
        self.assertFalse(inspection.self_approved())

    def test_a_plant_that_asks_for_a_second_person_gets_one(self):
        settings = QualitySettings.get()
        settings.concessions_need_a_second_person = True
        settings.save()
        inspection = self.concede(self.inspector)
        with self.assertRaises(ValidationError) as caught:
            inspection.post()
        self.assertIn("asks for a second person", str(caught.exception))

    def test_and_the_second_person_still_goes_through(self):
        settings = QualitySettings.get()
        settings.concessions_need_a_second_person = True
        settings.save()
        inspection = self.concede(self.manager)
        inspection.post()
        self.assertEqual(release_status(self.roll), ReleaseStatus.RELEASED)

    def test_a_plain_pass_is_never_self_approval(self):
        inspection = self.inspect(self.plan(), [87, 88, 89])
        inspection.post()
        self.assertFalse(inspection.self_approved())
