"""
The bottom fold, the thread worked from the seams, colours per face and
the press solvents. Figures worked by hand and checked by an independent
script before the code was written; the fixture sack is 60 x 100 on
87.48911 GSM fabric, hems 3 and 2, fabric 110.23628 g.

  DFDS with no allowance given takes 2 in = 5.08 cm: cut 107.08 cm,
      1.28496 m2, fabric 112.42001 g, with 1.2 g thread 113.62001 g.
  A stitch row is the width in chain stitch: 0.60 m x (12.5 / 12.5)
      x 4.5 x 1000 / 9000 = 0.30 g. SFDS is two rows and a hemmed
      mouth one more: 0.90 g, the sack 111.13628 g. DFSS, raw mouth,
      15 stitches a dm: one row at 0.36 g. At 1,500 denier, 1.35 g.
      Nona Manis (44.958 cm, SFDS, raw mouth): 0.44958 g, as the app.
  Colours: 4 front and 2 back on 0.6 m2 faces at 0.5 g: 1.8 g, the
      sack 113.23628 g; 3 front only is 0.9 g on one face.
  Reducer 20% of 1.8 g ink is 0.36 g; solvent 5% is 0.09 g. Neither
      is in the sack.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.inventory.models import Item

from .tests_woven import WovenTestCase, close
from .woven import BagSpecification


class FinishingTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        weigh = lambda sku: Item.objects.create(sku=sku, name=sku, uom=self.kg)
        self.ink = weigh("INK")
        self.reducer = weigh("REDUCER")
        self.mibk = weigh("MIBK")

    def sewn(self, **overrides):
        values = dict(thread_grams_per_bag=Decimal("0"), thread_denier=Decimal("1000"),
                      fold_type="SFDS")
        values.update(overrides)
        return self.bag(**values)

    def printed(self, **overrides):
        values = dict(print_colours=4, print_colours_back=2, ink_item=self.ink)
        values.update(overrides)
        return self.bag(**values)

    def row(self, bag, item):
        return bag.bom.components.get(item=item).quantity

    def refused(self, message, build, **overrides):
        with self.assertRaises(ValidationError) as caught:
            build(**overrides)
        self.assertIn(message, str(caught.exception))


class ThreadTests(FinishingTestCase):
    def test_two_bottom_rows_and_a_hemmed_mouth(self):
        bag = self.sewn()
        self.assertEqual(bag.stitch_rows(), 3)
        self.assertTrue(close(bag.thread_grams(), "0.90"))
        self.assertTrue(close(bag.bag_grams(), "111.13628"))
        self.assertTrue(close(self.row(bag, self.thread), "0.90"))

    def test_stitch_density_and_a_raw_mouth(self):
        bag = self.sewn(fold_type="DFSS", top_hem_cm=Decimal("0"),
                        stitches_per_dm=Decimal("15"))
        self.assertEqual(bag.stitch_rows(), 1)
        self.assertTrue(close(bag.thread_grams(), "0.36"))

    def test_a_heavier_yarn(self):
        self.assertTrue(close(self.sewn(thread_denier=Decimal("1500")).thread_grams(), "1.35"))

    def test_the_apps_figure_for_nona_manis(self):
        sack = BagSpecification(bag_width_cm=Decimal("44.958"), fold_type="SFDS",
                                top_hem_cm=Decimal("0"), thread_denier=Decimal("1000"))
        self.assertTrue(close(sack.thread_grams(), "0.44958"))

    def test_typed_thread_still_stands(self):
        self.assertTrue(close(self.bag().thread_grams(), "1.2"))

    def test_computed_thread_needs_the_fold(self):
        self.refused("needs the fold type", self.sewn, fold_type="")

    def test_computed_and_typed_are_one_or_the_other(self):
        self.refused("Give one or the other", self.sewn, thread_grams_per_bag=Decimal("1"))

    def test_thread_and_its_item_come_together(self):
        self.refused("the thread needs both", self.sewn, thread_item=None)
        self.refused("the thread needs both", self.bag, thread_grams_per_bag=Decimal("0"))

    def test_a_welded_sack_takes_no_computed_thread_either(self):
        fabric = self.fabric()
        coat = Item.objects.create(sku="COAT", name="Coat", uom=self.kg)
        self.refused("is not sewn", self.bag, fabric=fabric, closure="welded", thread_item=None,
                     thread_grams_per_bag=Decimal("0"), thread_denier=Decimal("1000"),
                     fold_type="SFSS", is_laminated=True, lamination_gsm=Decimal("15"),
                     lamination_item=coat)

    def test_the_database_refuses_an_unknown_fold(self):
        bag = self.bag()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BagSpecification.objects.filter(pk=bag.pk).update(fold_type="XX")


class ColourTests(FinishingTestCase):
    def test_colours_on_each_face(self):
        bag = self.printed()
        self.assertTrue(close(bag.ink_grams(), "1.8"))
        self.assertTrue(close(bag.printed_area_sqm(), "1.2"))
        self.assertTrue(close(bag.bag_grams(), "113.23628"))
        self.assertTrue(close(self.row(bag, self.ink), "1.8"))

    def test_the_front_only(self):
        bag = self.printed(print_colours=3, print_colours_back=0)
        self.assertTrue(close(bag.ink_grams(), "0.9"))
        self.assertTrue(close(bag.printed_area_sqm(), "0.6"))

    def test_back_colours_need_an_ink_too(self):
        self.refused("printed in 2 colour(s)", self.bag, print_colours_back=2)


class SolventTests(FinishingTestCase):
    def test_solvents_are_bought_and_never_weighed(self):
        bag = self.printed(reducer_item=self.reducer, reducer_percent=Decimal("20"),
                           solvent_item=self.mibk, solvent_percent=Decimal("5"))
        self.assertTrue(close(self.row(bag, self.reducer), "0.36"))
        self.assertTrue(close(self.row(bag, self.mibk), "0.09"))
        self.assertTrue(close(bag.bag_grams(), "113.23628"))

    def test_each_solvent_needs_its_item_and_its_share(self):
        self.refused("the reducer need both", self.printed, reducer_item=self.reducer)
        self.refused("the solvent need both", self.printed, solvent_percent=Decimal("5"))

    def test_nothing_to_thin_on_an_unprinted_sack(self):
        self.refused("no ink for a reducer", self.bag, reducer_item=self.reducer,
                     reducer_percent=Decimal("20"))

    def test_solvents_must_be_weighed(self):
        self.mibk.uom = self.pcs
        self.mibk.save()
        self.refused("the solvent MIBK is measured in pcs", self.printed,
                     solvent_item=self.mibk, solvent_percent=Decimal("5"))


class FoldApiTests(FinishingTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("planner"))
        self.body = {"code": "B-API", "bag_item": self.bag_item.pk, "fabric": self.fabric().pk,
                     "bag_width_cm": "60", "bag_length_cm": "100", "top_hem_cm": "2",
                     "thread_item": self.thread.pk, "thread_grams_per_bag": "1.2"}

    def test_a_fold_given_without_an_allowance_takes_its_own(self):
        response = self.client.post("/api/manufacturing/bag-specifications/",
                                    {**self.body, "fold_type": "DFDS"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["bottom_hem_cm"], "5.08")
        self.assertTrue(close(str(response.json()["bag_grams"]), "113.62001", "0.001"))

    def test_an_allowance_given_stands(self):
        response = self.client.post("/api/manufacturing/bag-specifications/",
                                    {**self.body, "fold_type": "DFDS", "bottom_hem_cm": "4"},
                                    format="json")
        self.assertEqual(response.json()["bottom_hem_cm"], "4.00")

    def test_changing_the_fold_changes_the_allowance_unless_one_is_given(self):
        pk = self.client.post("/api/manufacturing/bag-specifications/",
                              {**self.body, "fold_type": "SFSS"}, format="json").json()["id"]
        url = f"/api/manufacturing/bag-specifications/{pk}/"
        self.assertEqual(self.client.patch(url, {"fold_type": "EZWOF"}, format="json")
                         .json()["bottom_hem_cm"], "1.91")
        self.assertEqual(self.client.patch(url, {"fold_type": "DFSS", "bottom_hem_cm": "6"},
                                           format="json").json()["bottom_hem_cm"], "6.00")

    def test_solving_with_a_computed_thread(self):
        response = self.client.post("/api/manufacturing/bag-specifications/solve/", {
            "bag_width_cm": "60", "bag_length_cm": "100", "bottom_hem_cm": "3",
            "fold_type": "SFDS", "thread_denier": "1000", "target_grams": "111.136",
            "ends_per_inch": "10", "picks_per_inch": "10",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["addon_grams"], response.json()["fabric_gsm"]),
                         ("0.900", "87.489"))
        self.assertEqual(response.json()["warnings"], [])

    def test_a_solution_outside_the_usual_range_is_warned(self):
        response = self.client.post("/api/manufacturing/bag-specifications/solve/", {
            "bag_width_cm": "60", "bag_length_cm": "100", "thread_grams_per_bag": "1.2",
            "target_grams": "20", "ends_per_inch": "10", "picks_per_inch": "10",
        }, format="json")
        warnings = response.json()["warnings"]
        self.assertEqual(len(warnings), 3)
        self.assertIn("15 GSM", warnings[0])
        self.assertIn("warp tape at 164 denier", warnings[1])


class PrintedFacesMigrate(TransactionTestCase):
    """Colours on two faces become the same colours on the back; ink is unchanged."""

    before = [("manufacturing", "0031_weight_contract_and_shrink")]
    after = [("manufacturing", "0032_fold_thread_colours_solvents")]

    def test_faces_become_back_colours(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        uom = apps.get_model("core", "UnitOfMeasure").objects.create(
            code="kg", name="kg", category="weight")
        pcs = apps.get_model("core", "UnitOfMeasure").objects.create(
            code="pcs", name="pcs", category="count")
        item = apps.get_model("inventory", "Item")
        tape = apps.get_model("manufacturing", "TapeSpecification").objects.create(
            code="T", tape_item=item.objects.create(sku="T", name="T", uom=uom),
            denier=1000, tape_width_mm=2.5,
            virgin_granule=item.objects.create(sku="PP", name="PP", uom=uom))
        fabric = apps.get_model("manufacturing", "FabricSpecification").objects.create(
            code="F", fabric_item=item.objects.create(sku="F", name="F", uom=uom),
            warp_tape=tape, ends_per_inch=10, picks_per_inch=10,
            lay_flat_width_cm=60, target_gsm=87.5)
        bag = apps.get_model("manufacturing", "BagSpecification")
        for code, faces in (("TWO", 2), ("ONE", 1), ("NONE", 0)):
            bag.objects.create(code=code, fabric=fabric, bag_width_cm=60, bag_length_cm=100,
                               bag_item=item.objects.create(sku=code, name=code, uom=pcs),
                               print_colours=4, printed_faces=faces)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        bag = executor.loader.project_state(self.after).apps.get_model(
            "manufacturing", "BagSpecification")
        self.assertEqual(
            {row.code: (row.print_colours, row.print_colours_back) for row in bag.objects.all()},
            {"TWO": (4, 4), "ONE": (4, 0), "NONE": (0, 0)},
        )
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
