"""
The e-invoice and e-way bill screens' server side, asked as the people
who use them: the GST officer prepares a registration and finds it by its
invoice; a sales rep prepares none.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .tests_einvoice import EInvoiceTestCase


def as_(role):
    user = User.objects.create_user(role.replace(" ", "_").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class GstDocumentScreenTests(EInvoiceTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def test_the_officer_prepares_one_and_finds_it_by_its_invoice(self):
        invoice = self.s1()
        officer = as_("GST Officer")
        made = officer.post("/api/gst/e-invoices/", {"invoice": invoice.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = officer.get("/api/gst/e-invoices/", {"invoice": invoice.pk}).json()
        self.assertEqual((row["invoice_number"], row["irn"]), (invoice.number, ""))
        self.assertEqual(len(officer.get("/api/gst/e-invoices/", {"search": invoice.number}).json()), 1)
        bill = officer.post("/api/gst/eway-bills/", {"invoice": invoice.pk, "vehicle_number": "KA25AB1234",
                                                     "distance_km": 120}, format="json")
        self.assertEqual(bill.status_code, 201, bill.content)
        [found] = officer.get("/api/gst/eway-bills/", {"search": "KA25AB1234"}).json()
        self.assertEqual(found["distance_km"], 120)

    def test_a_rep_prepares_none(self):
        invoice = self.s1()
        response = as_("Sales Rep").post("/api/gst/e-invoices/", {"invoice": invoice.pk}, format="json")
        self.assertEqual(response.status_code, 403)


class NothingSentIsDroppedTests(EInvoiceTestCase):
    """
    O574: the e-invoice and e-way bill creates read request.data themselves and dropped a key they do not take,
    201 and without a word. Asked as the GST officer, each refuses it beside the key, and makes nothing.
    """

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def test_the_e_invoice_refuses_a_key_it_does_not_take(self):
        from .einvoice import EInvoice

        invoice = self.s1()
        officer = as_("GST Officer")
        # An IRN sent with the invoice is not recorded by preparing it: record/ keeps the portal's answer.
        refused = officer.post("/api/gst/e-invoices/", {"invoice": invoice.pk, "irn": "a" * 64}, format="json")
        self.assertEqual((refused.status_code, list(refused.json())), (400, ["irn"]), refused.content)
        self.assertEqual(refused.json()["irn"], ["This does not take `irn`, and would drop it without a word; it takes "
                                                 "`invoice`."])
        self.assertFalse(EInvoice.objects.exists())
        self.assertEqual(officer.post("/api/gst/e-invoices/", {"invoice": invoice.pk}, format="json").status_code, 201)

    def test_the_e_way_bill_refuses_a_key_it_does_not_take(self):
        from .ewaybill import EwayBill

        invoice = self.s1()
        officer = as_("GST Officer")
        # `vehicle` for `vehicle_number`: dropped, and the draft refused for want of the vehicle it was sent.
        refused = officer.post("/api/gst/eway-bills/", {"invoice": invoice.pk, "vehicle": "KA25AB1234",
                                                        "distance_km": 120}, format="json")
        self.assertEqual((refused.status_code, list(refused.json())), (400, ["vehicle"]), refused.content)
        self.assertIn("This does not take `vehicle`", refused.json()["vehicle"][0])
        self.assertIn("`vehicle_number`", refused.json()["vehicle"][0])
        self.assertFalse(EwayBill.objects.exists())
        made = officer.post("/api/gst/eway-bills/", {"invoice": invoice.pk, "vehicle_number": "KA25AB1234",
                                                     "distance_km": 120}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["vehicle_number"], "KA25AB1234")
