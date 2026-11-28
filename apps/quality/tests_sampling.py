"""
Acceptance sampling: the published examples first, then a lot of 1,000
printed sacks checked for a clean print at AQL 2.5, level II: code J,
80 sacks, accept at 5 bad, reject at 6.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import Item, Lot, MovementType, StockMovement, TrackingMode, Warehouse

from .models import (
    Characteristic,
    CharacteristicKind,
    Disposition,
    Evaluation,
    Inspection,
    InspectionPlan,
    PlanLine,
    Reading,
    Result,
)
from .sampling import sampling_plan
from .tests import TODAY, QualityTestCase


class TheTableTests(QualityTestCase):
    def test_the_published_examples(self):
        cases = [
            ((1000, "II", "2.5"), ("J", 80, 5, 6, False)),
            ((1000, "II", "4.0"), ("J", 80, 7, 8, False)),
            ((1000, "II", "1.5"), ("J", 80, 3, 4, False)),
            ((5000, "II", "2.5"), ("L", 200, 10, 11, False)),
            ((150, "II", "2.5"), ("F", 20, 1, 2, False)),
            ((400, "II", "2.5"), ("H", 50, 3, 4, False)),
            # An arrow up: F has no plan at 1.0, E's 13 at 0/1 is used.
            ((100, "II", "1.0"), ("E", 13, 0, 1, False)),
            # An arrow down: F has none at 1.5 either way, G's 32 at 1/2.
            ((100, "II", "1.5"), ("G", 32, 1, 2, False)),
            # The plan reached asks for more than the lot: all of it.
            ((10, "II", "1.0"), ("E", 10, 0, 1, True)),
            # Down to F's 20 on a lot of exactly 20: that is the whole lot.
            ((20, "II", "0.65"), ("F", 20, 0, 1, True)),
            ((600000, "III", "0.65"), ("R", 2000, 21, 22, False)),
            ((600000, "III", "1.0"), ("Q", 1250, 21, 22, False)),
            ((1000, "S-1", "2.5"), ("C", 5, 0, 1, False)),
            ((500, "I", "6.5"), ("F", 20, 3, 4, False)),
        ]
        for asked, wanted in cases:
            plan = sampling_plan(*asked)
            self.assertEqual((plan["letter"], plan["sample"], plan["accept"], plan["reject"],
                              plan["whole_lot"]), wanted, asked)

    def test_what_the_table_does_not_answer(self):
        with self.assertRaisesMessage(ValidationError, "AQL 3.0 is not one of"):
            sampling_plan(1000, "II", "3.0")
        with self.assertRaisesMessage(ValidationError, "AQL x is not a number"):
            sampling_plan(1000, "II", "x")
        with self.assertRaisesMessage(ValidationError, "level IV is not one of"):
            sampling_plan(1000, "IV", "2.5")
        with self.assertRaisesMessage(ValidationError, "at least two units"):
            sampling_plan(1, "II", "2.5")


class SampledLotTestCase(QualityTestCase):
    def setUp(self):
        super().setUp()
        self.pcs = UnitOfMeasure.objects.create(code="pcs", name="Pieces",
                                                category=UnitOfMeasureCategory.COUNT)
        self.sack = Item.objects.create(sku="SACK", name="Printed sack", uom=self.pcs,
                                        tracking=TrackingMode.LOT)
        self.bundle = Lot.objects.create(item=self.sack, code="B-1")
        StockMovement.objects.create(item=self.sack, lot=self.bundle,
                                     warehouse=Warehouse.objects.create(code="W", name="W"),
                                     movement_type=MovementType.RECEIPT, uom=self.pcs,
                                     quantity=Decimal("1000"), unit_cost=Decimal("10"),
                                     occurred_at=timezone.now())
        self.print_ok = Characteristic.objects.create(
            code="PRINT", name="Print clean and in register",
            kind=CharacteristicKind.ATTRIBUTE)
        self.sack_plan = InspectionPlan.objects.create(item=self.sack, is_mandatory=True)
        self.line = PlanLine.objects.create(plan=self.sack_plan, characteristic=self.print_ok,
                                            aql=Decimal("2.5"), inspection_level="II",
                                            line_number=1)

    def pulled(self, samples, bad, lot=None, plan=None, line=None, **extra):
        inspection = Inspection.objects.create(lot=lot or self.bundle,
                                               plan=plan or self.sack_plan,
                                               inspected_on=TODAY, inspected_by=self.inspector,
                                               **extra)
        for number in range(samples):
            Reading.objects.create(inspection=inspection, plan_line=line or self.line,
                                   present=number >= bad, sample_reference=f"S{number}")
        return inspection


class JudgedBySamplingTests(SampledLotTestCase):
    def test_five_bad_of_eighty_passes_and_six_does_not(self):
        passed = self.pulled(80, 5).post()
        passed.refresh_from_db()
        self.assertEqual((passed.result, passed.lot_size), (Result.PASS, 1000))
        self.assertEqual(passed.sampling[str(self.line.pk)],
                         {"letter": "J", "sample": 80, "accept": 5, "reject": 6,
                          "whole_lot": False, "level": "II", "aql": "2.5"})
        failed = self.pulled(80, 6).post()
        self.assertEqual((failed.result, failed.disposition), (Result.FAIL, Disposition.REJECT))

    def test_the_sample_is_the_plans_not_more_nor_fewer(self):
        with self.assertRaisesMessage(ValidationError, "takes 80 samples (code J); this "
                                                       "inspection has 79"):
            self.pulled(79, 0).post()
        with self.assertRaisesMessage(ValidationError, "this inspection has 81"):
            self.pulled(81, 0).post()

    def test_a_stated_lot_size_is_what_the_sample_was_drawn_from(self):
        inspection = self.pulled(20, 1, lot_size=150).post()
        self.assertEqual((inspection.result, inspection.sampling[str(self.line.pk)]["sample"]),
                         (Result.PASS, 20))

    def test_a_lot_weighed_rather_than_counted_must_say_how_many_units(self):
        fabric_plan = InspectionPlan.objects.create(item=self.fabric, is_mandatory=True)
        line = PlanLine.objects.create(plan=fabric_plan, characteristic=self.gsm_check,
                                       lower_limit=Decimal("83"), upper_limit=Decimal("92"),
                                       aql=Decimal("6.5"), line_number=1)
        StockMovement.objects.create(item=self.fabric, lot=self.roll,
                                     warehouse=Warehouse.objects.get(code="W"),
                                     movement_type=MovementType.RECEIPT, uom=self.kg,
                                     quantity=Decimal("500"), unit_cost=Decimal("1"),
                                     occurred_at=timezone.now())
        # 500 kilogrammes on hand is not 500 units.
        with self.assertRaisesMessage(ValidationError, "say how many units the lot holds"):
            self.pulled(2, 0, lot=self.roll, plan=fabric_plan, line=line).post()
        # Ten rolls: B's 3 has no plan at 6.5; the arrow up gives A's 2 at 0/1.
        inspection = Inspection.objects.create(lot=self.roll, plan=fabric_plan,
                                               inspected_on=TODAY, lot_size=10)
        for number, value in enumerate(("87", "93")):
            Reading.objects.create(inspection=inspection, plan_line=line,
                                   value=Decimal(value), sample_reference=f"S{number}")
        self.assertEqual(inspection.post().result, Result.FAIL)

    def test_a_counted_lot_with_a_part_unit_on_hand_must_say_too(self):
        StockMovement.objects.create(item=self.sack, lot=self.bundle,
                                     warehouse=Warehouse.objects.get(code="W"),
                                     movement_type=MovementType.ISSUE, uom=self.pcs,
                                     quantity=Decimal("-0.5"), unit_cost=Decimal("10"),
                                     occurred_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "say how many units"):
            self.pulled(80, 0).post()


class ThePlanLineTests(SampledLotTestCase):
    def test_what_a_sampled_line_may_say(self):
        other = Characteristic.objects.create(code="SEAM", name="Seam closed",
                                              kind=CharacteristicKind.ATTRIBUTE)
        with self.assertRaisesMessage(ValidationError, "AQL 3 is not one of"):
            PlanLine.objects.create(plan=self.sack_plan, characteristic=other,
                                    aql=Decimal("3"), line_number=2)
        with self.assertRaisesMessage(ValidationError, "level V is not one of"):
            PlanLine.objects.create(plan=self.sack_plan, characteristic=other,
                                    aql=Decimal("2.5"), inspection_level="V", line_number=2)
        with self.assertRaisesMessage(ValidationError, "not judged on their mean"):
            PlanLine.objects.create(plan=self.sack_plan, characteristic=other,
                                    aql=Decimal("2.5"), evaluation=Evaluation.MEAN,
                                    line_number=2)


class SamplingApiTests(SampledLotTestCase):
    def test_asked_before_pulling_a_sack(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("qa"))
        body = client.get("/api/quality/sampling/", {"lot_size": "1000", "aql": "2.5"}).json()
        self.assertEqual((body["letter"], body["sample"], body["accept"], body["reject"]),
                         ("J", 80, 5, 6))
        for bad in ({"lot_size": "many", "aql": "2.5"}, {"lot_size": "1000", "aql": "x"}):
            self.assertEqual(client.get("/api/quality/sampling/", bad).status_code, 400)
