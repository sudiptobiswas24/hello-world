"""
The 60 x 100 sack, tested for more than its weight: the seam holds 400 N
on the mean of five, every one of three filled sacks survives 5 drops,
and the fabric keeps 70% of its strength after 200 hours of UV. Its
fabric is pulled for 600 N warp and 550 N weft a 5 cm strip, and its
mesh counted at 10 x 10 give or take 0.5 an inch.

At the BCS the bundle is weighed as before and booked; its inspection
stays open for the lab, and the batch is not yet inspected until the lab
finishes it. Withdrawn before then, it comes off the lab's list.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.quality.models import Evaluation, Inspection

from .conversion import BagCount, void_bags
from .tests_bag_counts import ConversionTestCase
from .woven import BagSpecification, FabricSpecification


class LabLinesTestCase(ConversionTestCase):
    def tested(self, **values):
        spec = BagSpecification.objects.get(pk=self.bag_spec.pk)
        for name, value in values.items():
            setattr(spec, name, value)
        spec.save()
        return spec

    def lines(self, spec):
        return [(line.characteristic.code, line.target, line.lower_limit, line.upper_limit,
                 line.sample_size, line.evaluation)
                for line in spec.inspection_plan.lines.order_by("line_number")]


class WhatTheLabTestsTests(LabLinesTestCase):
    def test_each_test_the_customer_asks_for_is_a_line(self):
        spec = self.tested(seam_strength_min_n=Decimal("400"), drop_test_drops=5,
                           uv_retention_min_percent=Decimal("70"), uv_exposure_hours=200)
        self.assertEqual(self.lines(spec)[1:], [
            ("SEAM", None, Decimal("400.000000"), None, 5, Evaluation.MEAN),
            ("DROP", None, Decimal("5.000000"), None, 3, Evaluation.EVERY),
            ("UVRET", None, Decimal("70.000000"), None, 3, Evaluation.MEAN),
        ])
        self.assertEqual(spec.inspection_plan.lines.get(characteristic__code="UVRET")
                         .characteristic.name, "Strength kept after 200 h UV")

    def test_and_none_it_does_not(self):
        self.assertEqual([row[0] for row in self.lines(self.bag_spec)], ["BAGWT"])

    def test_a_typed_liner_is_gauged_on_the_sack(self):
        from apps.inventory.models import Item

        liner = Item.objects.create(sku="LDPE-FILM", name="Liner film", uom=self.kg,
                                    standard_cost=Decimal("150"))
        spec = self.tested(liner_item=liner, liner_micron=Decimal("40"),
                           liner_width_cm=Decimal("58"), liner_length_cm=Decimal("105"),
                           target_grams=None)
        self.assertEqual(self.lines(spec)[-1],
                         ("LINERMIC", Decimal("40.000000"), Decimal("36.000000"),
                          Decimal("44.000000"), 5, Evaluation.MEAN))

    def test_the_fabric_pulled_and_counted(self):
        fabric = FabricSpecification.objects.get(pk=self.spec.pk)
        fabric.warp_strength_min_n = Decimal("600")
        fabric.weft_strength_min_n = Decimal("550")
        fabric.mesh_tolerance_per_inch = Decimal("0.5")
        fabric.save()
        rows = [(line.characteristic.code, line.target, line.lower_limit, line.upper_limit)
                for line in fabric.inspection_plan.lines.order_by("line_number")][1:]
        ends, picks = fabric.ends_per_inch, fabric.picks_per_inch
        self.assertEqual(rows, [
            ("TENWARP", None, Decimal("600.000000"), None),
            ("TENWEFT", None, Decimal("550.000000"), None),
            ("EPI", ends, ends - Decimal("0.5"), ends + Decimal("0.5")),
            ("PPI", picks, picks - Decimal("0.5"), picks + Decimal("0.5")),
        ])

    def test_what_a_test_cannot_be(self):
        with self.assertRaisesMessage(ValidationError, "no lamination bond"):
            self.tested(bond_strength_min_n=Decimal("3"))
        with self.assertRaisesMessage(ValidationError, "both the strength kept and the hours"):
            self.tested(uv_retention_min_percent=Decimal("70"))
        with self.assertRaisesMessage(ValidationError, "seam strength limit is more"):
            self.tested(seam_strength_min_n=Decimal("0"))
        fabric = FabricSpecification.objects.get(pk=self.spec.pk)
        fabric.mesh_tolerance_per_inch = Decimal("0")
        with self.assertRaisesMessage(ValidationError, "mesh tolerance is more"):
            fabric.save()


class TheLabFinishesTests(LabLinesTestCase):
    def test_weighed_and_booked_then_left_open_for_the_lab(self):
        self.tested(seam_strength_min_n=Decimal("400"))
        count = self.count()
        self.assertEqual((count.passed, count.inspection.posted, count.inspection.disposition),
                         (True, False, ""))
        self.assertEqual(count.inspection.lot.on_hand_at(self.plant), Decimal("500"))
        self.assertEqual(count.inspection.readings.count(), 10)

    def test_off_weight_the_supervisor_takes_the_weight_and_the_lab_decides(self):
        from .tests_bag_counts import OUT

        self.tested(seam_strength_min_n=Decimal("400"))
        count = self.count(sample=OUT, supervisor=self.supervisor, reason="Customer agreed")
        self.assertEqual((count.passed, count.inspection.disposition,
                          count.inspection.decided_by, count.inspection.posted),
                         (False, "", self.supervisor.party, False))

    def test_withdrawn_before_the_lab_it_comes_off_the_list(self):
        self.tested(seam_strength_min_n=Decimal("400"))
        count = self.count()
        inspection = count.inspection
        void_bags(count, self.supervisor, "Miscounted")
        count = BagCount.objects.get(pk=count.pk)
        self.assertIsNone(count.inspection)
        self.assertFalse(Inspection.objects.filter(pk=inspection.pk).exists())
        self.assertEqual(count.entry.lot.on_hand_at(self.plant), Decimal("0"))

    def test_the_limits_do_not_move_under_an_inspected_batch(self):
        self.count()
        with self.assertRaisesMessage(ValidationError, "Close this specification's window"):
            self.tested(seam_strength_min_n=Decimal("400"))
