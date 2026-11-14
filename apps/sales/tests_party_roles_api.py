"""
A party's second trading role, given on its page: a vendor who also buys
from us made a customer, asked as a new customer is asked. A rep changes
only their own customers, so claims no vendor; a role a document names
the party in is not taken back.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import CustomerProfile
from .tests_base import SalesTestCase, carries_every_customer


class PartyRolesApiTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.supplier = Party.objects.create(code="V-9", name="Granule House")
        PartyRoleAssignment.objects.create(party=self.supplier, role=PartyRole.VENDOR)
        self.url = f"/api/core/parties/{self.supplier.pk}/roles/"

    def as_(self, role, name=None):
        user = User.objects.create_user(name or role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client, user

    def roles(self, party):
        return set(party.role_assignments.values_list("role", flat=True))

    def test_the_clerk_makes_a_vendor_a_customer_and_takes_it_back(self):
        clerk, _ = self.as_("Purchasing Clerk")
        made = clerk.post(self.url, {"role": "customer"}, format="json")
        self.assertEqual(made.status_code, 200, made.content)
        self.assertEqual(set(made.json()["roles"]), {"vendor", "customer"})
        again = clerk.post(self.url, {"role": "customer"}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("a customer already", again.content.decode())
        taken = clerk.delete(f"{self.url}?role=customer")
        self.assertEqual(taken.status_code, 200, taken.content)
        self.assertEqual(self.roles(self.supplier), {"vendor"})

    def test_not_an_employee_from_here(self):
        clerk, _ = self.as_("Purchasing Clerk")
        refused = clerk.post(self.url, {"role": "employee"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.roles(self.supplier), {"vendor"})

    def test_a_role_on_a_document_stays(self):
        self.make_order()
        url = f"/api/core/parties/{self.customer.pk}/roles/"
        refused = self.as_("Purchasing Clerk")[0].delete(f"{url}?role=customer")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("it stays a customer", refused.content.decode())
        self.assertIn("customer", self.roles(self.customer))

    def test_the_rep_neither_claims_a_vendor_nor_makes_one(self):
        # Made theirs, a vendor would be the rep's to change: its bank
        # account included.
        rep, user = self.as_("Sales Rep")
        carries_every_customer(user)
        claimed = rep.post(self.url, {"role": "customer"}, format="json")
        self.assertEqual(claimed.status_code, 400)
        self.assertIn("Only your own customers", claimed.content.decode())
        self.assertFalse(CustomerProfile.objects.filter(party=self.supplier).exists())
        own = rep.post(f"/api/core/parties/{self.customer.pk}/roles/", {"role": "vendor"}, format="json")
        self.assertEqual(own.status_code, 400)
        self.assertIn("A rep makes customers", own.content.decode())
        self.assertEqual(self.roles(self.customer), {"customer"})

    def test_the_bare_collection_is_read_only(self):
        clerk, _ = self.as_("Purchasing Clerk")
        written = clerk.post("/api/core/party-roles/", {"party": self.supplier.pk, "role": "employee"},
                             format="json")
        self.assertIn(written.status_code, (403, 405))
        self.assertEqual(self.roles(self.supplier), {"vendor"})
