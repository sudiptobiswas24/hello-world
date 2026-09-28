"""
Six tape batches at 4, 6, 8, 10, 12 and 14% filler, measured at 5.25,
4.95, 4.82, 4.58, 4.43 and 4.17 g/den, and stretching 18.0, 19.2, 20.4,
21.1, 22.6 and 23.3% before they broke.

  Tenacity = 5.6257 - 0.1029 x filler, R² 0.9906, scatter 0.0419.
  At 9% the line says 4.7000 g/den, one-sided 95% lower bound 4.6035:
  a 4.50 minimum is met, 4.65 is doubtful, 4.80 fails.
  At 20% it says 3.5686, and 20% is outside the 4-14% measured.
  Elongation at 9%: 20.7667, between 20.3192 and 21.2142.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.inventory.models import Item, Lot, TrackingMode
from apps.quality.models import Inspection, Reading

from .bom import BillOfMaterials, BomComponent
from .orders import WorkOrder
from .strength import check, fit
from .tests_orders import TODAY, RunTestCase
from .woven import TapeSpecification

BATCHES = [(4, "5.25", "18.0"), (6, "4.95", "19.2"), (8, "4.82", "20.4"),
           (10, "4.58", "21.1"), (12, "4.43", "22.6"), (14, "4.17", "23.3")]


class StrengthTestCase(RunTestCase):
    def spec(self, filler, **extra):
        item = Item.objects.create(sku=f"TAPE-F{filler}", name=f"Tape at {filler}% filler",
                                   uom=self.kg, tracking=TrackingMode.LOT)
        values = dict(code=f"T{filler}", tape_item=item, denier=Decimal("1000"),
                      tape_width_mm=Decimal("2.5"), virgin_granule=self.virgin,
                      filler_item=self.filler, filler_percent=Decimal(filler),
                      extrusion_waste_percent=Decimal("3"), waste_recovered_percent=Decimal("0"),
                      min_tenacity_gpd=Decimal("4.5"), elongation_min_percent=Decimal("18"),
                      elongation_max_percent=Decimal("25"))
        values.update(extra)
        return TapeSpecification.objects.create(**values)

    def made(self, spec, code, bom=None):
        run = WorkOrder.objects.create(item=spec.tape_item, bom=bom or spec.bom,
                                       quantity_ordered=Decimal("100"), uom=self.kg,
                                       warehouse=self.plant, work_centre=self.loom)
        run.release(TODAY)
        lot = Lot.objects.filter(item=spec.tape_item, code=code).first() or \
            Lot.objects.create(item=spec.tape_item, code=code)
        self.produce(run, "50", lot=lot).post()
        return lot

    def measured(self, spec, lot, tenacity, elongation):
        inspection = Inspection.objects.create(lot=lot, plan=spec.inspection_plan,
                                               inspected_on=TODAY)
        for line in spec.inspection_plan.lines.select_related("characteristic"):
            value = {"denier": "1000", "tenacity": tenacity,
                     "elongation": elongation}[line.derived_from]
            for number in range(line.sample_size):
                Reading.objects.create(inspection=inspection, plan_line=line,
                                       value=Decimal(value), sample_reference=f"S{number}")
        inspection.post()
        return inspection

    def history(self, rows=BATCHES):
        self.specs = {}
        for filler, tenacity, elongation in rows:
            spec = self.spec(filler)
            self.specs[filler] = spec
            self.measured(spec, self.made(spec, f"TB-{filler}"), tenacity, elongation)

    def row(self, measure, spec=None, filler=None):
        return next(row for row in check(spec or self.specs[8], filler)
                    if row["measure"] == measure)


class TheLineTests(StrengthTestCase):
    def test_learnt_from_the_batches(self):
        self.history()
        row = self.row("tenacity", filler="9")
        self.assertEqual((row["batches"], row["per_point_of_filler"], row["r_squared"]),
                         (6, Decimal("-0.1029"), Decimal("0.9906")))
        self.assertEqual((row["prediction"]["predicted"], row["prediction"]["lower"]),
                         (Decimal("4.7000"), Decimal("4.6035")))
        self.assertEqual((row["verdict"], row["prediction"]["extrapolated"]), ("meets", False))

    def test_against_the_customers_minimum(self):
        self.history()
        spec = self.specs[8]
        for minimum, verdict in (("4.65", "doubtful"), ("4.80", "fails")):
            TapeSpecification.objects.filter(pk=spec.pk).update(min_tenacity_gpd=Decimal(minimum))
            spec.refresh_from_db()
            self.assertEqual(self.row("tenacity", spec, "9")["verdict"], verdict)

    def test_outside_what_was_measured_says_so(self):
        self.history()
        prediction = self.row("tenacity", filler="20")["prediction"]
        self.assertEqual((prediction["predicted"], prediction["extrapolated"]),
                         (Decimal("3.5686"), True))

    def test_elongation_between_its_limits(self):
        self.history()
        row = self.row("elongation", filler="9")
        self.assertEqual((row["prediction"]["predicted"], row["prediction"]["lower"],
                          row["prediction"]["upper"], row["verdict"]),
                         (Decimal("20.7667"), Decimal("20.3192"), Decimal("21.2142"), "meets"))
        spec = self.specs[8]
        TapeSpecification.objects.filter(pk=spec.pk).update(elongation_max_percent=Decimal("21"))
        spec.refresh_from_db()
        self.assertEqual(self.row("elongation", spec, "9")["verdict"], "doubtful")
        TapeSpecification.objects.filter(pk=spec.pk).update(elongation_max_percent=Decimal("20"))
        spec.refresh_from_db()
        self.assertEqual(self.row("elongation", spec, "9")["verdict"], "fails")
        TapeSpecification.objects.filter(pk=spec.pk).update(elongation_min_percent=Decimal("21"),
                                                            elongation_max_percent=None)
        spec.refresh_from_db()
        self.assertEqual(self.row("elongation", spec, "9")["verdict"], "fails")


class WhatItWillNotPretendTests(StrengthTestCase):
    def test_too_few_batches(self):
        self.history(BATCHES[:4])
        row = self.row("tenacity")
        self.assertIsNone(row["prediction"])
        self.assertIn("a line needs at least 5", row["why"])

    def test_one_filler_shows_no_slope(self):
        spec = self.spec(8)
        for number, value in enumerate(("4.8", "4.9", "4.7", "4.85", "4.75")):
            self.measured(spec, self.made(spec, f"TB-{number}"), value, "20")
        with self.assertRaisesMessage(ValidationError, "cannot be seen from one level"):
            fit("tenacity")

    def test_only_the_standing_measurement_counts(self):
        self.history()
        Inspection.objects.get(lot__code="TB-14").void("Wrong sample")
        self.assertEqual(self.row("tenacity")["batches"], 5)

    def test_a_batch_measured_twice_counts_once_at_its_latest(self):
        self.history()
        self.measured(self.specs[14], Lot.objects.get(code="TB-14"), "4.30", "23.3")
        row = self.row("tenacity")
        # Refitted by hand with 4.30 in place of 4.17: slope -0.0936.
        self.assertEqual((row["batches"], row["per_point_of_filler"]), (6, Decimal("-0.0936")))

    def test_a_batch_of_no_one_filler_is_left_out_and_named(self):
        self.history()
        spec = self.specs[8]
        hand = BillOfMaterials.objects.create(item=spec.tape_item, name="By hand",
                                              quantity_produced=Decimal("100"), uom=self.kg,
                                              is_default=False, version=2)
        BomComponent.objects.create(bom=hand, item=self.virgin, quantity=Decimal("100"),
                                    uom=self.kg, line_number=1)
        self.made(spec, "TB-8", bom=hand)
        self.measured(spec, self.made(spec, "TB-HAND", bom=hand), "4.00", "20")
        row = self.row("tenacity")
        self.assertEqual((row["batches"], row["left_out"]), (5, ["TB-8", "TB-HAND"]))


class TheSpecificationTests(StrengthTestCase):
    def test_what_is_demanded_is_measured_on_every_batch(self):
        spec = self.spec(8)
        lines = {line.derived_from: line for line in spec.inspection_plan.lines.all()}
        self.assertEqual((lines["tenacity"].lower_limit, lines["tenacity"].upper_limit),
                         (Decimal("4.500000"), None))
        self.assertEqual((lines["elongation"].lower_limit, lines["elongation"].upper_limit,
                          lines["elongation"].target),
                         (Decimal("18.000000"), Decimal("25.000000"), Decimal("21.500000")))
        plain = self.spec(10, min_tenacity_gpd=None, elongation_min_percent=None,
                          elongation_max_percent=None)
        self.assertEqual([line.derived_from for line in plain.inspection_plan.lines.all()],
                         ["denier"])

    def test_limits_that_leave_nothing_are_refused(self):
        with self.assertRaisesMessage(ValidationError, "leaves nothing between"):
            self.spec(8, elongation_min_percent=Decimal("25"), elongation_max_percent=Decimal("18"))
        with self.assertRaisesMessage(ValidationError, "more than nothing"):
            self.spec(9, min_tenacity_gpd=Decimal("0"))


class StrengthApiTests(StrengthTestCase):
    def test_asked_of_a_specification_at_another_filler(self):
        self.history()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("lab"))
        url = f"/api/manufacturing/tape-specifications/{self.specs[8].pk}/strength/"
        rows = {row["measure"]: row for row in client.get(url, {"filler": "9"}).json()}
        self.assertEqual((rows["tenacity"]["prediction"]["predicted"],
                          rows["tenacity"]["verdict"]), ("4.7000", "meets"))
        self.assertEqual(client.get(url, {"filler": "lots"}).status_code, 400)
        self.assertEqual(client.get(url, {"filler": "NaN"}).status_code, 400)
        asked = client.get(url, {"filler": "1E+1"}).json()[0]["prediction"]["filler_percent"]
        self.assertEqual(asked, "10")


class AfterTheLabHasMeasuredTests(StrengthTestCase):
    """
    A specification whose batches have been inspected. Its plan's lines
    judged them, so they cannot be rewritten under them; anything else
    about the specification can still be saved.
    """

    def setUp(self):
        super().setUp()
        self.spec8 = self.spec(8)
        self.measured(self.spec8, self.made(self.spec8, "TB-8"), "4.82", "20.4")

    def test_a_save_that_leaves_the_limits_alone_goes_through(self):
        self.spec8.extrusion_waste_percent = Decimal("4")
        self.spec8.save()
        self.spec8.valid_to = TODAY
        self.spec8.save()
        self.assertEqual(TapeSpecification.objects.get(pk=self.spec8.pk).valid_to, TODAY)

    def test_new_limits_are_a_new_specification(self):
        self.spec8.min_tenacity_gpd = Decimal("4.6")
        with self.assertRaisesMessage(ValidationError, "stage a new one from the day"):
            self.spec8.save()
        line = self.spec8.inspection_plan.lines.get(derived_from="tenacity")
        self.assertEqual(line.lower_limit, Decimal("4.500000"))

    def test_the_way_out_it_names_works(self):
        self.spec8.valid_to = TODAY
        self.spec8.save()
        values = {field: getattr(self.spec8, field) for field in (
            "tape_item", "denier", "tape_width_mm", "virgin_granule", "filler_item",
            "filler_percent", "extrusion_waste_percent", "waste_recovered_percent",
            "elongation_min_percent", "elongation_max_percent")}
        successor = TapeSpecification.objects.create(
            code="T8-2", valid_from=TODAY + timedelta(days=1),
            min_tenacity_gpd=Decimal("4.6"), **values)
        self.assertEqual(successor.inspection_plan.lines.get(derived_from="tenacity").lower_limit,
                         Decimal("4.600000"))
        self.assertEqual(self.spec8.inspection_plan.lines.get(derived_from="tenacity").lower_limit,
                         Decimal("4.500000"))

    def test_the_api_says_why_rather_than_falling_over(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("lab"))
        response = client.patch(f"/api/manufacturing/tape-specifications/{self.spec8.pk}/",
                                {"min_tenacity_gpd": "4.6"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("stage a new one", response.content.decode())
