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

from .adjustments import AdjustmentReason, StockAdjustment, StockAdjustmentLine
from .models import Item, MovementType, StockMovement, Warehouse

D = Decimal


class StoresFixture(RolesTestCase):
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


class StoresRoles(StoresFixture):
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
        self.assertEqual(response.status_code, 400, f"changed costing under 10 kg: {response.status_code}")
        self.item.refresh_from_db()
        self.assertEqual(self.item.costing_method, "average")
        # Before anything has moved, it is still the item's to choose.
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        response = client.patch(f"/api/inventory/items/{fresh.pk}/", {"costing_method": "fifo"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

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



class TheFloorReadsNoStockMoneyAnywhere(RolesTestCase):
    """
    O331 with the review: every stock endpoint the floor can read, on the demo plant with an adjustment carrying a
    write-up and a revaluation, answers without a money field for Warehouse Staff and Quality Inspectors; the
    Stores Manager still reads them. Crawls each list and the detail of its first row.
    """

    MONEY = {"unit_cost", "value", "value_adjustment", "total_value", "revaluation", "posted_value"}
    ROOTS = ("/api/inventory/", "/api/manufacturing/production-entries/")

    def keys(self, data):
        found = set()
        if isinstance(data, dict):
            for key, inner in data.items():
                found.add(key)
                found |= self.keys(inner)
        elif isinstance(data, list):
            for inner in data[:5]:
                found |= self.keys(inner)
        return found

    def crawl(self, client):
        lists = []
        root = client.get(self.ROOTS[0]).json()
        lists += [url.replace("http://testserver", "") for url in root.values() if isinstance(url, str)]
        lists += list(self.ROOTS[1:])
        seen, read = set(), 0
        for url in lists:
            response = client.get(url)
            if response.status_code != 200:
                continue
            read += 1
            data = response.json()
            rows = data if isinstance(data, list) else data.get("results", data) if isinstance(data, dict) else data
            seen |= {(url, key) for key in self.keys(rows) & self.MONEY}
            if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "id" in rows[0]:
                detail = client.get(f"{url.rstrip('/')}/{rows[0]['id']}/")
                if detail.status_code == 200:
                    seen |= {(url + "{id}", key) for key in self.keys(detail.json()) & self.MONEY}
        return seen, read

    def test_it(self):
        from io import StringIO

        from apps.web.tests_load_demo import load

        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())
        call_command_roles()
        held = StockMovement.objects.filter(item__costing_method="average", warehouse__requires_bins=False,
                                            quantity__gt=0).select_related("item", "warehouse").first()
        item, warehouse = held.item, held.warehouse
        income = Account.objects.filter(account_type=AccountType.INCOME).first()
        reason = AdjustmentReason.objects.create(code="CRAWL", name="found", account=income)
        adjustment = StockAdjustment.objects.create(adjustment_date=timezone.localdate(), warehouse=warehouse,
                                                    reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=item, uom=item.uom, quantity=D("2"),
                                           unit_cost=D("9"))
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=item, uom=item.uom, quantity=D("0"),
                                           revaluation=D("7"))
        adjustment.post()
        for role in ("Warehouse Staff", "Quality Inspector"):
            seen, read = self.crawl(self.as_(role))
            self.assertGreater(read, 5, role)
            self.assertEqual(sorted(seen), [], role)
        seen, _ = self.crawl(self.as_("Stores Manager"))
        self.assertIn(("/api/inventory/stock-adjustments/", "total_value"), seen)


def call_command_roles():
    from django.core.management import call_command

    call_command("setup_roles", verbosity=0)


class AnInventoryAccountIsAStockAccount(StoresFixture):
    """
    The review of O324, its mirror: a Stores Manager pointed a new item's inventory account at the payables (200)
    and posted a write-up of 4 at 12.50 through it, debiting payables 50.00 with no vendor. An inventory account
    is an asset account kept for stock, wherever one is set; choosing one needs the accounting permission.
    """

    def test_a_stores_manager_cannot_point_an_item_at_the_payables(self):
        from .adjustments import StockAdjustment as Adjustment

        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        stores = self.as_("Stores Manager")
        for account in (self.payable, self.bank, self.cogs, self.inventory):
            response = stores.patch(f"/api/inventory/items/{fresh.pk}/", {"inventory_account": account.pk},
                                    format="json")
            self.assertEqual(response.status_code, 400, f"{account.name}: {response.status_code}")
        fresh.refresh_from_db()
        self.assertIsNone(fresh.inventory_account)
        reason = AdjustmentReason.objects.create(code="SH", name="s", account=self.cogs)
        adjustment = Adjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store,
                                               reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=fresh, uom=self.kg, quantity=D("4"),
                                           unit_cost=D("12.5"))
        # One set before the rule is refused where it is used.
        Item.objects.filter(pk=fresh.pk).update(inventory_account=self.payable)
        self.assertEqual(stores.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/").status_code, 400)
        self.assertFalse(JournalLine.objects.filter(account=self.payable).exists())

    def test_the_controller_chooses_an_asset_account_kept_for_stock(self):
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        controller = self.as_("Controller", "Stores Manager")
        self.assertEqual(controller.patch(f"/api/inventory/items/{fresh.pk}/", {"inventory_account": self.payable.pk},
                                          format="json").status_code, 400)
        stock = Account.objects.create(code="1210", name="Stock, spares", account_type=AccountType.ASSET)
        self.assertEqual(controller.patch(f"/api/inventory/items/{fresh.pk}/", {"inventory_account": stock.pk},
                                          format="json").status_code, 200)

    def test_a_template_and_the_company_default_too(self):
        from apps.core.models import Company as TheCompany

        from .variants import ItemTemplate

        with self.assertRaises(ValidationError):
            ItemTemplate.objects.create(code="T", name="T", uom=self.kg, inventory_account=self.payable)
        stores = self.as_("Stores Manager")
        template = ItemTemplate.objects.create(code="T2", name="T2", uom=self.kg)
        self.assertEqual(stores.patch(f"/api/inventory/item-templates/{template.pk}/",
                                      {"inventory_account": self.inventory.pk}, format="json").status_code, 400)
        company = TheCompany.get()
        company.default_inventory_account = self.payable
        with self.assertRaises(ValidationError):
            company.save()


class ACostOfSalesAccountIsKeptForIt(StoresFixture):
    """
    O347 (decided): an item's cogs_account is asked the same rule as its inventory account. A Stores Manager
    could point it at the payables, and every delivery of the item debited them. It is an expense account kept
    for cost of sales, choosing one needs the accounting permission, and one set before the rule is refused
    with a sentence where it is used.
    """

    def test_a_stores_manager_cannot_choose_it_at_all(self):
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        stores = self.as_("Stores Manager")
        for account in (self.payable, self.bank, self.inventory, self.cogs):
            response = stores.patch(f"/api/inventory/items/{fresh.pk}/", {"cogs_account": account.pk}, format="json")
            self.assertEqual(response.status_code, 400, f"{account.name}: {response.status_code}")
        fresh.refresh_from_db()
        self.assertIsNone(fresh.cogs_account)

    def test_the_controller_chooses_an_expense_account_kept_for_cost_of_sales(self):
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        controller = self.as_("Controller", "Stores Manager")
        variance = Account.objects.create(code="5950", name="Price variance", account_type=AccountType.EXPENSE)
        from apps.core.models import Company as TheCompany

        TheCompany.objects.update(purchase_price_variance_account=variance)
        for account in (self.payable, self.inventory, variance):
            response = controller.patch(f"/api/inventory/items/{fresh.pk}/", {"cogs_account": account.pk},
                                        format="json")
            self.assertEqual(response.status_code, 400, f"{account.name}: {response.status_code}")
        spares = Account.objects.create(code="5110", name="Cost of sales, spares", account_type=AccountType.EXPENSE)
        self.assertEqual(controller.patch(f"/api/inventory/items/{fresh.pk}/", {"cogs_account": spares.pk},
                                          format="json").status_code, 200)

    def test_one_set_before_the_rule_is_refused_where_it_is_used(self):
        from .valuation import cogs_account_for

        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        Item.objects.filter(pk=fresh.pk).update(cogs_account=self.payable)
        fresh.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is a liability account. Cost of sales is booked to an "
                                                       "expense account kept for it"):
            cogs_account_for(fresh)

    def test_a_template_and_the_company_default_too(self):
        from apps.core.models import Company as TheCompany

        from .variants import ItemTemplate

        with self.assertRaises(ValidationError):
            ItemTemplate.objects.create(code="T", name="T", uom=self.kg, cogs_account=self.payable)
        stores = self.as_("Stores Manager")
        template = ItemTemplate.objects.create(code="T2", name="T2", uom=self.kg)
        self.assertEqual(stores.patch(f"/api/inventory/item-templates/{template.pk}/",
                                      {"cogs_account": self.cogs.pk}, format="json").status_code, 400)
        company = TheCompany.get()
        company.default_cogs_account = self.payable
        with self.assertRaises(ValidationError):
            company.save()


class ACostOfSalesAccountIsAskedWhenChosenTests(StoresFixture):
    """
    O347, decided on the review: the rule is asked when the account is chosen or changed, as
    AdjustmentReason.check_account_if_chosen. A company or an item already on an account it refuses still
    saves its other settings; posting refuses with a sentence saying what to change; the stock health check
    names every one of them first. The admin's form asks in clean(), so the refusal is a field error, not a 500.
    """

    def doubled(self):
        from apps.core.models import Company as TheCompany

        variance = Account.objects.create(code="5950", name="Price variance", account_type=AccountType.EXPENSE)
        TheCompany.objects.update(purchase_price_variance_account=variance, default_cogs_account=variance)
        return TheCompany.get(), variance

    def test_a_company_on_a_doubled_account_saves_its_other_settings(self):
        company, variance = self.doubled()
        company.name = "Renamed"
        company.save()
        company.default_cogs_account = self.payable
        with self.assertRaisesMessage(ValidationError, "is a liability account"):
            company.save()

    def test_an_item_on_a_refused_account_saves_its_other_settings_and_posting_says_what_to_change(self):
        from .valuation import cogs_account_for

        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        Item.objects.filter(pk=fresh.pk).update(cogs_account=self.payable)
        fresh.refresh_from_db()
        fresh.name = "Fresh, renamed"
        fresh.save()
        with self.assertRaisesMessage(ValidationError, "Change F - Fresh, renamed's cost-of-sales account to an "
                                                       "expense account kept for cost of sales"):
            cogs_account_for(fresh)
        fresh.cogs_account = self.bank
        with self.assertRaises(ValidationError):
            fresh.save()

    def test_posting_through_the_companys_doubled_default_names_the_default(self):
        from .valuation import cogs_account_for

        self.doubled()
        fresh = Item.objects.create(sku="G", name="Goods", uom=self.kg)
        with self.assertRaisesMessage(ValidationError, "Change the company's default cost-of-sales account"):
            cogs_account_for(fresh)

    def test_the_stock_health_check_names_each_one(self):
        from apps.web.health import _stock

        self.doubled()
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        Item.objects.filter(pk=fresh.pk).update(cogs_account=self.payable)
        finding = _stock(None)
        labels = [row["label"] for row in finding["rows"]]
        self.assertEqual(finding["ok"], False, finding)
        self.assertTrue(any(label.startswith("The company's default cogs account") for label in labels), labels)
        self.assertTrue(any(label.startswith("F's cogs account") for label in labels), labels)

    def test_the_admin_says_it_beside_the_field(self):
        from django.contrib import admin
        from django.contrib.auth.models import User
        from django.forms.models import model_to_dict
        from django.test import RequestFactory

        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("admin")
        form_class = admin.site._registry[Item].get_form(request, fresh, change=True)
        data = {key: ("" if value is None else value) for key, value in model_to_dict(fresh).items()
                if key in form_class.base_fields}
        data["cogs_account"] = self.payable.pk
        form = form_class(data, instance=fresh)
        self.assertFalse(form.is_valid())
        self.assertIn("cogs_account", form.errors)


class ATemplatesCostingMethodIsFrozenUnderItsVariantsStock(StoresFixture):
    """The review of O330: PATCH an item template to FIFO while a variant holds 10 kg: refused, not 200."""

    def test_it(self):
        from .variants import ItemTemplate

        template = ItemTemplate.objects.create(code="TT", name="T", uom=self.kg)
        empty = ItemTemplate.objects.create(code="TE", name="E", uom=self.kg)
        variant = Item.objects.create(sku="TT-1", name="T one", uom=self.kg, template=template)
        StockMovement.objects.create(item=variant, warehouse=self.store, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=D("10"), unit_cost=D("5"), occurred_at=timezone.now())
        stores = self.as_("Stores Manager")
        response = stores.patch(f"/api/inventory/item-templates/{template.pk}/", {"costing_method": "fifo"},
                                format="json")
        self.assertEqual(response.status_code, 400, response.content)
        template.refresh_from_db()
        self.assertEqual(template.costing_method, "average")
        self.assertEqual(stores.patch(f"/api/inventory/item-templates/{empty.pk}/", {"costing_method": "fifo"},
                                      format="json").status_code, 200)


class TheReviewsSmallerPoints(StoresFixture):
    """The review of fix_stores2, item 7 (8, 9, 10 in its report)."""

    def test_the_admin_says_a_control_account_beside_the_field(self):
        from django.contrib import admin

        from django.contrib.auth.models import User
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("admin")
        form_class = admin.site._registry[AdjustmentReason].get_form(request)
        form = form_class(data={"code": "PAY", "name": "found", "account": self.payable.pk, "direction": "both",
                                "is_active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("account", form.errors)

    def test_a_legacy_reason_on_a_control_account_can_be_renamed_and_still_books_nothing(self):
        reason = AdjustmentReason.objects.create(code="OLD", name="old", account=self.cogs)
        AdjustmentReason.objects.filter(pk=reason.pk).update(account=self.payable)
        stores = self.as_("Stores Manager")
        response = stores.patch(f"/api/inventory/adjustment-reasons/{reason.pk}/", {"name": "Retired",
                                                                                   "is_active": False},
                                format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(stores.patch(f"/api/inventory/adjustment-reasons/{reason.pk}/",
                                      {"account": self.inventory.pk}, format="json").status_code, 400)

    def test_an_empty_standard_shelf_is_found_at_its_standard(self):
        from .adjustments import StockAdjustment as Adjustment

        item = Item.objects.create(sku="STD", name="s", uom=self.kg, costing_method="standard",
                                   standard_cost=D("10.005"))
        income = Account.objects.create(code="4100", name="Found", account_type=AccountType.INCOME)
        reason = AdjustmentReason.objects.create(code="F", name="found", account=income)
        adjustment = Adjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.store,
                                               reason=reason)
        StockAdjustmentLine.objects.create(adjustment=adjustment, item=item, uom=self.kg, quantity=D("2"))
        adjustment.post()
        self.assertEqual(item.valuation_at(self.store), (D("2"), D("20.01")))

    def test_a_test_is_one_by_where_it_sits_in_the_repository(self):
        from . import models as inventory_models
        from .models import is_test_source

        here = inventory_models.__file__
        self.assertTrue(is_test_source(__file__))
        self.assertFalse(is_test_source(here))
        self.assertFalse(is_test_source("/srv/tests/erp/apps/inventory/models.py"))
        self.assertFalse(is_test_source("/opt/tests/plant/apps/inventory/adjustments.py"))
