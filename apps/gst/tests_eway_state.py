"""
The state an e-way bill says the goods are delivered in (O533).

One rule (accounting.gst.state_delivered_in) is read by the place of supply,
by posting and by the e-way bill: the address's own state; for the address
billed to with none, the buyer's principal state; for any other address with
none, nothing, and the document is refused while it can still be put right.
"""

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APIClient

from apps.accounting.gst import state_delivered_in
from apps.core.models import Address, AddressType
from apps.sales.models import Invoice, InvoiceLine

from .ewaybill import _invoice_payload as build_eway
from .einvoice import build as build_einvoice
from .tests import D, DAY, gstin
from .tests_einvoice import EInvoiceTestCase

EWAY_BILLS = "/api/gst/eway-bills/"


class TheStateTheGoodsAreDeliveredInTests(EInvoiceTestCase):
    def officer(self):
        call_command("setup_roles", verbosity=0)
        user = get_user_model().objects.create_user("gst-officer")
        user.groups.add(Group.objects.get(name="GST Officer"))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def goods(self):
        return [(self.sack, "1000", "125", "2", self.pair)]

    def test_the_billing_address_with_no_state_reads_the_registration_on_both_documents(self):
        """The auditor's case: the e-invoice built with Stcd 29 and the e-way bill refused "state is nothing"."""
        buyer = self.party("KAVERI", gstin=gstin("29AAFCK6627T1Z"))
        self.address(buyer, "8 APMC Yard", "Hubballi", "", "580030")
        invoice = self.invoice(buyer, self.goods())
        self.assertEqual(build_einvoice(invoice)["BuyerDtls"]["Stcd"], "29")
        answer = self.officer().post(EWAY_BILLS, {"invoice": invoice.pk, "distance_km": 560,
                                                  "vehicle_number": "KA25AB1234"}, format="json")
        self.assertEqual(answer.status_code, 201, answer.data)
        payload = answer.data["payload"]
        self.assertEqual((payload["toStateCode"], payload["actToStateCode"], payload["toPincode"]),
                         (29, 29, 580030))
        self.assertEqual(payload["totalValue"], 122500.0)

    def test_an_unregistered_buyer_billed_at_an_address_with_no_state_reads_the_state_on_its_record(self):
        buyer = self.party("KA-URP", gst_state="29")
        self.address(buyer, "2 Station Road", "Dharwad", "", "580001")
        payload, required, because = build_eway(self.invoice(buyer, self.goods()))
        self.assertEqual((payload["toGstin"], payload["toStateCode"], payload["actToStateCode"]), ("URP", 29, 29))

    def test_a_third_partys_site_with_no_state_is_refused_before_the_invoice_posts(self):
        """It posted, and then neither the e-way bill nor the e-invoice's ship-to could ever be built."""
        site = Address.objects.create(party=self.mh, address_type=AddressType.SHIPPING, line1="Gat 41",
                                      city="Sinnar", state="", postal_code="422103")
        invoice = self.invoice(self.ka, self.goods(), post=False)
        invoice.shipping_address = site
        with self.assertRaisesMessage(ValidationError, "the e-way bill and the e-invoice name the state"):
            invoice.save()
        Invoice.objects.filter(pk=invoice.pk).update(shipping_address=site)
        invoice.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "whose state, nothing, is no GST state"):
            invoice.post()
        self.assertFalse(Invoice.objects.get(pk=invoice.pk).posted)
        # Put right while it still can be, it posts and the bill names the state the lorry goes to.
        Address.objects.filter(pk=site.pk).update(state="Maharashtra")
        invoice.refresh_from_db()
        invoice.post()
        payload, required, because = build_eway(invoice)
        self.assertEqual((payload["toStateCode"], payload["actToStateCode"], payload["transactionType"]),
                         (29, 27, 2))

    def test_a_service_billed_with_such_a_ship_to_still_posts(self):
        """Nothing moves on it, so it needs no e-way bill and no state to deliver in."""
        site = Address.objects.create(party=self.mh, address_type=AddressType.SHIPPING, line1="Gat 41",
                                      city="Sinnar", state="", postal_code="422103")
        invoice = Invoice.objects.create(customer=self.ka, invoice_date=DAY, receivable_account=self.ar,
                                         currency=self.inr, shipping_address=site)
        line = InvoiceLine.objects.create(invoice=invoice, charge=self.freight6, quantity=D("1"),
                                          unit_price=D("3000"), revenue_account=self.revenue)
        line.taxes.set(self.pair)
        invoice.post()
        self.assertTrue(invoice.posted)

    def test_the_rule(self):
        billed = self.ka.billing_address()
        bare = Address.objects.create(party=self.ka, address_type=AddressType.SHIPPING, line1="Shed 4",
                                      city="Gadag", state="", postal_code="582101")
        Address.objects.filter(pk=billed.pk).update(state="")
        stateless = Address.objects.get(pk=billed.pk)
        self.assertEqual(state_delivered_in(billed, billed, "27"), "29")       # the address says
        self.assertEqual(state_delivered_in(stateless, stateless, "29"), "29")  # billed there: the principal state
        self.assertIsNone(state_delivered_in(bare, stateless, "29"))            # another site: never the GSTIN's
        self.assertIsNone(state_delivered_in(stateless, stateless, ""))
        self.assertIsNone(state_delivered_in(None, stateless, "29"))
