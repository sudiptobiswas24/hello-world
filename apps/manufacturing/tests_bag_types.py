"""
The sacks the plant makes: unlaminated, laminated, BOPP, gusseted,
valve and liner. Every weight here was worked out by hand, then checked
by an independent script, before any code was written.

The fixture's fabric is 87.48911 GSM (a 10 x 10 mesh of 1,000 denier)
woven as a tube 60 cm lay-flat.

  Flat 60 x 100, hems 3 + 2 = 105 cm cut, 2 x 0.60 x 1.05 = 1.26 m2:
      fabric 110.2363 g + thread 1.2              = 111.4363 g
  Laminated at 15 GSM: + 1.26 x 15 = 18.9          = 130.3363 g
  Gusseted 60 x 100 with 5 cm gussets: the width is the tube's, gussets
      folded inside it, so the same 1.26 m2 and 111.4363 g; the face is
      50 cm, so printed 2 x 0.5 x 1.0 = 1.0 m2
  BOPP both faces, gusseted: tie coat 18.9 + film 1.26 x 20 micron x 0.91
      = 22.932 + ink 1.0 m2 x 3 g x 4 colours = 12.0 = 165.2683 g
  BOPP one face: half the film, 11.466         = 153.8023 g
  Valve, block bottom, 50 x 80 on a 50 cm tube, fold allowances 6 + 6 =
      92 cm cut, 0.92 m2: fabric 80.4900 + coat 18.4 + valve 4.5 +
      covers 7.0, no thread                       = 110.3900 g
  Liner 62 x 110 cm at 50 micron LDPE: 2 x 0.62 x 1.10 x 50 x 0.92
      = 62.744 g, on the flat sack                = 174.1803 g
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.inventory.models import Item

from .tests_woven import WovenTestCase, close
from .woven import BagSpecification


class BagTypesTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        weigh = lambda sku: Item.objects.create(sku=sku, name=sku, uom=self.kg)
        self.coat = weigh("PP-COAT")
        self.film = weigh("BOPP-20")
        self.ink = weigh("INK")
        self.ldpe = weigh("LINER-LDPE")
        self.valve = weigh("VALVE-PATCH")
        self.cover = weigh("COVER-SHEET")
        self.tubes = {}

    def tube(self, width):
        """One fabric per lay-flat width, all on one tape, built once a test."""
        if not self.tubes:
            self.tubes[60] = self.fabric()
        if width not in self.tubes:
            self.tubes[width] = self.fabric(
                tape=self.tubes[60].warp_tape, code=f"F{width}",
                lay_flat_width_cm=Decimal(width),
                fabric_item=Item.objects.create(sku=f"FAB-{width}", name=f"Fabric {width}",
                                                uom=self.kg),
            )
        return self.tubes[width]

    def bag(self, fabric=None, **overrides):
        return super().bag(fabric=fabric or self.tube(60), **overrides)

    def gusseted(self, **overrides):
        values = dict(gusset_cm=Decimal("5"))
        values.update(overrides)
        return self.bag(**values)

    def bopp(self, **overrides):
        values = dict(
            is_laminated=True, lamination_gsm=Decimal("15"), lamination_item=self.coat,
            bopp_film_item=self.film, bopp_micron=Decimal("20"),
            print_colours=4, ink_item=self.ink,
        )
        values.update(overrides)
        return self.gusseted(**values)

    def valve_bag(self, **overrides):
        values = dict(
            fabric=self.tube(50), bag_width_cm=Decimal("50"), bag_length_cm=Decimal("80"),
            bottom_hem_cm=Decimal("6"), top_hem_cm=Decimal("6"), closure="welded",
            is_laminated=True, lamination_gsm=Decimal("20"), lamination_item=self.coat,
            thread_item=None, thread_grams_per_bag=Decimal("0"),
            valve_patch_item=self.valve, valve_patch_grams=Decimal("4.5"),
            cover_patch_item=self.cover, cover_patch_grams=Decimal("7.0"),
        )
        values.update(overrides)
        return self.bag(**values)

    def lined(self, **overrides):
        values = dict(liner_item=self.ldpe, liner_micron=Decimal("50"),
                      liner_width_cm=Decimal("62"), liner_length_cm=Decimal("110"))
        values.update(overrides)
        return self.bag(**values)

    def row(self, bag, item):
        return bag.bom.components.get(item=item)

    def refused(self, message, build, **overrides):
        with self.assertRaises(ValidationError) as caught:
            build(**overrides)
        self.assertIn(message, str(caught.exception))


class UnlaminatedAndLaminatedTests(BagTypesTestCase):
    def test_unlaminated_is_as_it_always_was(self):
        bag = self.bag()
        self.assertTrue(close(bag.bag_grams(), "111.4363"))
        self.assertEqual(bag.construction(), "unlaminated")

    def test_laminated(self):
        bag = self.bag(is_laminated=True, lamination_gsm=Decimal("15"), lamination_item=self.coat)
        self.assertTrue(close(bag.bag_grams(), "130.3363"))
        self.assertTrue(close(self.row(bag, self.coat).quantity, "18.9"))
        self.assertEqual(bag.construction(), "laminated")


class GussetedTests(BagTypesTestCase):
    def test_the_gussets_fold_inside_the_width(self):
        bag = self.gusseted()
        self.assertEqual(bag.face_width_cm(), Decimal("50"))
        self.assertTrue(close(bag.fabric_area_sqm(), "1.26"))
        self.assertTrue(close(bag.bag_grams(), "111.4363"))
        self.assertTrue(close(bag.printed_area_sqm(), "1.0"))
        self.assertEqual(bag.construction(), "unlaminated, gusseted")

    def test_gussets_that_meet_leave_no_face(self):
        self.refused("leave no face", self.gusseted, gusset_cm=Decimal("30"))
        self.assertEqual(self.gusseted(gusset_cm=Decimal("29.5")).face_width_cm(), Decimal("1"))

    def test_the_database_refuses_gussets_that_meet(self):
        bag = self.gusseted()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BagSpecification.objects.filter(pk=bag.pk).update(gusset_cm=Decimal("30"))

    def test_a_negative_gusset_is_refused_by_the_database(self):
        bag = self.gusseted()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BagSpecification.objects.filter(pk=bag.pk).update(gusset_cm=Decimal("-1"))


class BoppTests(BagTypesTestCase):
    def test_worked_by_hand(self):
        bag = self.bopp()
        self.assertTrue(close(bag.bopp_grams(), "22.932"))
        self.assertTrue(close(bag.bag_grams(), "165.2683"))
        self.assertEqual(bag.construction(), "BOPP laminated, gusseted")

    def test_film_on_one_face_is_half_the_film(self):
        bag = self.bopp(bopp_faces=1)
        self.assertTrue(close(bag.bopp_grams(), "11.466"))
        self.assertTrue(close(bag.bag_grams(), "153.8023"))

    def test_film_must_be_weighed(self):
        self.film.uom = self.pcs
        self.film.save()
        self.refused("the BOPP film BOPP-20 is measured in pcs", self.bopp)

    def test_the_bill_carries_film_tie_coat_and_ink(self):
        bag = self.bopp()
        film = self.row(bag, self.film)
        self.assertTrue(close(film.quantity, "22.932"))
        self.assertEqual(film.waste_percent, Decimal("4"))
        self.assertTrue(close(self.row(bag, self.coat).quantity, "18.9"))
        self.assertTrue(close(self.row(bag, self.ink).quantity, "12"))

    def test_the_weight_check_is_the_bopp_sacks_weight(self):
        from apps.quality.models import PlanLine

        line = PlanLine.objects.get(plan=self.bopp().inspection_plan)
        self.assertTrue(close(line.target, "165.2683"))

    def test_film_needs_its_thickness_and_thickness_its_film(self):
        self.refused("both the film and its thickness", self.bopp, bopp_micron=Decimal("0"))
        self.refused("both the film and its thickness", self.bopp, bopp_film_item=None)

    def test_film_needs_the_coat_that_bonds_it(self):
        self.refused("bonded by the extruded coating", self.bopp, is_laminated=False,
                     lamination_gsm=Decimal("0"), lamination_item=None)


class ValveTests(BagTypesTestCase):
    def test_worked_by_hand(self):
        bag = self.valve_bag()
        self.assertEqual(bag.cut_length_cm(), Decimal("92"))
        self.assertTrue(close(bag.bag_grams(), "110.3900"))
        self.assertEqual(bag.construction(), "laminated, valve, block bottom")
        self.assertTrue(close(self.row(bag, self.valve).quantity, "4.5"))
        self.assertTrue(close(self.row(bag, self.cover).quantity, "7.0"))
        self.assertFalse(bag.bom.components.filter(item=self.thread).exists())

    def test_a_welded_sack_must_be_coated(self):
        self.refused("nothing to weld", self.valve_bag, is_laminated=False,
                     lamination_gsm=Decimal("0"), lamination_item=None)

    def test_a_welded_sack_has_no_thread(self):
        self.refused("is not sewn", self.valve_bag, thread_item=self.thread,
                     thread_grams_per_bag=Decimal("1.2"))
        self.refused("is not sewn", self.valve_bag, thread_grams_per_bag=Decimal("1.2"))
        self.refused("is not sewn", self.valve_bag, thread_item=self.thread)

    def test_a_valve_needs_its_item_and_its_weight(self):
        self.refused("the valve need both", self.valve_bag, valve_patch_grams=Decimal("0"))
        self.refused("the valve need both", self.valve_bag, valve_patch_item=None)
        self.refused("the cover sheets need both", self.valve_bag, cover_patch_item=None)

    def test_cover_sheets_must_be_weighed(self):
        self.cover.uom = self.pcs
        self.cover.save()
        self.refused("the cover sheets COVER-SHEET is measured in pcs", self.valve_bag)

    def test_a_sewn_valve_sack_is_allowed(self):
        bag = self.valve_bag(closure="sewn", thread_item=self.thread,
                             thread_grams_per_bag=Decimal("1.2"),
                             cover_patch_item=None, cover_patch_grams=Decimal("0"))
        self.assertEqual(bag.construction(), "laminated, valve")

    def test_an_unknown_closure_is_refused_by_the_database(self):
        bag = self.valve_bag()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BagSpecification.objects.filter(pk=bag.pk).update(closure="glued")


class LinerTests(BagTypesTestCase):
    def test_computed_from_its_film(self):
        bag = self.lined()
        self.assertTrue(close(bag.liner_grams(), "62.744"))
        self.assertTrue(close(bag.bag_grams(), "174.1803"))
        self.assertTrue(close(self.row(bag, self.ldpe).quantity, "62.744"))
        self.assertEqual(bag.construction(), "unlaminated, with liner")

    def test_or_typed_as_a_weight(self):
        bag = self.bag(liner_item=self.ldpe, liner_grams_per_bag=Decimal("60"))
        self.assertTrue(close(bag.bag_grams(), "171.4363"))

    def test_not_both(self):
        self.refused("Give one or the other", self.lined, liner_grams_per_bag=Decimal("60"))

    def test_a_computed_liner_needs_its_size(self):
        self.refused("needs its width and length", self.lined, liner_length_cm=Decimal("0"))

    def test_a_liner_nobody_named_is_refused(self):
        """It used to count in the sack's weight and in no bill at all."""
        self.refused("does not say what it is", self.bag, liner_grams_per_bag=Decimal("60"))
        self.refused("does not say what it is", self.lined, liner_item=None)


class CombinedTests(BagTypesTestCase):
    def test_bopp_gusseted_with_a_liner(self):
        bag = self.bopp(liner_item=self.ldpe, liner_micron=Decimal("50"),
                        liner_width_cm=Decimal("62"), liner_length_cm=Decimal("110"))
        # 165.2683 + 62.744
        self.assertTrue(close(bag.bag_grams(), "228.0123"))
        self.assertEqual(bag.construction(), "BOPP laminated, gusseted, with liner")


class BagTypesApiTests(BagTypesTestCase):
    def test_a_bopp_gusseted_sack_over_the_api(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        fabric = self.tube(60)
        response = client.post("/api/manufacturing/bag-specifications/", {
            "code": "BOPP-50", "bag_item": self.bag_item.pk, "fabric": fabric.pk,
            "bag_width_cm": "60", "bag_length_cm": "100", "gusset_cm": "5",
            "bottom_hem_cm": "3", "top_hem_cm": "2",
            "thread_item": self.thread.pk, "thread_grams_per_bag": "1.2",
            "is_laminated": True, "lamination_gsm": "15", "lamination_item": self.coat.pk,
            "bopp_film_item": self.film.pk, "bopp_micron": "20", "bopp_faces": 2,
            "print_colours": 4, "ink_item": self.ink.pk,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["construction"], "BOPP laminated, gusseted")
        self.assertTrue(close(body["bag_grams"], "165.2683"))

    def test_a_refusal_is_a_sentence(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        response = client.post("/api/manufacturing/bag-specifications/", {
            "code": "BAD", "bag_item": self.bag_item.pk, "fabric": self.tube(60).pk,
            "bag_width_cm": "60", "bag_length_cm": "100", "closure": "welded",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("nothing to weld", str(response.content))
