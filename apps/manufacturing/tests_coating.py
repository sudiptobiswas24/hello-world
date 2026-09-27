"""
A sack's coating as a blend, written only through its specification.

The fixture sack laminated at 15 GSM carries 1.26 x 15 = 18.9 g of
coating (130.33628 g in all). Lam PP 80 + LDPE 20 splits it 15.12 and
3.78; PP 70 + CC 10 + LDPE 20 is 13.23, 1.89 and 3.78; 4 and 1 is the
same blend as 80 and 20. The sack weighs the same whatever the blend.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.inventory.models import Item

from .tests_woven import WovenTestCase, close
from .woven import BagCoatingLine, BagSpecification


class CoatingTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        weigh = lambda sku: Item.objects.create(sku=sku, name=sku, uom=self.kg)
        self.lam_pp = weigh("LAM-PP")
        self.ldpe = weigh("LDPE-1070")
        self.lam_cc = weigh("LAM-CC")

    def coated(self, blend, **overrides):
        return self.bag(is_laminated=True, lamination_gsm=Decimal("15"), coating=blend,
                        **overrides)

    def rows(self, bag):
        return {row.item.sku: row.quantity for row in bag.bom.components.all()}

    def refused(self, message, build, *args, **kwargs):
        with self.assertRaises(ValidationError) as caught:
            build(*args, **kwargs)
        self.assertIn(message, str(caught.exception))


class BlendTests(CoatingTestCase):
    def test_the_coating_is_split_by_share(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        rows = self.rows(bag)
        self.assertTrue(close(rows["LAM-PP"], "15.12"))
        self.assertTrue(close(rows["LDPE-1070"], "3.78"))
        self.assertTrue(close(bag.bag_grams(), "130.33628"))

    def test_three_polymers(self):
        rows = self.rows(self.coated([(self.lam_pp, 70), (self.lam_cc, 10), (self.ldpe, 20)]))
        self.assertTrue(close(rows["LAM-PP"], "13.23"))
        self.assertTrue(close(rows["LAM-CC"], "1.89"))
        self.assertTrue(close(rows["LDPE-1070"], "3.78"))

    def test_shares_are_relative(self):
        rows = self.rows(self.coated([(self.lam_pp, 4), (self.ldpe, 1)]))
        self.assertTrue(close(rows["LAM-PP"], "15.12"))

    def test_changing_the_blend_rebuilds_the_bill(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        bag = BagSpecification.objects.get(pk=bag.pk)
        bag.set_coating([(self.lam_pp, 100)])
        bag.save()
        self.assertEqual(self.rows(bag).get("LDPE-1070"), None)
        self.assertTrue(close(self.rows(bag)["LAM-PP"], "18.9"))
        self.assertEqual(bag.coating_lines.count(), 1)

    def test_saving_again_does_not_rewrite_the_blend(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        before = list(bag.coating_lines.values_list("id", flat=True))
        bag.lamination_gsm = Decimal("20")
        bag.save()
        self.assertEqual(list(bag.coating_lines.values_list("id", flat=True)), before)

    def test_a_save_that_does_not_mention_the_blend_keeps_it(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        bag = BagSpecification.objects.get(pk=bag.pk)
        bag.lamination_gsm = Decimal("20")
        bag.save()
        # 1.26 x 20 = 25.2 g, split 20.16 and 5.04
        self.assertTrue(close(self.rows(bag)["LDPE-1070"], "5.04"))


class RefusalTests(CoatingTestCase):
    def test_a_laminated_sack_needs_a_blend(self):
        self.refused("does not say what with", self.coated, [])

    def test_a_blend_on_an_unlaminated_sack(self):
        self.refused("not laminated", self.bag, coating=[(self.lam_pp, 100)])

    def test_a_share_of_nothing(self):
        self.refused("more than nothing", self.coated, [(self.lam_pp, 80), (self.ldpe, 0)])

    def test_the_same_polymer_twice(self):
        self.refused("in the coating twice", self.coated, [(self.lam_pp, 80), (self.lam_pp, 20)])

    def test_a_coating_polymer_must_be_weighed(self):
        self.ldpe.uom = self.pcs
        self.ldpe.save()
        self.refused("the coating LDPE-1070 is measured in pcs", self.coated,
                     [(self.lam_pp, 80), (self.ldpe, 20)])

    def test_a_refused_blend_leaves_the_old_one(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        bag = BagSpecification.objects.get(pk=bag.pk)
        bag.set_coating([(self.lam_pp, 80), (self.lam_pp, 20)])
        with self.assertRaises(ValidationError):
            bag.save()
        self.assertEqual(BagCoatingLine.objects.filter(specification=bag).count(), 2)

    def test_a_line_cannot_be_changed_behind_the_specification(self):
        bag = self.coated([(self.lam_pp, 80), (self.ldpe, 20)])
        line = bag.coating_lines.first()
        line.parts = Decimal("90")
        self.refused("through its specification", line.save)
        self.refused("through its specification", line.delete)
        self.refused("through its specification", BagCoatingLine(
            specification=bag, item=self.lam_cc, parts=Decimal("5")).save)


class CoatingApiTests(CoatingTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("planner"))
        self.body = {"code": "B-LAM", "bag_item": self.bag_item.pk, "fabric": self.fabric().pk,
                     "bag_width_cm": "60", "bag_length_cm": "100", "is_laminated": True,
                     "lamination_gsm": "15", "thread_item": self.thread.pk,
                     "thread_grams_per_bag": "1.2"}
        self.url = "/api/manufacturing/bag-specifications/"

    def test_created_with_its_blend(self):
        response = self.client.post(self.url, {**self.body, "coating": [
            {"item": self.lam_pp.pk, "parts": "80"}, {"item": self.ldpe.pk, "parts": "20"}]},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([(row["item"], row["parts"]) for row in response.json()["coating"]],
                         [(self.lam_pp.pk, "80.000"), (self.ldpe.pk, "20.000")])
        bag = BagSpecification.objects.get(code="B-LAM")
        self.assertTrue(close(self.rows(bag)["LDPE-1070"], "3.78"))

    def test_laminated_without_a_blend_is_a_sentence(self):
        response = self.client.post(self.url, self.body, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not say what with", str(response.content))
        self.assertFalse(BagSpecification.objects.filter(code="B-LAM").exists())

    def test_patching_other_figures_keeps_the_blend(self):
        pk = self.client.post(self.url, {**self.body, "coating": [
            {"item": self.lam_pp.pk, "parts": "100"}]}, format="json").json()["id"]
        response = self.client.patch(f"{self.url}{pk}/", {"lamination_gsm": "20"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["coating"]), 1)
        response = self.client.patch(f"{self.url}{pk}/", {"coating": [
            {"item": self.ldpe.pk, "parts": "1"}]}, format="json")
        self.assertEqual([row["item"] for row in response.json()["coating"]], [self.ldpe.pk])


class SinglePolymerMigrates(TransactionTestCase):
    before = [("manufacturing", "0032_fold_thread_colours_solvents")]
    after = [("manufacturing", "0033_coating_blend")]

    def test_the_polymer_becomes_the_whole_blend(self):
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
        coat = item.objects.create(sku="COAT", name="COAT", uom=uom)
        bag = apps.get_model("manufacturing", "BagSpecification")
        bag.objects.create(code="LAM", fabric=fabric, bag_width_cm=60, bag_length_cm=100,
                           bag_item=item.objects.create(sku="LAM", name="LAM", uom=pcs),
                           is_laminated=True, lamination_gsm=15, lamination_item=coat)
        bag.objects.create(code="PLAIN", fabric=fabric, bag_width_cm=60, bag_length_cm=100,
                           bag_item=item.objects.create(sku="PLAIN", name="PLAIN", uom=pcs))
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        line = executor.loader.project_state(self.after).apps.get_model(
            "manufacturing", "BagCoatingLine")
        self.assertEqual([(row.specification.code, row.item.sku, row.parts)
                          for row in line.objects.all()], [("LAM", "COAT", Decimal("100"))])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
