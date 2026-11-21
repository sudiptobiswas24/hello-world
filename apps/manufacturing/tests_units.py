"""
A sack counted by weight and by the bale. Figures worked by an
independent script first: the fixture sack weighs 111.43628 g, so a
kilogramme of it is 1000 / 111.43628 = 8.973738 sacks, and 1,000 kg is
8,973.74 sacks. Contracted at 110 g, 1,000 kg is 9,090.91 and two tonnes
18,181.82. A bale of 500 makes three bales 1,500 sacks.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import ItemUnit

from .tests_woven import WovenTestCase, close
from .woven import BagSpecification


class UnitsTestCase(WovenTestCase):
    def setUp(self):
        super().setUp()
        self.bale = UnitOfMeasure.objects.create(code="bale", name="Bale",
                                                 category=UnitOfMeasureCategory.COUNT)
        self.tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"))

    def refused(self, message, call, *args, **kwargs):
        with self.assertRaises(ValidationError) as caught:
            call(*args, **kwargs)
        self.assertIn(message, str(caught.exception))


class BySpecificationTests(UnitsTestCase):
    def test_a_kilogramme_of_sacks_is_what_the_specification_weighs(self):
        self.bag()
        self.assertTrue(close(self.bag_item.to_stock_quantity(Decimal("1000"), self.kg),
                              "8973.7382"))

    def test_at_the_contracted_weight_where_there_is_one(self):
        self.bag(target_grams=Decimal("110"), weight_tolerance_percent=Decimal("2"))
        self.assertTrue(close(self.bag_item.to_stock_quantity(Decimal("1000"), self.kg),
                              "9090.9091"))
        self.assertTrue(close(self.bag_item.to_stock_quantity(Decimal("2"), self.tonne),
                              "18181.8182"))

    def test_a_changed_specification_changes_the_answer(self):
        bag = self.bag()
        bag = BagSpecification.objects.get(pk=bag.pk)
        bag.target_grams = Decimal("110")
        bag.weight_tolerance_percent = Decimal("2")
        bag.save()
        self.assertTrue(close(self.bag_item.unit_factor(self.kg), "9.0909"))

    def test_only_the_specification_in_force_on_the_day(self):
        today = timezone.localdate()
        self.bag(valid_to=today - datetime.timedelta(days=1))
        self.refused("not the same kind of measure", self.bag_item.unit_factor, self.kg)
        self.assertTrue(close(self.bag_item.unit_factor(
            self.kg, on_date=today - datetime.timedelta(days=2)), "8.9737"))

    def test_a_typed_weight_is_refused_where_the_specification_gives_one(self):
        self.bag()
        self.refused("from its own specification", ItemUnit.objects.create,
                     item=self.bag_item, uom=self.kg, factor=Decimal("9"))


class ItemUnitApiTests(UnitsTestCase):
    def test_a_bale_over_the_api_and_a_typed_weight_refused_as_a_sentence(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("stores"))
        response = client.post("/api/inventory/item-units/", {
            "item": self.bag_item.pk, "uom": self.bale.pk, "factor": "500"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.bag()
        response = client.post("/api/inventory/item-units/", {
            "item": self.bag_item.pk, "uom": self.kg.pk, "factor": "9"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("from its own specification", str(response.content))


class TypedUnitTests(UnitsTestCase):
    def test_a_bale_of_five_hundred(self):
        ItemUnit.objects.create(item=self.bag_item, uom=self.bale, factor=Decimal("500"))
        self.assertEqual(self.bag_item.to_stock_quantity(Decimal("3"), self.bale),
                         Decimal("1500"))

    def test_nothing_known_is_refused_not_guessed(self):
        self.refused("Give it a unit conversion for bale", self.bag_item.to_stock_quantity,
                     Decimal("1"), self.bale)

    def test_not_within_one_chain(self):
        dozen = UnitOfMeasure.objects.create(code="dz", name="Dozen", base_unit=self.pcs,
                                             conversion_factor=Decimal("12"))
        self.refused("one chain already", ItemUnit.objects.create,
                     item=self.bag_item, uom=dozen, factor=Decimal("12"))

    def test_not_twice_into_one_chain(self):
        ItemUnit.objects.create(item=self.bag_item, uom=self.bale, factor=Decimal("500"))
        big_bale = UnitOfMeasure.objects.create(code="bale2", name="Double bale",
                                                base_unit=self.bale,
                                                conversion_factor=Decimal("2"))
        self.refused("would disagree", ItemUnit.objects.create,
                     item=self.bag_item, uom=big_bale, factor=Decimal("1000"))
        # ...but a unit in the chain converts through the one conversion
        self.assertEqual(self.bag_item.to_stock_quantity(Decimal("1"), big_bale),
                         Decimal("1000"))

    def test_the_same_chain_converts_exactly_as_before(self):
        self.assertEqual(self.fabric_item.to_stock_quantity(Decimal("2"), self.tonne),
                         Decimal("2000"))
