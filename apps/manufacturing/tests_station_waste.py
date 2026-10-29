"""
Loom waste off L-17, weighed at the loom exit and taken into stock.

The fabric recipe loses 2% at the loom and 85% of the loss is swept up
and kept: 100 x 0.02 / 0.98 x 0.85 = 1.734694 kg a 100 kg of fabric,
as the bill stores it. A 104.4 kg roll should give back 1.734694 x
1.044 = 1.8110205 kg. Weighed at 2.0 kg, the run gave back 0.1889795 kg
more than its recipe. The waste is worth its
standard 30 a kg: 60.00 back out of the run's work in progress.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item

from .orders import ProductionEntry
from .station_floor import book_waste, void_waste, waste_variance
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase
from .woven import FabricSpecification

NOON = at(TODAY, 12)


class WasteTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.sweepings = Item.objects.create(sku="LOOM-WASTE", name="Loom waste", uom=self.kg,
                                             standard_cost=Decimal("30"))
        spec = FabricSpecification.objects.get(pk=self.spec.pk)
        spec.loom_waste_item = self.sweepings
        spec.waste_recovered_percent = Decimal("85")
        spec.save()
        self.spec = spec

    def weigh_waste(self, kg="2.0", **extra):
        return book_waste(self.station, self.operator, self.l17, kg, at=NOON, **extra)


class IntoStockTests(WasteTestCase):
    def test_weighed_into_stock_against_the_run(self):
        entry = self.weigh_waste()
        self.assertEqual((entry.work_order, entry.machine, entry.quantity_produced,
                          entry.posted), (self.run, self.l17, Decimal("0"), True))
        (row,) = entry.byproducts.all()
        self.assertEqual((row.item, row.quantity), (self.sweepings, Decimal("2.0000")))
        self.assertEqual(self.sweepings.on_hand_at(self.plant), Decimal("2"))
        # What came out of the run's work in progress, as for any output.
        self.assertEqual(entry.posted_value, Decimal("60.00"))

    def test_against_what_the_recipe_expected(self):
        self.weigh()
        self.weigh_waste()
        (row,) = waste_variance(self.run)
        self.assertEqual(row["item"], self.sweepings)
        self.assertEqual(row["expected"].quantize(Decimal("0.000001")), Decimal("1.811021"))
        self.assertEqual(row["weighed"], Decimal("2.0000"))
        self.assertEqual(row["difference"].quantize(Decimal("0.000001")),
                         Decimal("0.188979"))

    def test_what_a_weighing_must_be(self):
        with self.assertRaisesMessage(ValidationError, "The waste is more than nothing"):
            self.weigh_waste("0")
        with self.assertRaisesMessage(ValidationError, "is not what"):
            self.weigh_waste(item_code="REGRIND")
        spec = FabricSpecification.objects.get(pk=self.spec.pk)
        spec.loom_waste_item = None
        spec.save()
        with self.assertRaisesMessage(ValidationError, "gives nothing back"):
            self.weigh_waste()
        self.assertFalse(ProductionEntry.objects.filter(quantity_produced=0).exists())

    def test_an_entry_that_books_nothing_is_refused(self):
        entry = ProductionEntry.objects.create(
            work_order=self.run, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("0"), quantity_scrapped=Decimal("0"), uom=self.kg)
        with self.assertRaisesMessage(ValidationError, "books nothing"):
            entry.post()


class TwoKindsOfWasteTests(WasteTestCase):
    def setUp(self):
        super().setUp()
        from .bom import BomByproduct

        self.lumps = Item.objects.create(sku="LUMPS", name="Lumps", uom=self.kg,
                                         standard_cost=Decimal("10"))
        # Straight in: the computed bill refuses a hand-added line, and
        # this is only here to give the run a second thing to give back.
        BomByproduct.objects.bulk_create([BomByproduct(
            bom=self.run.bom, item=self.lumps, quantity=Decimal("1"), uom=self.kg)])

    def test_which_is_said_and_each_counted_on_its_own(self):
        with self.assertRaisesMessage(ValidationError, "say which this is"):
            self.weigh_waste()
        self.weigh_waste(item_code="LUMPS")
        self.weigh_waste("2.0", item_code="LOOM-WASTE")
        weighed = {row["item"].sku: row["weighed"] for row in waste_variance(self.run)}
        self.assertEqual(weighed, {"LOOM-WASTE": Decimal("2.0000"),
                                   "LUMPS": Decimal("2.0000")})

    def test_waste_is_weighed(self):
        Item.objects.filter(pk=self.lumps.pk).update(uom=self.pcs_unit())
        with self.assertRaisesMessage(ValidationError, "waste is weighed"):
            self.weigh_waste(item_code="LUMPS")

    def pcs_unit(self):
        from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

        return UnitOfMeasure.objects.create(code="pcs", name="Pieces",
                                            category=UnitOfMeasureCategory.COUNT)


class WithdrawnTests(WasteTestCase):
    def test_withdrawn_with_a_supervisors_pin(self):
        entry = self.weigh_waste()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_waste(entry, self.station, self.supervisor, self.operator, " ")
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_waste(entry, self.station, self.operator, self.operator, "Wrong loom")
        void_waste(entry, self.station, self.supervisor, self.operator, "Wrong loom")
        self.assertEqual(self.sweepings.on_hand_at(self.plant), Decimal("0"))
        (row,) = waste_variance(self.run)
        self.assertEqual(row["weighed"], Decimal("0"))

    def test_waste_and_scrap_are_withdrawn_each_as_itself(self):
        from .station_floor import void_scrap

        scrap = ProductionEntry.objects.create(
            work_order=self.run, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("0"), quantity_scrapped=Decimal("5"), uom=self.kg,
            machine=self.l17)
        with self.assertRaisesMessage(ValidationError, "is not waste weighed at"):
            void_waste(scrap, self.station, self.supervisor, self.operator, "x")
        waste = self.weigh_waste()
        with self.assertRaisesMessage(ValidationError, "is not scrap booked at"):
            void_scrap(waste, self.station, self.supervisor, self.operator, "x")

    def test_only_waste_weighed_here(self):
        roll = self.weigh()
        with self.assertRaisesMessage(ValidationError, "is not waste weighed at"):
            void_waste(roll.entry, self.station, self.supervisor, self.operator, "x")
        from .station import LoomStation

        other = LoomStation.objects.create(code="LX-9", name="Other", warehouse=self.plant)
        entry = self.weigh_waste()
        with self.assertRaisesMessage(ValidationError, "is not waste weighed at LX-9"):
            void_waste(entry, other, self.supervisor, self.operator, "x")


class WasteApiTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        self.sweepings = Item.objects.create(sku="LOOM-WASTE", name="Loom waste", uom=self.kg,
                                             standard_cost=Decimal("30"))
        spec = FabricSpecification.objects.get(pk=self.spec.pk)
        spec.loom_waste_item = self.sweepings
        spec.waste_recovered_percent = Decimal("85")
        spec.save()

    def test_weighed_withdrawn_and_reported(self):
        self.sign_in()
        response = self.post("run-waste/", {"machine": "L-17", "kg": "2.0"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["item"], body["kg"], body["run"]),
                         ("LOOM-WASTE", "2.0000", self.run.number))
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        office = APIClient()
        office.force_authenticate(User.objects.create_superuser("planner"))
        rows = office.get(f"/api/manufacturing/work-orders/{self.run.pk}/waste/").json()
        self.assertEqual([(row["item"], row["weighed"]) for row in rows],
                         [("LOOM-WASTE", "2.0000")])
        response = self.post(f"run-waste/{body['id']}/void/",
                             {"supervisor_pin": self.supervisor_pin, "reason": "Wrong loom"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.post("run-waste/", {"machine": "L-17", "kg": "0"}).status_code,
                         400)
