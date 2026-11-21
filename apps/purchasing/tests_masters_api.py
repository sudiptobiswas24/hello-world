"""
The purchasing masters through the API, as the people who keep them: the
AP manager agrees vendor prices and sets budgets and the approval policy;
the buyer reads the prices and keeps the reorder rules.

Each of these was read by purchasing and planning and kept only in the
admin. Two rules came with the screens: one vendor is preferred for an
item, and one approval policy is in force, sales and purchasing alike.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Warehouse
from apps.sales.models import ApprovalPolicy

from .models import PurchaseApprovalPolicy, VendorPrice
from .tests_budgets import BudgetTestCase


class MastersTestCase(BudgetTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def second_vendor(self):
        party = Party.objects.create(code="V-2", name="Second Polymers")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        return party


class VendorPriceTests(MastersTestCase):
    def test_the_ap_manager_agrees_a_price_and_the_buyer_reads_it(self):
        manager = self.as_("AP Manager")
        made = manager.post("/api/purchasing/vendor-prices/", {
            "vendor": self.vendor.pk, "item": self.item.pk, "unit_price": "102.50",
            "min_quantity": "1000", "valid_from": "2026-06-01"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        buyer = self.as_("Purchasing Clerk")
        [row] = buyer.get("/api/purchasing/vendor-prices/", {"item": self.item.pk}).json()
        self.assertEqual((row["vendor_name"], row["unit_price"]), (self.vendor.name, "102.50"))
        refused = buyer.post("/api/purchasing/vendor-prices/", {
            "vendor": self.vendor.pk, "item": self.item.pk, "unit_price": "90"}, format="json")
        self.assertEqual(refused.status_code, 403)

    def test_preferring_another_vendor_hands_it_over(self):
        first = VendorPrice.objects.create(vendor=self.vendor, item=self.item, unit_price=Decimal("100"),
                                           is_preferred=True)
        first_break = VendorPrice.objects.create(vendor=self.vendor, item=self.item, unit_price=Decimal("95"),
                                                 min_quantity=Decimal("500"), is_preferred=True)
        other = self.second_vendor()
        made = self.as_("AP Manager").post("/api/purchasing/vendor-prices/", {
            "vendor": other.pk, "item": self.item.pk, "unit_price": "98", "is_preferred": True}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        first.refresh_from_db()
        first_break.refresh_from_db()
        self.assertEqual((first.is_preferred, first_break.is_preferred), (False, False))
        from .pricing import preferred_vendor

        self.assertEqual(preferred_vendor(self.item), other)

    def test_one_vendors_breaks_are_preferred_together(self):
        VendorPrice.objects.create(vendor=self.vendor, item=self.item, unit_price=Decimal("100"),
                                   is_preferred=True)
        second = VendorPrice.objects.create(vendor=self.vendor, item=self.item, unit_price=Decimal("95"),
                                            min_quantity=Decimal("500"), is_preferred=True)
        self.assertEqual(VendorPrice.objects.filter(is_preferred=True).count(), 2)
        self.assertTrue(second.is_preferred)


class ReorderRuleTests(MastersTestCase):
    def test_the_buyer_keeps_the_rules_and_is_told_a_target_below_the_minimum(self):
        plant = Warehouse.objects.create(code="PLT", name="Plant")
        buyer = self.as_("Purchasing Clerk")
        made = buyer.post("/api/purchasing/reorder-rules/", {
            "item": self.item.pk, "warehouse": plant.pk, "minimum": "1000", "target": "5000"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        [row] = buyer.get("/api/purchasing/reorder-rules/", {"warehouse": plant.pk}).json()
        self.assertEqual(row["warehouse_name"], "Plant")
        refused = buyer.patch(f"/api/purchasing/reorder-rules/{row['id']}/", {"target": "500"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("target cannot be below the minimum", refused.content.decode())


class BudgetTests(MastersTestCase):
    def test_its_figures_are_read_from_the_orders_each_time(self):
        self.order_of("100", "20")
        manager = self.as_("AP Manager")
        figures = manager.get(f"/api/purchasing/budgets/{self.budget.pk}/figures/").json()
        self.assertEqual((figures["amount"], figures["committed"], figures["spent"], figures["available"]),
                         ("10000.00", "2000.00", "0", "8000.00"))

    def test_the_buyer_reads_a_budget_and_does_not_set_one(self):
        buyer = self.as_("Purchasing Clerk")
        self.assertEqual(len(buyer.get("/api/purchasing/budgets/").json()), 1)
        refused = buyer.patch(f"/api/purchasing/budgets/{self.budget.pk}/", {"amount": "99999"}, format="json")
        self.assertEqual(refused.status_code, 403)


class ApprovalPolicyTests(MastersTestCase):
    def test_one_policy_is_in_force_and_its_tiers_name_a_role(self):
        manager = self.as_("AP Manager")
        first = manager.post("/api/purchasing/approval-policies/", {"code": "P1", "name": "First",
                                                                     "max_order_value": "50000"}, format="json")
        second = manager.post("/api/purchasing/approval-policies/", {"code": "P2", "name": "Second",
                                                                      "max_order_value": "100000"}, format="json")
        self.assertEqual((first.status_code, second.status_code), (201, 201), second.content)
        self.assertEqual(PurchaseApprovalPolicy.active().code, "P2")
        self.assertFalse(PurchaseApprovalPolicy.objects.get(code="P1").is_active)
        roles = {row["name"]: row["id"] for row in manager.get("/api/core/roles/").json()}
        tier = manager.post("/api/purchasing/approval-tiers/", {
            "policy": second.json()["id"], "group": roles["Controller"], "up_to_amount": "500000"}, format="json")
        self.assertEqual(tier.status_code, 201, tier.content)
        [row] = manager.get(f"/api/purchasing/approval-policies/{second.json()['id']}/").json()["tiers"]
        self.assertEqual(row["group_name"], "Controller")

    def test_the_buyer_reads_the_policy_and_not_the_roles(self):
        buyer = self.as_("Purchasing Clerk")
        self.assertEqual(buyer.get("/api/purchasing/approval-policies/").status_code, 200)
        self.assertEqual(buyer.get("/api/core/roles/").status_code, 403)

    def test_the_sales_policy_too(self):
        ApprovalPolicy.objects.create(code="S1", name="First")
        ApprovalPolicy.objects.create(code="S2", name="Second")
        self.assertEqual(ApprovalPolicy.active().code, "S2")
        self.assertEqual(ApprovalPolicy.objects.filter(is_active=True).count(), 1)
