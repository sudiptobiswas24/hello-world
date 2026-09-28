"""
Two bundles of 500 bags off C-1 pressed into one bale: 1,000 bags,
nominally 1,000 x 110 g = 110.000 kg. A bundle of 400 off C-2 is left
for the next. The bale goes to Deccan Cement.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, Lot
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .bales import Bale, bales_holding, break_bale, load, pack, trace, unload
from .tests_bag_counts import ConversionTestCase
from .tests_orders import TODAY
from .trace import recall


class BaleTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.b1 = self.count().inspection.lot
        self.b2 = self.count().inspection.lot
        self.b3 = self.count("400", machine=self.c2, operator=self.other).inspection.lot
        self.cement = Party.objects.create(code="DCM", name="Deccan Cement")
        PartyRoleAssignment.objects.create(party=self.cement, role=PartyRole.CUSTOMER)

    def bale(self, *rows, **extra):
        return pack(self.plant, self.operator, rows or [(self.b1, 500), (self.b2, 500)],
                    on_date=TODAY, **extra)

    def delivery(self, bags="1000"):
        order = SalesOrder.objects.create(customer=self.cement, order_date=TODAY)
        SalesOrderLine.objects.create(order=order, item=self.bag, uom=self.pcs,
                                      warehouse=self.plant, quantity=Decimal(bags),
                                      unit_price=Decimal("12"))
        order.confirm()
        return Delivery.objects.create(sales_order=order, delivery_date=TODAY)


class PackingTests(BaleTestCase):
    def test_sealed_numbered_and_weighed_by_its_bags(self):
        bale = self.bale(gross_kg="111.4")
        self.assertTrue(bale.number.startswith("BL-"))
        self.assertEqual((bale.bags(), bale.nominal_kg(), bale.status()),
                         (Decimal("1000"), Decimal("110.000"), "sealed"))

    def test_a_bundle_goes_in_once(self):
        first = self.bale()
        with self.assertRaisesMessage(ValidationError, "has 0 bags on the shelf not already"):
            self.bale((self.b1, 100))
        break_bale(first, "Strap snapped")
        self.assertEqual(self.bale((self.b1, 100)).bags(), Decimal("100"))

    def test_only_released_bundles_of_one_sack_whole(self):
        self.b3.inspections.get().void("Scale out")
        with self.assertRaisesMessage(ValidationError, "has not been inspected"):
            self.bale((self.b3, 400))
        other = Lot.objects.create(item=Item.objects.create(sku="BAG-2", name="Other",
                                                            uom=self.pcs, tracking="lot"),
                                   code="OTHER-1")
        with self.assertRaisesMessage(ValidationError, "the same sack"):
            self.bale((self.b1, 100), (other, 1))
        with self.assertRaisesMessage(ValidationError, "counted whole"):
            self.bale((self.b1, "10.5"))
        with self.assertRaisesMessage(ValidationError, "holds nothing"):
            pack(self.plant, self.operator, [], on_date=TODAY)

    def test_sealed_is_sealed(self):
        bale = self.bale()
        line = bale.lines.first()
        line.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is sealed"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "is sealed; break it"):
            line.delete()
        with self.assertRaisesMessage(ValidationError, "is sealed; break it"):
            bale.delete()
        bale.gross_kg = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "is sealed. Break it"):
            bale.save()


class ShippingTests(BaleTestCase):
    def test_loaded_unloaded_and_shipped(self):
        bale, delivery = self.bale(), self.delivery()
        load(delivery, [bale])
        self.assertEqual((delivery.lines.count(), Bale.objects.get(pk=bale.pk).status()),
                         (2, "loaded"))
        with self.assertRaisesMessage(ValidationError, "unload it first"):
            break_bale(Bale.objects.get(pk=bale.pk), "No")
        with self.assertRaisesMessage(ValidationError, "is already on"):
            load(delivery, [bale])
        unload(bale)
        self.assertEqual(delivery.lines.count(), 0)
        load(delivery, [Bale.objects.get(pk=bale.pk)])
        delivery.post()
        bale = Bale.objects.get(pk=bale.pk)
        self.assertEqual(bale.status(), "shipped")
        self.assertEqual(self.b1.on_hand_at(self.plant), Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "which has shipped"):
            break_bale(bale, "No")
        with self.assertRaisesMessage(ValidationError, "a return brings it back"):
            unload(bale)

    def test_a_delivery_line_changed_under_it_is_said(self):
        bale, delivery = self.bale(), self.delivery()
        load(delivery, [bale])
        DeliveryLine.objects.filter(delivery=delivery, lot=self.b1).update(
            quantity_shipped=Decimal("499"))
        self.assertEqual(Bale.objects.get(pk=bale.pk).status(), "altered")

    def test_a_broken_bale_goes_nowhere(self):
        bale = self.bale()
        break_bale(bale, "Strap snapped")
        with self.assertRaisesMessage(ValidationError, "was broken"):
            load(self.delivery(), [bale])


class TraceTests(BaleTestCase):
    def test_from_its_number_back_to_the_machine_and_on_to_the_customer(self):
        bale, delivery = self.bale(), self.delivery()
        load(delivery, [bale])
        delivery.post()
        found = trace(Bale.objects.get(pk=bale.pk))
        self.assertEqual((found["status"], found["shipped_to"], found["delivery"]),
                         ("shipped", "Deccan Cement", delivery.number))
        first = found["bundles"][0]
        self.assertEqual((first["lot"], first["machine"], first["operator"], first["mean_grams"]),
                         (self.b1.code, "C-1", "Operator", Decimal("110.200")))

    def test_a_recall_names_the_bales_the_customer_can_see(self):
        bale, delivery = self.bale(), self.delivery()
        load(delivery, [bale])
        delivery.post()
        (row,) = recall(self.b1)["bales"]
        self.assertEqual((row["bale"], row["customer"]), (bale.number, "Deccan Cement"))
        self.assertEqual(bales_holding([self.b3]), [])

    def test_a_broken_bale_is_no_longer_named(self):
        bale = self.bale()
        self.assertEqual([row["bale"] for row in bales_holding([self.b1])], [bale.number])
        break_bale(bale, "Strap snapped")
        self.assertEqual(bales_holding([self.b1]), [])


class BaleApiTests(BaleTestCase):
    def test_packed_loaded_labelled_and_traced(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("dispatch"))
        response = client.post("/api/manufacturing/bales/pack/", {
            "warehouse": self.plant.pk, "packed_by": self.operator.pk, "packed_on": "2026-06-01",
            "lines": [{"lot": self.b1.pk, "quantity": 500}, {"lot": self.b2.pk, "quantity": 500}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        bale = response.json()
        self.assertEqual((bale["bags"], bale["nominal_kg"]), ("1000", "110.000"))
        page = client.get(f"/api/manufacturing/bales/{bale['id']}/label/").content.decode()
        self.assertIn(bale["number"], page)
        self.assertIn("1000 bags", page)
        delivery = self.delivery()
        response = client.post("/api/manufacturing/bales/load/",
                               {"delivery": delivery.pk, "bales": [bale["id"]]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()[0]["status"], "loaded")
        body = client.get(f"/api/manufacturing/bales/{bale['id']}/trace/").json()
        self.assertEqual([row["bags"] for row in body["bundles"]], ["500", "500"])
        response = client.post(f"/api/manufacturing/bales/{bale['id']}/break/",
                               {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
