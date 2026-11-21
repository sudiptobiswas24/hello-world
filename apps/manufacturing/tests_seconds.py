"""
The 60 x 100 sack loses 2.5% of what is fed into conversion; 1% of the
feed comes out a second, sold as BAG-2ND at 5.00. A thousand firsts are
fed 1,000 / 0.975 sacks' worth, so 10.256410 seconds come with them;
the offcut swept up is the other 1.5%, not the whole 2.5%.

36 seconds counted at C-1 for a print defect are worth 180.00, taken out
of the run's work in progress; withdrawn, they go back.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item

from .bom import ByproductValuation
from .scrap import ScrapReason
from .station_floor import book_seconds, void_waste, waste_variance
from .tests_bag_counts import ConversionTestCase
from .tests_orders import TODAY
from .tests_station import at
from .woven import BagSpecification

NOON = at(TODAY, 12)


class SecondsTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.seconds = Item.objects.create(sku="BAG-2ND", name="Seconds", uom=self.pcs,
                                           standard_cost=Decimal("5"))
        ScrapReason.objects.create(code="PRINT", name="Print defect")
        self.graded = self.grade(seconds_item=self.seconds, seconds_percent=Decimal("1"))

    def grade(self, **values):
        spec = BagSpecification.objects.get(pk=self.bag_spec.pk)
        for name, value in values.items():
            setattr(spec, name, value)
        spec.save()
        return spec

    def seconds_off(self, pieces="36", reason="PRINT"):
        return book_seconds(self.cv, self.operator, self.c1, pieces, reason, at=NOON)


class InTheRecipeTests(SecondsTestCase):
    def test_a_thousand_firsts_bring_their_seconds(self):
        row = self.graded.bom.byproducts.get(item=self.seconds)
        self.assertEqual((row.quantity, row.uom, row.valuation),
                         (Decimal("10.256410"), self.pcs, ByproductValuation.STANDARD))

    def test_and_what_is_a_second_is_not_offcut_too(self):
        waste = Item.objects.create(sku="CUT-WASTE", name="Offcut", uom=self.kg,
                                    standard_cost=Decimal("30"))
        spec = self.grade(cutting_waste_item=waste, waste_recovered_percent=Decimal("85"))
        offcut = spec.bom.byproducts.get(item=waste).quantity
        expected = (spec.fabric_grams() * Decimal("0.015") / Decimal("0.975")
                    * Decimal("0.85")).quantize(Decimal("0.000001"))
        self.assertEqual(offcut, expected)

    def test_what_seconds_cannot_be(self):
        with self.assertRaisesMessage(ValidationError, "both the item they are sold as"):
            self.grade(seconds_percent=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "leaves nothing for offcut"):
            self.grade(seconds_percent=Decimal("2.5"))
        weighed = Item.objects.create(sku="BAG-KG", name="By weight", uom=self.kg)
        with self.assertRaisesMessage(ValidationError, "seconds are counted"):
            self.grade(seconds_item=weighed)


class CountedOffTheMachineTests(SecondsTestCase):
    def test_counted_into_stock_at_their_own_value(self):
        entry = self.seconds_off()
        (row,) = entry.byproducts.all()
        self.assertEqual((row.item, row.quantity, entry.quantity_produced),
                         (self.seconds, Decimal("36.0000"), Decimal("0")))
        self.assertEqual(self.seconds.on_hand_at(self.plant), Decimal("36"))
        self.assertEqual(entry.posted_value, Decimal("180.00"))
        self.assertIn("Seconds, PRINT", entry.memo)

    def test_against_what_the_recipe_expected(self):
        self.count()
        self.seconds_off()
        rows = {row["item"].sku: row for row in waste_variance(self.bag_run)}
        # 500 firsts expect 5.128205 seconds; 36 were counted.
        self.assertEqual(rows["BAG-2ND"]["expected"].quantize(Decimal("0.000001")),
                         Decimal("5.128205"))
        self.assertEqual(rows["BAG-2ND"]["weighed"], Decimal("36.0000"))

    def test_what_a_count_must_be(self):
        with self.assertRaisesMessage(ValidationError, "NOPE is not a defect in use"):
            self.seconds_off(reason="NOPE")
        ScrapReason.objects.create(code="OLD", name="Retired", is_active=False)
        with self.assertRaisesMessage(ValidationError, "OLD is not a defect in use"):
            self.seconds_off(reason="OLD")
        with self.assertRaisesMessage(ValidationError, "The seconds is more than nothing"):
            self.seconds_off("0")
        self.grade(seconds_item=None, seconds_percent=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "has no seconds item"):
            self.seconds_off()

    def test_withdrawn_like_waste(self):
        entry = self.seconds_off()
        void_waste(entry, self.cv, self.supervisor, self.operator, "Miscounted")
        self.assertEqual(self.seconds.on_hand_at(self.plant), Decimal("0"))


class SecondsApiTests(SecondsTestCase):
    def test_counted_at_the_station(self):
        from django.contrib.auth.models import Permission, User
        from rest_framework.test import APIClient

        device = User.objects.create_user("bcs")
        device.user_permissions.add(Permission.objects.get(codename="weigh_at_station"))
        client = APIClient()
        client.force_authenticate(device)
        base = f"/api/manufacturing/stations/{self.cv.code}/"
        client.post(base + "sign-in/", {"pin": self.operator.issue_pin()}, format="json")
        response = client.post(base + "run-seconds/", {"machine": "C-1", "pieces": "36",
                                                       "reason": "PRINT"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["item"], response.json()["pieces"]),
                         ("BAG-2ND", "36.0000"))
