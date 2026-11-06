"""
Typing what is written on a record finds it: "0412" the invoice OLD/0412,
"Shree" the customer, "Granule" the vendor, "WDG" the item, each at the
screen it lives on. A login that may not read invoices finds no invoice
by the same number; fewer than two characters find nothing; nobody
signed in finds nothing.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party
from apps.imports.importer import run
from apps.imports.tests import PARTIES, ImportTestCase
from apps.sales.models import Invoice


class SearchTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        for kind, text in (("parties", PARTIES),
                           ("open_invoices", "customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n")):
            report = run(kind, text, commit=True, against="3900")
            self.assertTrue(report.committed, report.errors)
        self.invoice = Invoice.objects.get(reference="OLD/0412")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def hits(self, client, typed):
        got = client.get("/api/web/search/", {"q": typed})
        self.assertEqual(got.status_code, 200, got.content)
        return [(hit["kind"], hit["label"], hit["href"]) for hit in got.json()]

    def test_each_kind_at_its_own_screen(self):
        controller = self.as_("Controller")
        self.assertEqual(self.hits(controller, "0412"),
                         [("Invoice", f"{self.invoice.number} · {self.invoice.customer.name}",
                           f"/sales/invoices/{self.invoice.pk}")])
        shree = Party.objects.get(code="C-100")
        granules = Party.objects.get(code="V-100")
        self.assertEqual(self.hits(controller, "Shree"),
                         [("Customer", "C-100 · Shree Cement", f"/sales/customers/{shree.pk}")])
        self.assertEqual(self.hits(controller, "granule"),
                         [("Vendor", "V-100 · Granule House", f"/purchasing/vendors/{granules.pk}")])
        self.assertEqual([hit[0] for hit in self.hits(controller, "WDG")], ["Item"])
        self.assertEqual(self.hits(controller, "0"), [])

    def test_only_what_the_login_may_read(self):
        stores = self.as_("Warehouse Staff")
        self.assertEqual([hit[0] for hit in self.hits(stores, "0412")], [])
        self.assertEqual([hit[0] for hit in self.hits(stores, "WDG")], ["Item"])
        nobody = APIClient()
        self.assertIn(nobody.get("/api/web/search/", {"q": "0412"}).status_code, (401, 403))
