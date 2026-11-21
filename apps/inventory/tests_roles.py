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

    def test_the_controller_s_opening_reason_follows_the_books_and_a_stores_manager_cannot_tick_it(self):
        """O542 through the API: the reason follows the account the Controller opens the books against."""
        seeded = Account.objects.create(code="3900", name="Opening balances", account_type=AccountType.EQUITY)
        capital = Account.objects.create(code="3100", name="Capital", account_type=AccountType.EQUITY)
        controller = self.as_("Controller")
        made = controller.post("/api/inventory/adjustment-reasons/", {
            "code": "OPENING", "name": "Opening stock", "account": seeded.pk, "direction": "increase",
            "follows_books_opening": True}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        reason = made.json()["id"]
        opened = controller.post("/api/accounting/books-opening/", {
            "opened_on": "2026-10-01", "opening_account": capital.pk}, format="json")
        self.assertEqual(opened.status_code, 201, opened.content)
        self.assertEqual(controller.get(f"/api/inventory/adjustment-reasons/{reason}/").json()["account"], capital.pk)
        changed = controller.patch(f"/api/accounting/books-opening/{opened.json()['id']}/",
                                   {"opening_account": seeded.pk}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(AdjustmentReason.objects.get(pk=reason).account, seeded)
        # Another account on a reason that follows is refused beside the account, naming the books'.
        other = controller.patch(f"/api/inventory/adjustment-reasons/{reason}/", {"account": capital.pk}, format="json")
        self.assertEqual(other.status_code, 400, other.content)
        self.assertIn("which is 3900 Opening balances", other.json()["account"][0])
        # Ticking moves where a reason books, so it is not the Stores Manager's.
        shrink = AdjustmentReason.objects.create(code="SH", name="Shrink", account=self.cogs)
        stores = self.as_("Stores Manager")
        ticked = stores.patch(f"/api/inventory/adjustment-reasons/{shrink.pk}/", {"follows_books_opening": True},
                              format="json")
        self.assertEqual((ticked.status_code, list(ticked.json())), (400, ["follows_books_opening"]))
        self.assertEqual(AdjustmentReason.objects.get(pk=shrink.pk).account, self.cogs)
        self.assertEqual(stores.patch(f"/api/inventory/adjustment-reasons/{reason}/", {"name": "Opening"},
                                      format="json").status_code, 200)

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

    def test_an_items_tracking_cannot_change_once_its_stock_has_moved(self):
        """
        L5, as the Stores Manager: lot tracking switched on over 10 kg held stranded them (every movement
        after names a batch, and they are in none); switched off, the batches are lost. Refused both ways,
        beside nothing else changed; an item nothing has moved is still free, and so is every other field.
        """
        from .tracking import Lot

        client = self.as_("Stores Manager")
        response = client.patch(f"/api/inventory/items/{self.item.pk}/", {"tracking": "lot"}, format="json")
        self.assertEqual(response.status_code, 400, f"tracked by lot over 10 kg in no batch: {response.status_code}")
        self.assertEqual(response.json(), [
            "T has stock movements counted and valued as it stands, so its tracking cannot change. Make a new "
            "item and move the stock to it. Stock already moved is in no batch if it was not tracked, and in "
            "batches every later movement would drop if it was; the new item is made tracked as you mean, and "
            "the stock goes to it by a stock adjustment off this item and on to that one, each batch named."])
        self.item.refresh_from_db()
        self.assertEqual(self.item.tracking, "none")
        # The edit path: what is not frozen is still the item's to change.
        self.assertEqual(client.patch(f"/api/inventory/items/{self.item.pk}/", {"name": "Tape, 900 denier"},
                                      format="json").status_code, 200)
        # The reverse: a tracked item with a batch on the shelf is not untracked, nor made serial.
        batched = Item.objects.create(sku="B", name="Batched", uom=self.kg, tracking="lot")
        StockMovement.objects.create(item=batched, warehouse=self.store, movement_type=MovementType.RECEIPT,
                                     uom=self.kg, quantity=D("4"), unit_cost=D("5"), occurred_at=timezone.now(),
                                     lot=Lot.objects.create(item=batched, code="B-1"))
        for other in ("none", "serial"):
            response = client.patch(f"/api/inventory/items/{batched.pk}/", {"tracking": other}, format="json")
            self.assertEqual(response.status_code, 400, f"{other} over a batch of 4 kg: {response.status_code}")
        batched.refresh_from_db()
        self.assertEqual(batched.tracking, "lot")
        # Gone from the shelf is still moved: the 10 kg issued, the item stays as its movements were written.
        StockMovement.objects.create(item=self.item, warehouse=self.store, movement_type=MovementType.ISSUE,
                                     uom=self.kg, quantity=D("-10"), occurred_at=timezone.now())
        self.assertEqual(client.patch(f"/api/inventory/items/{self.item.pk}/", {"tracking": "lot"},
                                      format="json").status_code, 400)
        # Before anything has moved, it is still the item's to choose; and the model refuses whoever asks.
        fresh = Item.objects.create(sku="F", name="Fresh", uom=self.kg)
        self.assertEqual(client.patch(f"/api/inventory/items/{fresh.pk}/", {"tracking": "lot"},
                                      format="json").status_code, 200)
        self.item.tracking = "lot"
        with self.assertRaisesMessage(ValidationError, "so its tracking cannot change"):
            self.item.save()

    def test_the_admin_is_told_in_words_that_tracking_is_fixed(self):
        """L5 in the admin: the guard is save()'s, and the admin says it, not a server error."""
        from django.contrib.auth.models import User
        from django.test import Client

        from apps.core.tests_second_factor import staff_session

        admin = Client()
        staff_session(admin, User.objects.create_superuser("root", "root@example.com", "x"))
        url = f"/admin/inventory/item/{self.item.pk}/change/"
        page = admin.get(url)
        self.assertEqual(page.status_code, 200)
        form = page.context["adminform"].form
        data = {name: ("" if form[name].value() is None else form[name].value()) for name in form.fields}
        data = {name: value for name, value in data.items() if value is not False}
        for formset in page.context["inline_admin_formsets"]:
            prefix = formset.formset.prefix
            data.update({f"{prefix}-TOTAL_FORMS": 0, f"{prefix}-INITIAL_FORMS": 0})
        data["tracking"] = "lot"
        response = admin.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("so its tracking cannot change", response.content.decode())
        self.item.refresh_from_db()
        self.assertEqual(self.item.tracking, "none")
        # The costing method was refused by save() alone, and the admin answered a server error (O330's guard).
        data.update(tracking="none", costing_method="fifo")
        response = admin.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("so its costing method cannot change", response.content.decode())
        # What is not fixed still saves from the admin.
        data.update(costing_method="average", name="Tape, 900 denier")
        self.assertEqual(admin.post(url, data, follow=True).status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual((self.item.name, self.item.costing_method), ("Tape, 900 denier", "average"))

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
        # An item kept without batches: this adjustment names none (the demo's copolymer is kept by lot, L28).
        held = StockMovement.objects.filter(item__costing_method="average", warehouse__requires_bins=False,
                                            item__tracking="none",
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
        # And its tracking (L5): the variant's 10 kg are in no batch.
        response = stores.patch(f"/api/inventory/item-templates/{template.pk}/", {"tracking": "lot"}, format="json")
        self.assertEqual((response.status_code, list(response.json())), (400, ["tracking"]), response.content)
        template.refresh_from_db()
        self.assertEqual(template.tracking, "none")
        self.assertEqual(stores.patch(f"/api/inventory/item-templates/{empty.pk}/", {"tracking": "lot"},
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
