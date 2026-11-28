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


class AnUnregisteredWorkersOwnAddressTests(ChallanEwayBillTestCase):
    """
    O533, the challan's half. An unregistered worker in Karnataka, billed and
    shipped to at its one address, which gives no state: the challan recorded
    29 at issue, and the bill reads it (accounting.gst.state_delivered_in).
    50 kg at 95 = 4,750.00, across a state line.
    """

    def setUp(self):
        super().setUp()
        PartyTaxProfile.objects.filter(party=self.laminator).update(gstin="", gst_state="29")
        Address.objects.filter(party=self.laminator).delete()
        self.own = Address.objects.create(party=self.laminator, address_type=AddressType.BILLING,
                                          line1="KIADB Plot 7", city="Dharwad", state="",
                                          postal_code="580011", is_primary=True)

    def test_with_no_state_it_reads_the_state_the_challan_recorded(self):
        challan = self.challan("50")
        self.assertEqual((challan.job_worker_gstin, challan.job_worker_state), ("", "29"))
        payload = prepare(challan, vehicle_number="MH12AB1234").payload
        self.assertEqual((payload["toGstin"], payload["toStateCode"], payload["actToStateCode"],
                          payload["toPincode"], payload["totInvValue"]), ("URP", 29, 29, 580011, 4750.0))

    def test_another_site_with_no_state_is_still_refused(self):
        """A site that is not the address billed to is nobody's to guess."""
        Address.objects.create(party=self.laminator, address_type=AddressType.SHIPPING,
                               line1="Shed 9", city="Gadag", state="", postal_code="582101",
                               is_primary=True)
        with self.assertRaisesMessage(ValidationError, "says its state is nothing"):
            prepare(self.challan("50"), vehicle_number="MH12AB1234")


class AWorkersSiteInAnotherStateTests(ChallanEwayBillTestCase):
    """
    The auditor's case: a laminator registered in Karnataka (29) with works at Kolhapur, Maharashtra.
    50 kg at 95 = 4,750.00. It was refused: "is in Maharashtra, but the registration is in Karnataka".
    """

    def works(self, state):
        Address.objects.filter(party=self.laminator, address_type=AddressType.SHIPPING).delete()
        Address.objects.create(party=self.laminator, address_type=AddressType.BILLING, line1="KIADB Plot 7",
                               city="Dharwad", state="Karnataka", postal_code="580011", is_primary=True)
        return Address.objects.create(party=self.laminator, address_type=AddressType.SHIPPING, line1="Gat 12, Shiroli MIDC",
                                      city="Kolhapur", state=state, postal_code="416001", is_primary=True)

    def test_a_site_with_no_state_is_refused_rather_than_given_the_registrations(self):
        """It built state code 29 over PIN 416001, which the portal rejects."""
        self.works("")
        with self.assertRaisesMessage(ValidationError, "says its state is nothing"):
            prepare(self.challan("50"), vehicle_number="MH12AB1234")

    def test_the_bill_says_where_the_lorry_goes_and_whom_it_is_sent_to(self):
        self.works("Maharashtra")
        bill = prepare(self.challan("50"), vehicle_number="MH12AB1234")
        payload = bill.payload
        self.assertEqual((payload["toGstin"], payload["toStateCode"], payload["actToStateCode"], payload["toPincode"],
                          payload["toPlace"], payload["transactionType"], payload["totInvValue"]),
                         ("29AABCE5678F1ZD", 29, 27, 416001, "Kolhapur", 2, 4750.0))
        # The goods cross no state line; that the worker is registered across one is said.
        self.assertFalse(bill.required)
        self.assertIn("Worth 4750", bill.required_because)
        self.assertIn("registered in Karnataka and takes the goods in Maharashtra", bill.required_because)

    def test_works_across_the_line_still_need_a_bill_whatever_they_are_worth(self):
        bill = prepare(self.second, vehicle_number="MH12AB1234")
        self.assertTrue(bill.required)
        self.assertEqual((bill.payload["toStateCode"], bill.payload["actToStateCode"], bill.payload["transactionType"]),
                         (29, 29, 1))
