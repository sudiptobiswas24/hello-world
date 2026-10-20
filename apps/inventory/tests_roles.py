"""
The stores roles over the API, never as a superuser (audit2_stores S1, S2 and M4: O324, O330, O331).
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, UnitOfMeasure
from apps.core.tests_roles import RolesTestCase

from .adjustments import AdjustmentReason
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
        for account in (self.bank, self.payable, self.inventory, self.cogs):
            response = client.post("/api/inventory/adjustment-reasons/", {
                "code": f"R{account.code}", "name": "found", "account": account.pk, "direction": "increase"},
                format="json")
            self.assertEqual(response.status_code, 400, f"reason on {account.name}: {response.status_code}")
            self.assertIn("account", response.json())
        self.assertFalse(AdjustmentReason.objects.exists())

    def test_the_controller_points_a_reason_at_an_expense_but_never_at_a_control_account(self):
        client = self.as_("Controller")
        response = client.post("/api/inventory/adjustment-reasons/", {
            "code": "PAY", "name": "found", "account": self.payable.pk, "direction": "increase"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/inventory/adjustment-reasons/", {
            "code": "SH", "name": "shrink", "account": self.cogs.pk, "direction": "decrease"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        reason = response.json()["id"]
        # The Stores Manager keeps the reason's words and direction, not where it books.
        stores = self.as_("Stores Manager")
        self.assertEqual(stores.patch(f"/api/inventory/adjustment-reasons/{reason}/", {"name": "Shrinkage"},
                                      format="json").status_code, 200)
        other = Account.objects.create(code="5100", name="Write-off", account_type=AccountType.EXPENSE)
        self.assertEqual(stores.patch(f"/api/inventory/adjustment-reasons/{reason}/", {"account": other.pk},
                                      format="json").status_code, 400)
        self.assertEqual(AdjustmentReason.objects.get().account, self.cogs)

    def test_a_stores_manager_cannot_credit_the_payables_control_through_a_stock_adjustment(self):
        # A reason left on the payables from before the rule is refused when it would book.
        with self.assertRaises(ValidationError):
            AdjustmentReason.objects.create(code="AP", name="found", account=self.payable)
        reason = AdjustmentReason.objects.create(code="PAY", name="found", account=self.cogs, direction="increase")
        AdjustmentReason.objects.filter(pk=reason.pk).update(account=self.payable)
        client = self.as_("Stores Manager")
        adjustment = client.post("/api/inventory/stock-adjustments/", {
            "adjustment_date": "2026-03-01", "warehouse": self.store.pk, "reason": reason.pk}, format="json")
        self.assertEqual(adjustment.status_code, 201, adjustment.content)
        line = client.post("/api/inventory/stock-adjustment-lines/", {
            "adjustment": adjustment.json()["id"], "item": self.item.pk, "uom": self.kg.pk, "quantity": "4",
            "unit_cost": "12.5"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        posted = client.post(f"/api/inventory/stock-adjustments/{adjustment.json()['id']}/post/")
        credited = sum((line.credit - line.debit for line in JournalLine.objects.filter(account=self.payable)), D("0"))
        self.assertEqual(posted.status_code, 400, f"posted: payables credited {credited} with no vendor")
        self.assertEqual(credited, D("0"))

    def test_an_items_costing_method_cannot_change_under_stock(self):
        client = self.as_("Stores Manager")
        response = client.patch(f"/api/inventory/items/{self.item.pk}/", {"costing_method": "fifo"}, format="json")
        self.assertGreaterEqual(response.status_code, 400, f"changed costing under 10 kg: {response.status_code}")

    def test_warehouse_staff_do_not_read_what_stock_is_worth(self):
        for role in ("Warehouse Staff", "Quality Inspector"):
            client = self.as_(role)
            rows = client.get("/api/inventory/stock-movements/")
            self.assertEqual(rows.status_code, 200)
            row = rows.json()[0] if isinstance(rows.json(), list) else rows.json()["results"][0]
            self.assertEqual(row["quantity"], "10.0000")
            for field in ("unit_cost", "value", "value_adjustment"):
                self.assertNotIn(field, row, role)
            # The shelf's quantities, not its worth.
            on_hand = client.get("/api/inventory/stock-reports/valuation/").json()
            self.assertNotIn("total_value", on_hand, role)
            self.assertEqual([(r["item"], r["quantity"]) for r in on_hand["rows"]], [("T", "10.0000")])
            self.assertNotIn("value", on_hand["rows"][0], role)
            self.assertNotIn("unit_cost", on_hand["rows"][0], role)
            stock = client.get(f"/api/inventory/items/{self.item.pk}/stock/").json()
            self.assertNotIn("total_value", stock, role)
            self.assertNotIn("value", stock["rows"][0], role)
            self.assertNotIn("value", client.get("/api/inventory/stock-reports/negative/").json(), role)
            self.assertEqual(client.get("/api/inventory/stock-reports/reconciliation/").status_code, 403, role)
            self.assertEqual(client.get("/api/inventory/stock-reports/movements/",
                                        {"start": "2026-01-01", "end": "2026-12-31"}).status_code, 403, role)

    def test_the_stores_manager_reads_what_stock_is_worth(self):
        client = self.as_("Stores Manager")
        row = client.get("/api/inventory/stock-movements/").json()
        row = row[0] if isinstance(row, list) else row["results"][0]
        self.assertEqual(row["unit_cost"], "5.00000000")
        on_hand = client.get("/api/inventory/stock-reports/valuation/").json()
        self.assertEqual((on_hand["total_value"], on_hand["rows"][0]["value"]), ("50.00", "50.00"))
        self.assertEqual(client.get("/api/inventory/stock-reports/reconciliation/").status_code, 200)

