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
