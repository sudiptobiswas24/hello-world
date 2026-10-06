"""
The floor records' screens, server side, asked as the people who read
them: a shipment's certificate, an agency's release, a rebatch. Each
list names what a row is about; quantities arrive as exact text.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .tests_certificates import CertificateTestCase
from .tests_rebatch import RebatchTestCase
from .tests_third_party import InspectedTestCase


def as_(role):
    user, _ = User.objects.get_or_create(username=role.replace(" ", "_").lower())
    user.groups.add(Group.objects.get(name=role))
    client = APIClient()
    client.force_authenticate(user)
    return client


class CertificateListTests(CertificateTestCase):
    def test_the_inspector_issues_one_and_the_list_names_the_shipment(self):
        call_command("setup_roles", verbosity=0)
        self.inspect(self.tape_plan, self.tape_lot, ["1000", "1010", "990"])  # passed: it may ship
        delivery = self.shipped()
        inspector = as_("Quality Inspector")
        made = inspector.post("/api/manufacturing/test-certificates/", {"delivery": delivery.pk}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = inspector.get("/api/manufacturing/test-certificates/", {"delivery": delivery.pk}).json()
        self.assertEqual((row["delivery_number"], row["customer_name"]),
                         (delivery.number, delivery.sales_order.customer.name))
        self.assertEqual(inspector.get("/api/manufacturing/test-certificates/",
                                       {"voided_at__isnull": "false"}).json(), [])


class ReleaseListTests(InspectedTestCase):
    def test_the_list_names_the_customer_agency_and_batches(self):
        call_command("setup_roles", verbosity=0)
        order = self.sale()
        self.release((self.b1, "500", "500"), order=order)
        [row] = as_("Quality Manager").get("/api/sales/third-party-releases/", {"customer": self.cement.pk}).json()
        self.assertEqual((row["customer_name"], row["agency_name"], row["order_number"], row["lines"][0]["lot_code"]),
                         (self.cement.name, "SGS India", order.number, self.b1.code))


class RebatchListTests(RebatchTestCase):
    def test_the_list_names_the_item_and_store(self):
        call_command("setup_roles", verbosity=0)
        document, _ = self.split()
        [row] = as_("Production Supervisor").get("/api/manufacturing/rebatches/", {"item": self.bag.pk}).json()
        self.assertEqual((row["id"], row["item_label"], row["warehouse_name"]),
                         (document.pk, f"{self.bag.sku} · {self.bag.name}", self.plant.name))
