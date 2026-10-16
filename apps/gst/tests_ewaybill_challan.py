"""
E-way bills for job-work challans: a Maharashtra plant sends fabric to a
laminator in Karnataka.

  400 kg at 95 = 38,000.00. Under the 50,000 limit, but across a state
  line to a job worker, so required whatever it is worth.
  50 kg (4,750.00) to a job worker inside Maharashtra: not required.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import PartyTaxProfile
from apps.core.models import Address, AddressType, Company

from .ewaybill import CancelReason, prepare
from .tests_itc04 import Itc04TestCase


class ChallanEwayBillTestCase(Itc04TestCase):
    def setUp(self):
        super().setUp()
        plant = Address.objects.create(line1="Plot 12, MIDC Chakan", city="Pune",
                                       state="Maharashtra", postal_code="410501")
        Company.objects.update(address=plant)
        Address.objects.create(party=self.laminator, address_type=AddressType.SHIPPING,
                               line1="KIADB Plot 7", city="Dharwad", state="Karnataka",
                               postal_code="580011", is_primary=True)


class ChallanPayloadTests(ChallanEwayBillTestCase):
    def test_across_a_state_line_it_is_required_whatever_it_is_worth(self):
        bill = prepare(self.second, vehicle_number="MH12AB1234")
        self.assertTrue(bill.required)
        self.assertIn("across a state line", bill.required_because)
        payload = bill.payload
        self.assertEqual((payload["subSupplyType"], payload["docType"], payload["docNo"],
                          payload["toGstin"], payload["toStateCode"], payload["toPincode"]),
                         ("4", "CHL", self.second.number, "29AABCE5678F1ZD", 29, 580011))
        (item,) = payload["itemList"]
        self.assertEqual((item["hsnCode"], item["quantity"], item["qtyUnit"],
                          item["taxableAmount"], item["igstRate"]),
                         (63053300, 400.0, "KGS", 38000.0, 0))
        self.assertEqual((payload["totalValue"], payload["totInvValue"]), (38000.0, 38000.0))

    def test_inside_the_state_only_above_the_limit(self):
        PartyTaxProfile.objects.filter(party=self.laminator).update(
            gstin="27AABCD1234E2Z7", gst_state="27")
        Address.objects.filter(party=self.laminator).update(state="Maharashtra",
                                                            postal_code="411001")
        bill = prepare(self.challan("50"), vehicle_number="MH12AB1234")
        self.assertFalse(bill.required)
        self.assertIn("Worth 4750", bill.required_because)

    def test_the_vehicle_on_the_challan_is_the_one_on_the_bill(self):
        challan = self.challan("50", post=False)
        challan.vehicle = "MH 12 AB 1234"
        challan.save()
        challan.post()
        self.assertEqual(prepare(challan).vehicle_number, "MH12AB1234")
        with self.assertRaisesMessage(ValidationError, "not MH14ZZ9999"):
            prepare(challan, vehicle_number="MH14ZZ9999")

    def test_a_withdrawn_challan_moves_nothing(self):
        self.second.void()
        with self.assertRaisesMessage(ValidationError, "not an issued challan"):
            prepare(self.second, vehicle_number="MH12AB1234")


class ChallanVoidTests(ChallanEwayBillTestCase):
    def test_a_challan_on_the_road_is_not_withdrawn_from_under_its_bill(self):
        bill = prepare(self.second, vehicle_number="MH12AB1234")
        with self.assertRaisesMessage(ValidationError, "(not yet generated) standing"):
            self.second.void()
        bill.record("331000000002", timezone.now())
        with self.assertRaisesMessage(ValidationError, "331000000002 standing"):
            self.second.void()
        bill.cancel(CancelReason.ORDER_CANCELLED)
        self.second.void()
        self.second.refresh_from_db()
        self.assertIsNotNone(self.second.voided_at)
        self.assertEqual(Decimal(str(bill.payload["totalValue"])), Decimal("38000"))
