"""
Leno, D-cut, handles and metallic film. Figures worked by hand and
checked by an independent script before the code; the fixture sack is
60 x 100 on 87.48911 GSM, 1.26 m2, 111.43628 g with 1.2 g thread.

  Handle 2 g: 113.43628 g.
  D-cut of 40 cm2 on a face, through both: 0.008 m2 of fabric, 0.69991 g,
      out of the sack (110.73637 g) but not out of the bill. Its 70%
      recovered joins the cutting waste: 1.97860 + 0.48994 = 2.46854.
      Laminated at 15 GSM it also takes 0.12 g of coating: 129.51637 g.
      With BOPP both faces at 20 micron it takes 0.1456 g of film:
      152.30277 g.
  Metallic 12 micron on the front face (0.63 m2): 6.8796 g, the
      laminated sack 137.21588 g; a 50% window 3.4398 g, 133.77608 g.
  Leno 4 x 4 of 1,000 denier: 34.99564 GSM, the sack 45.29451 g.
  Solving 110.736 g with that D-cut: (110.736 - 1.2) / (1.26 - 0.008)
      = 87.48882 GSM.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.inventory.models import Item

from .tests_woven import WovenTestCase, close


class ExtrasTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        weigh = lambda sku: Item.objects.create(sku=sku, name=sku, uom=self.kg)
        self.coat = weigh("COAT")
        self.film = weigh("BOPP")
        self.metallic = weigh("MET-FILM")
        self.handle = weigh("HANDLE")

    def laminated(self, **overrides):
        values = dict(is_laminated=True, lamination_gsm=Decimal("15"), coating=[(self.coat, 100)])
        values.update(overrides)
        return self.bag(**values)

    def refused(self, message, build, **overrides):
        with self.assertRaises(ValidationError) as caught:
            build(**overrides)
        self.assertIn(message, str(caught.exception))


class HandleTests(ExtrasTestCase):
    def test_a_handle_is_in_the_sack_and_the_bill(self):
        bag = self.bag(handle_item=self.handle, handle_grams=Decimal("2"))
        self.assertTrue(close(bag.bag_grams(), "113.43628"))
        self.assertTrue(close(bag.bom.components.get(item=self.handle).quantity, "2"))
        self.assertEqual(bag.construction(), "unlaminated, with handle")

    def test_a_handle_needs_its_item_and_its_weight(self):
        self.refused("the handle need both", self.bag, handle_item=self.handle)
        self.refused("the handle need both", self.bag, handle_grams=Decimal("2"))


class DCutTests(ExtrasTestCase):
    def test_the_hole_leaves_the_sack_and_not_the_bill(self):
        bag = self.bag(dcut_area_sqcm=Decimal("40"))
        self.assertTrue(close(bag.bag_grams(), "110.73637"))
        self.assertTrue(close(bag.bom.components.get(item=self.fabric_item).quantity, "110.23628"))
        self.assertEqual(bag.construction(), "unlaminated, D-cut")

    def test_what_is_punched_out_joins_the_recovered_waste(self):
        plain = self.bag()
        self.assertTrue(close(plain.bom.byproducts.get().quantity, "1.97860"))
        plain.dcut_area_sqcm = Decimal("40")
        plain.save()
        self.assertTrue(close(plain.bom.byproducts.get().quantity, "2.46854"))

    def test_the_coating_goes_with_the_hole(self):
        self.assertTrue(close(self.laminated(dcut_area_sqcm=Decimal("40")).bag_grams(), "129.51637"))

    def test_film_on_both_faces_goes_with_it(self):
        bag = self.laminated(dcut_area_sqcm=Decimal("40"), bopp_film_item=self.film,
                             bopp_micron=Decimal("20"))
        self.assertTrue(close(bag.bag_grams(), "152.30277"))

    def test_metallic_film_goes_with_it_by_its_coverage(self):
        # 137.21588 less fabric 0.69991, coating 0.12, film 0.004 x 12 x 0.91 = 0.04368
        bag = self.laminated(dcut_area_sqcm=Decimal("40"), metallic_film_item=self.metallic,
                             metallic_micron=Decimal("12"))
        self.assertTrue(close(bag.bag_grams(), "136.35229"))

    def test_a_hole_as_big_as_the_face(self):
        self.refused("no sack left around it", self.bag, dcut_area_sqcm=Decimal("6000"))


class MetallicTests(ExtrasTestCase):
    def test_full_metallic_on_the_front(self):
        bag = self.laminated(metallic_film_item=self.metallic, metallic_micron=Decimal("12"))
        self.assertTrue(close(bag.metallic_grams(), "6.8796"))
        self.assertTrue(close(bag.bag_grams(), "137.21588"))
        self.assertTrue(close(bag.bom.components.get(item=self.metallic).quantity, "6.8796"))
        self.assertEqual(bag.construction(), "laminated, metallic")

    def test_a_window(self):
        bag = self.laminated(metallic_film_item=self.metallic, metallic_micron=Decimal("12"),
                             metallic_coverage_percent=Decimal("50"))
        self.assertTrue(close(bag.bag_grams(), "133.77608"))
        self.assertEqual(bag.construction(), "laminated, metallic window")

    def test_film_needs_the_coating(self):
        self.refused("bonded by the extruded coating", self.bag, metallic_film_item=self.metallic,
                     metallic_micron=Decimal("12"))

    def test_film_needs_its_thickness_and_thickness_its_film(self):
        self.refused("metallic film needs both", self.laminated, metallic_film_item=self.metallic)
        self.refused("metallic film needs both", self.laminated, metallic_micron=Decimal("12"))

    def test_metallic_film_must_be_weighed(self):
        self.metallic.uom = self.pcs
        self.metallic.save()
        self.refused("the metallic film MET-FILM is measured in pcs", self.laminated,
                     metallic_film_item=self.metallic, metallic_micron=Decimal("12"))


class LenoTests(ExtrasTestCase):
    def leno(self):
        return self.fabric(code="LENO", is_leno=True, ends_per_inch=Decimal("4"),
                           picks_per_inch=Decimal("4"), target_gsm=Decimal("35"))

    def test_an_open_mesh_sack(self):
        bag = self.bag(fabric=self.leno())
        self.assertTrue(close(bag.bag_grams(), "45.29451"))
        self.assertEqual(bag.construction(), "leno")

    def test_leno_cannot_be_coated(self):
        self.refused("cannot be coated", self.laminated, fabric=self.leno())

    def test_leno_takes_no_handle(self):
        self.refused("cannot be given a handle", self.bag, fabric=self.leno(),
                     handle_item=self.handle, handle_grams=Decimal("2"))


class SolveWithExtrasTests(ExtrasTestCase):
    def test_the_hole_is_allowed_for_when_solving(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("quoter"))
        response = client.post("/api/manufacturing/bag-specifications/solve/", {
            "bag_width_cm": "60", "bag_length_cm": "100", "thread_grams_per_bag": "1.2",
            "dcut_area_sqcm": "40", "target_grams": "110.736",
            "ends_per_inch": "10", "picks_per_inch": "10",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fabric_gsm"], "87.489")

    def test_a_hole_bigger_than_the_face_is_refused_when_solving(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("quoter"))
        response = client.post("/api/manufacturing/bag-specifications/solve/", {
            "bag_width_cm": "60", "bag_length_cm": "100", "dcut_area_sqcm": "6000",
            "target_grams": "100", "ends_per_inch": "10", "picks_per_inch": "10",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no sack left", str(response.content))
