"""
Deccan Cement's inspector (SGS) passes sacks before they are loaded.
Batch BG-...-01 holds 500 bags.

  Released 300 of it: 200 and 100 go; one more is 1 short.
  200 for order A and 100 for any order: A takes 250 (200 its own, 50
  of the shared), B takes 50, and B's next one is 1 short.
  Two releases of 400 and 100, 350 shipped: the 100 can be withdrawn,
  the 400 cannot.
  Released 500, shipped 200 and 300, the 200 comes back and goes out
  again: 700 against 500 is 200 short, unless the customer takes
  returns back under the release, when it is 500 against 500.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, MovementType, StockMovement
from apps.sales.models import (
    CustomerProfile,
    Delivery,
    DeliveryLine,
    SalesOrder,
    SalesOrderLine,
    ThirdPartyRelease,
    ThirdPartyReleaseLine,
)
from apps.sales.third_party import coverage

from .certificates import issue
from .tests_bales import BaleTestCase
from .tests_orders import TODAY


class InspectedTestCase(BaleTestCase):
    def setUp(self):
        super().setUp()
        self.profile = CustomerProfile.objects.create(party=self.cement,
                                                      third_party_inspection=True)
        self.sgs = Party.objects.create(code="SGS", name="SGS India")

    def sale(self, customer=None, bags="1000", item=None, **extra):
        order = SalesOrder.objects.create(customer=customer or self.cement, order_date=TODAY,
                                          **extra)
        SalesOrderLine.objects.create(order=order, item=item or self.bag, uom=self.pcs,
                                      warehouse=self.plant, quantity=Decimal(bags),
                                      unit_price=Decimal("12"))
        order.confirm()
        return order

    def release(self, *rows, order=None, reference="IC-1", customer=None, post=True):
        found = ThirdPartyRelease.objects.create(
            customer=customer or self.cement, agency=self.sgs, sales_order=order,
            their_reference=reference, inspected_on=TODAY)
        for lot, offered, released in rows:
            ThirdPartyReleaseLine.objects.create(release=found, lot=lot,
                                                 quantity_offered=Decimal(offered),
                                                 quantity_released=Decimal(released))
        if post:
            found.post()
        return found

    def ship(self, order, bags, lot=None):
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                    warehouse=self.plant, lot=lot or self.b1,
                                    quantity_shipped=Decimal(bags))
        delivery.post()
        return delivery


class HeldForTheInspectorTests(InspectedTestCase):
    def test_nothing_leaves_without_the_inspectors_word(self):
        with self.assertRaisesMessage(ValidationError, "is 100 short of what DCM - Deccan Cement's inspector"):
            self.ship(self.sale(), "100")

    def test_what_was_released_is_the_ceiling(self):
        self.release((self.b1, "500", "300"))
        order = self.sale()
        self.ship(order, "200")
        self.ship(order, "100")
        with self.assertRaisesMessage(ValidationError, "is 1 short"):
            self.ship(order, "1")

    def test_a_release_for_one_order_serves_that_order_first(self):
        first, second = self.sale(), self.sale()
        self.release((self.b1, "200", "200"), order=first, reference="IC-A")
        with self.assertRaisesMessage(ValidationError, "is 1 short"):
            self.ship(second, "1")
        self.release((self.b1, "100", "100"), reference="IC-ANY")
        self.ship(first, "250")
        self.ship(second, "50")
        with self.assertRaisesMessage(ValidationError, "is 1 short"):
            self.ship(second, "1")
        found = coverage(self.cement, self.b1)
        self.assertEqual((found["spare_for_any_order"], found["fits"]), (Decimal("0"), True))

    def test_only_this_customers_standing_releases_count(self):
        other = Party.objects.create(code="ACC", name="Other Cement")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        self.release((self.b1, "500", "500"), customer=other, reference="IC-O")
        self.release((self.b1, "500", "500"), reference="IC-D", post=False)
        self.release((self.b1, "500", "500"), reference="IC-V").void("Wrong batch")
        with self.assertRaisesMessage(ValidationError, "is 10 short"):
            self.ship(self.sale(), "10")


class TheOrderDecidesTests(InspectedTestCase):
    def test_takes_the_customers_answer_unless_it_gives_its_own(self):
        # Small orders: each confirmed one reserves its bags.
        self.assertTrue(self.sale(bags="10").third_party_inspection)
        self.ship(self.sale(bags="10", third_party_inspection=False), "10")
        CustomerProfile.objects.filter(pk=self.profile.pk).update(third_party_inspection=False)
        self.assertFalse(self.sale(bags="10").third_party_inspection)
        with self.assertRaisesMessage(ValidationError, "is 10 short"):
            self.ship(self.sale(bags="10", third_party_inspection=True), "10")

    def test_fixed_once_anything_has_shipped(self):
        order = self.sale()
        order.third_party_inspection = False
        order.save()
        self.ship(order, "10")
        order.third_party_inspection = True
        with self.assertRaisesMessage(ValidationError, "has shipped"):
            order.save()

    def test_goods_not_traced_to_a_batch_cannot_be_released(self):
        plain = Item.objects.create(sku="PLAIN", name="Plain sack", uom=self.pcs)
        StockMovement.objects.create(item=plain, warehouse=self.plant,
                                     movement_type=MovementType.RECEIPT, uom=self.pcs,
                                     quantity=Decimal("10"), unit_cost=Decimal("1"),
                                     occurred_at=timezone.now())
        uninspected = self.sale(item=plain, bags="5", third_party_inspection=False)
        delivery = Delivery.objects.create(sales_order=uninspected, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=uninspected.lines.get(),
                                    warehouse=self.plant, quantity_shipped=Decimal("5"))
        delivery.post()
        order = self.sale(item=plain, bags="5")
        delivery = Delivery.objects.create(sales_order=order, delivery_date=TODAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                    warehouse=self.plant, quantity_shipped=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "untraced to a batch"):
            delivery.post()


class WithdrawnTests(InspectedTestCase):
    def test_only_while_nothing_has_gone_on_its_word(self):
        big = self.release((self.b1, "400", "400"), reference="IC-400")
        small = self.release((self.b1, "100", "100"), reference="IC-100")
        self.ship(self.sale(), "350")
        small.void("Entered twice")
        with self.assertRaisesMessage(ValidationError, "shipped to DCM - Deccan Cement on this"):
            big.void("No")
        with self.assertRaisesMessage(ValidationError, "not a standing release"):
            small.void("Again")


class ReturnedSacksTests(InspectedTestCase):
    def round_trip(self):
        self.release((self.b1, "500", "500"))
        order = self.sale()
        first = self.ship(order, "200")
        self.ship(order, "300")
        # create_return() posts the return itself.
        return order, first.create_return(credit_invoices=False)

    def test_are_inspected_again_by_default(self):
        order, back = self.round_trip()
        self.assertFalse(Delivery.objects.get(pk=back.pk).returned_under_release)
        with self.assertRaisesMessage(ValidationError, "is 200 short"):
            self.ship(order, "200")
        self.release((self.b1, "200", "200"), reference="IC-2")
        self.ship(order, "200")

    def test_or_go_back_under_the_release_where_the_customer_says_so(self):
        CustomerProfile.objects.filter(pk=self.profile.pk).update(release_covers_returns=True)
        order, back = self.round_trip()
        self.ship(order, "200")
        # Recorded on the return: turning the setting off afterwards does
        # not make the 200 already re-shipped count twice.
        CustomerProfile.objects.filter(pk=self.profile.pk).update(release_covers_returns=False)
        found = coverage(self.cement, self.b1)
        self.assertEqual((found["spare_for_any_order"], found["fits"]), (Decimal("0"), True))


class WhatAReleaseMaySayTests(InspectedTestCase):
    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "501 offered and 500 on hand"):
            self.release((self.b1, "501", "500"))
        with self.assertRaisesMessage(ValidationError, "releases nothing"):
            self.release(reference="IC-E")
        with self.assertRaisesMessage(ValidationError, "certificate number"):
            self.release((self.b1, "10", "10"), reference="  ")
        other = Party.objects.create(code="ACC", name="Other Cement")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        with self.assertRaisesMessage(ValidationError, "is ACC - Other Cement's, not DCM - Deccan Cement's"):
            self.release((self.b1, "10", "10"), order=self.sale(customer=other),
                         reference="IC-X")
        self.release((self.b1, "10", "10"), reference="IC-SAME")
        with self.assertRaisesMessage(ValidationError, "is already entered"):
            self.release((self.b1, "10", "10"), reference="IC-SAME")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self.release((self.b1, "10", "11"), reference="IC-OVER")

    def test_its_own_agency_is_not_one(self):
        found = ThirdPartyRelease.objects.create(customer=self.cement, agency=self.cement,
                                                 their_reference="QA-1", inspected_on=TODAY)
        with self.assertRaisesMessage(ValidationError, "name the agency that signed"):
            found.post()

    def test_posted_is_posted(self):
        found = self.release((self.b1, "10", "10"))
        found.inspector = "Someone else"
        with self.assertRaisesMessage(ValidationError, "is posted. Void it"):
            found.save()
        line = found.lines.get()
        line.quantity_released = Decimal("5")
        with self.assertRaisesMessage(ValidationError, "its lines do not change"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "its lines do not change"):
            line.delete()
        with self.assertRaisesMessage(ValidationError, "is posted; void it"):
            found.delete()


class OnTheCertificateTests(InspectedTestCase):
    def test_the_agencys_word_beside_the_plants(self):
        found = self.release((self.b1, "500", "500"), reference="SGS/IC/0419")
        delivery = self.ship(self.sale(), "100")
        (batch,) = issue(delivery, TODAY).content["lines"][0]["batches"]
        self.assertEqual(batch["released_by"], [{
            "release": found.number, "agency": "SGS India", "certificate": "SGS/IC/0419",
            "inspected_on": "2026-06-01"}])


class ReleaseApiTests(InspectedTestCase):
    def test_recorded_consulted_and_held_once_used(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("qa"))
        response = client.post("/api/sales/third-party-releases/record/", {
            "customer": self.cement.pk, "agency": self.sgs.pk, "their_reference": "IC-9",
            "inspected_on": "2026-06-01",
            "lines": [{"lot": self.b1.pk, "quantity_offered": "500",
                       "quantity_released": "450"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        release = response.json()
        self.assertTrue(release["number"].startswith("TPR-"))
        self.ship(self.sale(), "100")
        body = client.get("/api/sales/third-party-releases/coverage/",
                          {"customer": self.cement.pk, "lot": self.b1.pk}).json()
        self.assertEqual((body["released_for_any_order"], body["spare_for_any_order"]),
                         ("450", "350"))
        response = client.post(f"/api/sales/third-party-releases/{release['id']}/void/",
                               {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/sales/third-party-releases/record/", {
            "customer": self.cement.pk, "agency": self.sgs.pk, "their_reference": "IC-10",
            "inspected_on": "2026-06-01",
            "lines": [{"lot": self.b1.pk, "quantity_offered": "5", "quantity_released": "6"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
