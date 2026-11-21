"""
The sack's weight as a contract, shrink, and working back from a weight
to the tape that makes it. Every figure below was worked by hand and
checked by an independent script before the code was written.

The fixture fabric is 10 x 10 of 1,000 denier tape with no shrink:
20 x 39.3701 x 1000 / 9000 = 87.48911 GSM, and the flat 60 x 100 sack
on it (1.26 m2, 1.2 g thread) weighs 111.43628 g.

  At 4% shrink the same tape weighs 87.48911 / 0.96 = 91.13449 GSM,
      4.15% over the 87.5 quoted; the sack is 116.02946 g.
  Contracted at 110 g, 2%: 111.43628 is 1.31% over, accepted; inspected
      at 110, 107.8 to 112.2. At 108 g it is 3.18% over and refused.
  Picks 10 -> 10.5: 89.67634 GSM, the sack 114.19219 g (2.47% over
      111.43628). Tape 1000 -> 1020 denier: 89.23889 GSM, sack 113.64101.
      Tape 1100: 96.23802 GSM, 9.99% over 87.5, outside 5%.
  Solving 111.43628 g: (111.43628 - 1.2) / 1.26 = 87.48911 GSM, which
      is 1,000 denier in the fabric and 960 on the line at 4% shrink.
      Solving 100 g with a 960 denier warp: 78.41270 GSM needed, warp
      1,000 in the fabric, weft 792.513 in the fabric, 760.813 on the line.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory.models import Item
from apps.quality.models import PlanLine

from .tests_woven import WovenTestCase, close
from .woven import (
    BagSpecification, FabricSpecification, TapeSpecification, denier_for,
)

SOLVE = "/api/manufacturing/bag-specifications/solve/"


class ContractTestCase(WovenTestCase):
    def weight_line(self, bag):
        return PlanLine.objects.get(plan=bag.inspection_plan)

    def fabric_row(self, bag):
        bag.refresh_from_db()
        return bag.bom.components.get(item=self.fabric_item)


class ShrinkTests(ContractTestCase):
    def test_shrink_makes_the_fabric_heavier_than_its_tapes_laid_straight(self):
        fabric = self.fabric(shrink_percent=Decimal("4"))
        self.assertTrue(close(fabric.gsm(), "91.13449"))
        self.assertTrue(close(self.bag(fabric=fabric).bag_grams(), "116.02946"))

    def test_the_tolerance_is_checked_with_the_shrink_in(self):
        with self.assertRaisesMessage(ValidationError, "at 4% shrink makes 91.1 GSM"):
            self.fabric(shrink_percent=Decimal("4"), gsm_tolerance_percent=Decimal("4"))

    def test_the_database_refuses_a_shrink_that_leaves_no_tape(self):
        fabric = self.fabric()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FabricSpecification.objects.filter(pk=fabric.pk).update(shrink_percent=100)


class ContractTests(ContractTestCase):
    def test_a_sack_inside_its_contract_is_inspected_against_the_contract(self):
        bag = self.bag(target_grams=Decimal("110"), weight_tolerance_percent=Decimal("2"))
        line = self.weight_line(bag)
        self.assertEqual((line.target, line.lower_limit, line.upper_limit),
                         (Decimal("110"), Decimal("107.8"), Decimal("112.2")))
        self.assertTrue(close(bag.weight_deviation_percent(), "1.3057"))

    def test_a_sack_outside_its_contract_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "3.2% from the 108 g contracted"):
            self.bag(target_grams=Decimal("108"), weight_tolerance_percent=Decimal("2"))

    def test_under_weight_is_outside_the_contract_too(self):
        # 111.43628 against 115 is 3.10% light
        with self.assertRaisesMessage(ValidationError, "3.1% from the 115 g contracted"):
            self.bag(target_grams=Decimal("115"), weight_tolerance_percent=Decimal("2"))

    def test_without_a_contract_the_computed_weight_is_inspected(self):
        line = self.weight_line(self.bag())
        self.assertTrue(close(line.target, "111.43628"))

    def test_the_database_refuses_a_contract_of_nothing(self):
        bag = self.bag()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BagSpecification.objects.filter(pk=bag.pk).update(target_grams=0)


class CascadeTests(ContractTestCase):
    """A change upstream reaches every specification computed from it."""

    def test_a_fabric_change_rebuilds_the_sacks_cut_from_it(self):
        bag = self.bag()
        bag.fabric.picks_per_inch = Decimal("10.5")
        bag.fabric.save()
        self.assertTrue(close(self.fabric_row(bag).quantity, "112.99219"))
        self.assertTrue(close(self.weight_line(bag).target, "114.19219"))

    def test_a_fabric_change_that_breaks_a_contract_is_refused(self):
        bag = self.bag(target_grams=Decimal("111.43628"), weight_tolerance_percent=Decimal("2"))
        fabric = FabricSpecification.objects.get(pk=bag.fabric_id)
        fabric.picks_per_inch = Decimal("10.5")
        with self.assertRaisesMessage(ValidationError, "B60X100: the sack as specified weighs 114.19 g"):
            fabric.save()
        self.assertEqual(FabricSpecification.objects.get(pk=fabric.pk).picks_per_inch, Decimal("10"))

    def test_a_tape_change_reaches_the_sack_through_the_fabric(self):
        bag = self.bag()
        tape = bag.fabric.warp_tape
        tape.denier = Decimal("1020")
        tape.save()
        self.assertTrue(close(self.fabric_row(bag).quantity, "112.44101"))

    def test_a_tape_change_that_puts_a_fabric_off_its_quote_is_refused(self):
        tape = self.bag().fabric.warp_tape
        tape.denier = Decimal("1100")
        with self.assertRaisesMessage(ValidationError, "F87: a 10.00 x 10.00 mesh"):
            tape.save()
        self.assertEqual(TapeSpecification.objects.get(pk=tape.pk).denier, Decimal("1000"))

    def test_a_tape_used_only_as_weft_still_reaches_its_fabric(self):
        weft = self.tape(code="T1020", tape_item=Item.objects.create(
            sku="TAPE-1020", name="Tape 1020", uom=self.kg), denier=Decimal("1000"))
        bag = self.bag(fabric=self.fabric(weft_tape=weft))
        weft.denier = Decimal("1040")
        weft.save()
        # 10 x 39.3701 x (1000 + 1040) / 9000 = 89.23889 GSM
        self.assertTrue(close(self.weight_line(bag).target, "113.64101"))


class SolveTests(ContractTestCase):
    def sack(self, **values):
        base = dict(bag_width_cm=Decimal("60"), bag_length_cm=Decimal("100"),
                    bottom_hem_cm=Decimal("3"), top_hem_cm=Decimal("2"),
                    thread_grams_per_bag=Decimal("1.2"))
        base.update(values)
        return BagSpecification(**base)

    def test_back_from_the_weight_to_the_tape(self):
        gsm = self.sack().fabric_gsm_for("111.43628")
        self.assertTrue(close(gsm, "87.48911"))
        deniers = denier_for(gsm, 10, 10, 4)
        self.assertTrue(close(deniers["warp_fabric_denier"], "1000"))
        self.assertTrue(close(deniers["weft_tape_denier"], "960"))

    def test_with_the_warp_fixed_the_weft_makes_up_the_rest(self):
        gsm = self.sack().fabric_gsm_for("100")
        self.assertTrue(close(gsm, "78.41270"))
        deniers = denier_for(gsm, 10, 10, 4, warp_tape_denier=960)
        self.assertTrue(close(deniers["warp_fabric_denier"], "1000"))
        self.assertTrue(close(deniers["weft_fabric_denier"], "792.51332"))
        self.assertTrue(close(deniers["weft_tape_denier"], "760.81279"))

    def test_a_weight_the_add_ons_already_exceed(self):
        with self.assertRaisesMessage(ValidationError, "already weighs 1.20 g"):
            self.sack().fabric_gsm_for("1.2")

    def test_a_warp_that_leaves_nothing_for_the_weft(self):
        with self.assertRaisesMessage(ValidationError, "nothing left for the weft"):
            denier_for(Decimal("78.4127"), 10, 10, 4, warp_tape_denier=2000)

    def test_a_mesh_with_no_tapes_one_way(self):
        with self.assertRaisesMessage(ValidationError, "tapes both ways"):
            denier_for(Decimal("80"), 0, 10, 4)

    def test_film_counts_before_any_film_item_is_chosen(self):
        sack = self.sack(is_laminated=True, lamination_gsm=Decimal("15"),
                         bopp_micron=Decimal("20"))
        # 18.9 coat + 22.932 film + 1.2 thread
        self.assertTrue(close(sack.addon_grams(), "43.032"))
        self.assertTrue(close(sack.fabric_gsm_for("153.26828"), "87.48911"))


class SolveApiTests(ContractTestCase):
    body = {"bag_width_cm": "60", "bag_length_cm": "100", "target_grams": "111.437",
            "thread_grams_per_bag": "1.2",
            "ends_per_inch": "10", "picks_per_inch": "10"}

    def client_with(self, *codenames):
        user = User.objects.create_user(f"quoter{User.objects.count()}")
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        return client

    def test_the_fabric_and_the_tape_for_a_contracted_weight(self):
        near = self.fabric()
        heavy_tape = self.tape(code="T1100", denier=Decimal("1100"), tape_item=Item.objects.create(
            sku="TAPE-1100", name="Tape 1100", uom=self.kg))
        self.fabric(code="F96", tape=heavy_tape, target_gsm=Decimal("96"),
                    fabric_item=Item.objects.create(sku="FAB-96", name="F96", uom=self.kg))
        light_tape = self.tape(code="T900", denier=Decimal("900"), tape_item=Item.objects.create(
            sku="TAPE-900", name="Tape 900", uom=self.kg))
        # 78.74020 GSM: a 100.41 g sack, 9.9% light
        self.fabric(code="F79", tape=light_tape, target_gsm=Decimal("79"),
                    fabric_item=Item.objects.create(sku="FAB-79", name="F79", uom=self.kg))
        self.fabric(code="F50", lay_flat_width_cm=Decimal("50"),
                    tape=near.warp_tape,
                    fabric_item=Item.objects.create(sku="FAB-50", name="F50", uom=self.kg))
        response = self.client_with("view_bagspecification").post(SOLVE, self.body, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["fabric_gsm"], body["warp_fabric_denier"], body["warp_tape_denier"]),
                         ("87.490", "1000.0", "960.0"))
        self.assertEqual(body["shrink_percent"], "4")
        self.assertEqual([row["code"] for row in body["fabrics"]], ["F87"])
        # 111.43628 against 111.437 is 0.0006% light, which shows as
        # nothing at two places, and must not show as "-0.00".
        self.assertEqual(body["fabrics"][0]["deviation_percent"], "0.00")

    def test_a_fabric_whose_specification_has_ended_is_not_offered(self):
        today = timezone.localdate()
        self.fabric(valid_to=today - datetime.timedelta(days=1))
        self.fabric(code="F87-NEXT", tape=FabricSpecification.objects.get().warp_tape,
                    valid_from=today + datetime.timedelta(days=30),
                    fabric_item=Item.objects.create(sku="FAB-NEXT", name="Next", uom=self.kg))
        body = self.client_with("view_bagspecification").post(SOLVE, self.body, format="json").json()
        self.assertEqual([row["code"] for row in body["fabrics"]], ["F87-NEXT"])

    def test_quoting_needs_only_the_right_to_see_specifications(self):
        self.assertEqual(self.client_with().post(SOLVE, self.body, format="json").status_code, 403)
        viewer = self.client_with("view_bagspecification")
        self.assertEqual(viewer.post(SOLVE, self.body, format="json").status_code, 200)
        self.assertEqual(viewer.post("/api/manufacturing/bag-specifications/", {},
                                     format="json").status_code, 403)

    def test_an_impossible_sack_is_a_sentence(self):
        client = self.client_with("view_bagspecification")
        response = client.post(SOLVE, {**self.body, "gusset_cm": "30"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("leave no face", str(response.content))
        response = client.post(SOLVE, {**self.body, "target_grams": "1"}, format="json")
        self.assertIn("already weighs", str(response.content))

    def test_a_new_fabric_takes_the_plants_shrink(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        response = client.post("/api/manufacturing/fabric-specifications/", {
            "code": "F-NEW", "fabric_item": self.fabric_item.pk, "warp_tape": self.tape().pk,
            "ends_per_inch": "10", "picks_per_inch": "10", "lay_flat_width_cm": "60",
            "weave": "tubular", "target_gsm": "91",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["shrink_percent"], "4.00")
        self.assertTrue(close(str(response.json()["gsm"]), "91.134"))


# Slow: it unwinds every later migration and replays them.
@tag("migration")
class ExistingFabricsKeepTheirWeight(TransactionTestCase):
    """The migration must not reweigh a fabric whose sacks were costed on it."""

    before = [("manufacturing", "0030_bag_constructions")]
    after = [("manufacturing", "0031_weight_contract_and_shrink")]

    def test_existing_rows_arrive_at_no_shrink(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        uom = apps.get_model("core", "UnitOfMeasure").objects.create(
            code="kg", name="kg", category="weight")
        item = apps.get_model("inventory", "Item")
        tape = apps.get_model("manufacturing", "TapeSpecification").objects.create(
            code="T", tape_item=item.objects.create(sku="T", name="T", uom=uom),
            denier=1000, tape_width_mm=2.5,
            virgin_granule=item.objects.create(sku="PP", name="PP", uom=uom))
        apps.get_model("manufacturing", "FabricSpecification").objects.create(
            code="F", fabric_item=item.objects.create(sku="F", name="F", uom=uom),
            warp_tape=tape, ends_per_inch=10, picks_per_inch=10,
            lay_flat_width_cm=60, target_gsm=87.5)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        fabric = apps.get_model("manufacturing", "FabricSpecification").objects.get(code="F")
        self.assertEqual(fabric.shrink_percent, Decimal("0"))
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
