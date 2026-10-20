"""
Audit probe (audit2_stores): the stores roles over the API, never as a superuser.
"""

import datetime
from decimal import Decimal

from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, UnitOfMeasure
from apps.core.tests_roles import RolesTestCase

from .models import Item, MovementType, StockMovement, Warehouse

D = Decimal


class StoresRoles(RolesTestCase):
    def setUp(self):
        super().setUp()
        usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.inventory = Account.objects.create(code="1200", name="Inventory", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1000", name="Bank", account_type=AccountType.ASSET, is_money=True) \
            if hasattr(Account, "is_money") else Account.objects.create(code="1000", name="Bank", account_type=AccountType.ASSET)
        self.payable = Account.objects.create(code="2000", name="Payable", account_type=AccountType.LIABILITY)
        self.cogs = Account.objects.create(code="5000", name="COGS", account_type=AccountType.EXPENSE)
        Company.objects.create(name="Co", base_currency=usd, default_inventory_account=self.inventory,
                               default_cogs_account=self.cogs)
        self.kg = UnitOfMeasure.objects.create(code="kg", name="kg")
        self.item = Item.objects.create(sku="T", name="Tape", uom=self.kg)
        self.store = Warehouse.objects.create(code="S", name="Store")
        StockMovement.objects.create(item=self.item, warehouse=self.store, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=D("10"), unit_cost=D("5"), occurred_at=timezone.now())

    def test_a_stores_manager_cannot_point_a_reason_at_the_payables_or_the_bank(self):
        client = self.as_("Stores Manager")
        for account in (self.bank, self.payable, self.inventory):
            response = client.post("/api/inventory/adjustment-reasons/", {
                "code": f"R{account.code}", "name": "found", "account": account.pk, "direction": "increase"},
                format="json")
            self.assertGreaterEqual(response.status_code, 400, f"reason on {account.name}: {response.status_code}")

    def test_an_items_costing_method_cannot_change_under_stock(self):
        client = self.as_("Stores Manager")
        response = client.patch(f"/api/inventory/items/{self.item.pk}/", {"costing_method": "fifo"}, format="json")
        self.assertGreaterEqual(response.status_code, 400, f"changed costing under 10 kg: {response.status_code}")

    def test_warehouse_staff_do_not_read_what_stock_is_worth(self):
        client = self.as_("Warehouse Staff")
        rows = client.get("/api/inventory/stock-movements/")
        self.assertEqual(rows.status_code, 200)
        self.assertNotIn("unit_cost", rows.json()[0] if isinstance(rows.json(), list) else rows.json()["results"][0])

    def test_a_stores_manager_cannot_credit_the_payables_control_through_a_stock_adjustment(self):
        client = self.as_("Stores Manager")
        reason = client.post("/api/inventory/adjustment-reasons/", {
            "code": "PAY", "name": "found", "account": self.payable.pk, "direction": "increase"}, format="json")
        if reason.status_code >= 400:
            return
        adjustment = client.post("/api/inventory/stock-adjustments/", {
            "adjustment_date": "2026-03-01", "warehouse": self.store.pk, "reason": reason.json()["id"]}, format="json")
        client.post("/api/inventory/stock-adjustment-lines/", {
            "adjustment": adjustment.json()["id"], "item": self.item.pk, "uom": self.kg.pk, "quantity": "4",
            "unit_cost": "12.5"}, format="json")
        posted = client.post(f"/api/inventory/stock-adjustments/{adjustment.json()['id']}/post/")
        credited = sum((line.credit - line.debit for line in JournalLine.objects.filter(account=self.payable)), D("0"))
        self.assertGreaterEqual(posted.status_code, 400, f"posted: payables credited {credited} with no vendor")
