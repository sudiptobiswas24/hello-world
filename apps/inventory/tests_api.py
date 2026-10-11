"""
The API for stock.

Warehouses, items and raw movements were exposed. Adjustments, counts,
transfers, lots, bins and reservations were not — all built, tested, and
reachable only from a Python shell.

These tests are about reachability and about refusals arriving as
answers rather than as crashes. The domain behaviour has its own tests;
what is checked here is that the door exists and that walking into it
produces a sentence.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Currency, UnitOfMeasure, UnitOfMeasureCategory

from .models import (
    AdjustmentReason,
    Item,
    Lot,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    StorageBin,
    TrackingMode,
    Warehouse,
)


class ApiTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.shrinkage = acc("6200", "Shrinkage", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, default_cogs_account=self.cogs,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )
        user = get_user_model().objects.create_superuser(
            username="stock", email="s@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def stock(self, quantity="100", cost="5", item=None, warehouse=None, lot=None):
        target = item or self.item
        return StockMovement.objects.create(
            item=target, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=target.uom, lot=lot,
            quantity=Decimal(quantity), unit_cost=Decimal(cost),
            occurred_at=timezone.now(),
        )


class ReachabilityTests(ApiTestCase):
    def test_every_document_has_a_route(self):
        for path in (
            "/api/inventory/warehouses/",
            "/api/inventory/items/",
            "/api/inventory/stock-movements/",
            "/api/inventory/lots/",
            "/api/inventory/bins/",
            "/api/inventory/adjustment-reasons/",
            "/api/inventory/stock-adjustments/",
            "/api/inventory/stock-counts/",
            "/api/inventory/stock-transfers/",
            "/api/inventory/stock-reservations/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_a_warehouse_exposes_what_decides_how_it_behaves(self):
        # Quarantine, transit, consignment and binning all changed what
        # stock does here and none of them were reachable.
        response = self.client.get(f"/api/inventory/warehouses/{self.north.pk}/")
        for field in ("is_quarantine", "is_transit", "requires_bins",
                      "allow_negative_stock", "consignment_vendor"):
            self.assertIn(field, response.data)

    def test_an_item_exposes_its_tracking_and_costing(self):
        response = self.client.get(f"/api/inventory/items/{self.item.pk}/")
        for field in ("tracking", "costing_method", "standard_cost"):
            self.assertIn(field, response.data)

    def test_a_movement_exposes_its_unit_lot_and_bin(self):
        self.stock()
        response = self.client.get("/api/inventory/stock-movements/")
        row = response.data["results"][0] if "results" in response.data else response.data[0]
        for field in ("uom", "lot", "bin", "document_quantity", "unit_cost"):
            self.assertIn(field, row)


class AdjustmentApiTests(ApiTestCase):
    def draft(self):
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 4, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.item, uom=self.each,
            quantity=Decimal("-10"),
        )
        return adjustment

    def test_posting_is_an_action(self):
        self.stock()
        adjustment = self.draft()
        response = self.client.post(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["posted"])
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("90"))

    def test_voiding_is_an_action(self):
        self.stock()
        adjustment = self.draft()
        self.client.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json")
        response = self.client.post(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/void/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["voided"])
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))

    def test_a_refusal_arrives_as_an_answer_not_a_crash(self):
        # Without the exception handler this was a 500 and a stack trace
        # instead of the sentence the model wrote.
        self.stock("5")
        adjustment = self.draft()
        response = self.client.post(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("on hand", str(response.data))

    def test_posting_twice_is_refused_in_words(self):
        self.stock()
        adjustment = self.draft()
        self.client.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json")
        response = self.client.post(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already posted", str(response.data))

    def test_a_posted_adjustment_cannot_be_patched(self):
        self.stock()
        adjustment = self.draft()
        self.client.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json")
        response = self.client.patch(
            f"/api/inventory/stock-adjustments/{adjustment.pk}/",
            {"memo": "changed"}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_line_cannot_be_written_onto_a_posted_adjustment(self):
        self.stock()
        adjustment = self.draft()
        self.client.post(f"/api/inventory/stock-adjustments/{adjustment.pk}/post/", {}, format="json")
        response = self.client.post(
            "/api/inventory/stock-adjustment-lines/",
            {
                "adjustment": adjustment.pk, "item": self.item.pk,
                "uom": self.each.pk, "quantity": "-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class CountApiTests(ApiTestCase):
    def sheet(self):
        return StockCount.objects.create(
            count_date=datetime.date(2026, 4, 1), warehouse=self.north,
            reason=self.reason,
        )

    def test_adding_a_line_freezes_what_the_books_say(self):
        # It cannot be a plain POST of a line: the caller would supply
        # the system quantity, which is the one number they must not.
        self.stock("100")
        count = self.sheet()
        response = self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/add/",
            {"item": self.item.pk, "counted_quantity": "96"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["system_quantity"]), Decimal("100"))
        self.assertEqual(Decimal(response.data["variance"]), Decimal("-4"))

    def test_posting_returns_the_adjustment_it_raised(self):
        self.stock("100")
        count = self.sheet()
        self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/add/",
            {"item": self.item.pk, "counted_quantity": "96"}, format="json",
        )
        response = self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["count"]["posted"])
        self.assertIsNotNone(response.data["adjustment"])
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("96"))

    def test_a_count_that_agrees_raises_nothing_and_says_so(self):
        self.stock("100")
        count = self.sheet()
        self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/add/",
            {"item": self.item.pk, "counted_quantity": "100"}, format="json",
        )
        response = self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/post/", {}, format="json"
        )
        self.assertIsNone(response.data["adjustment"])
        self.assertTrue(response.data["count"]["posted"])

    def test_a_counted_quantity_is_required(self):
        count = self.sheet()
        response = self.client.post(
            f"/api/inventory/stock-counts/{count.pk}/add/",
            {"item": self.item.pk}, format="json",
        )
        self.assertEqual(response.status_code, 400)


class TransferApiTests(ApiTestCase):
    def draft(self, transit=False, quantity="30"):
        transit_warehouse = None
        if transit:
            transit_warehouse = Warehouse.objects.create(
                code="T", name="Road", is_transit=True
            )
        transfer = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 4, 1),
            from_warehouse=self.north, to_warehouse=self.south,
            transit_warehouse=transit_warehouse,
        )
        StockTransferLine.objects.create(
            transfer=transfer, item=self.item, uom=self.each,
            quantity=Decimal(quantity),
        )
        return transfer

    def test_a_direct_transfer_posts(self):
        self.stock()
        transfer = self.draft()
        response = self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("30"))

    def test_dispatch_and_receive_are_separate_actions(self):
        self.stock()
        transfer = self.draft(transit=True)
        self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/dispatch/", {}, format="json"
        )
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("0"))
        response = self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/receive/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("30"))

    def test_a_lorry_that_arrives_short_is_a_partial_receipt(self):
        self.stock()
        transfer = self.draft(transit=True)
        self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/dispatch/", {}, format="json"
        )
        line = transfer.lines.get()
        response = self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/receive/",
            {"quantities": {str(line.pk): "18"}}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.item.on_hand_at(self.south), Decimal("18"))
        self.assertEqual(response.data["status"], "in_transit")

    def test_cancelling_is_an_action(self):
        self.stock()
        transfer = self.draft()
        self.client.post(f"/api/inventory/stock-transfers/{transfer.pk}/post/", {}, format="json")
        response = self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.item.on_hand_at(self.north), Decimal("100"))

    def test_moving_more_than_is_there_is_refused_in_words(self):
        self.stock("10")
        transfer = self.draft()
        response = self.client.post(
            f"/api/inventory/stock-transfers/{transfer.pk}/post/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot move", str(response.data))


class LotApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tracked = Item.objects.create(
            sku="B", name="Vaccine", uom=self.each, tracking=TrackingMode.LOT
        )

    def test_a_batch_can_say_where_it_has_been(self):
        lot = Lot.objects.create(
            item=self.tracked, code="L1", expires_on=datetime.date(2026, 12, 1)
        )
        self.stock("50", item=self.tracked, lot=lot)
        response = self.client.get(f"/api/inventory/lots/{lot.pk}/trail/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["warehouse"], "N")

    def test_what_is_going_off_can_be_asked_for(self):
        lot = Lot.objects.create(
            item=self.tracked, code="OLD", expires_on=datetime.date(2026, 7, 1)
        )
        self.stock("50", item=self.tracked, lot=lot)
        response = self.client.get(
            "/api/inventory/lots/expiring/", {"before": "2026-09-01"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["lot"], "OLD")

    def test_expiring_needs_a_date(self):
        self.assertEqual(
            self.client.get("/api/inventory/lots/expiring/").status_code, 400
        )


class ReportApiTests(ApiTestCase):
    def test_the_reconciliation_is_reachable(self):
        self.stock("100", "5")
        response = self.client.get("/api/inventory/stock-reports/reconciliation/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("balanced", response.data)
        self.assertIn("difference", response.data)

    def test_the_valuation_is_reachable(self):
        self.stock("100", "5")
        response = self.client.get("/api/inventory/stock-reports/valuation/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["total_value"]), Decimal("500.00"))

    def test_an_item_can_be_asked_where_its_stock_is(self):
        self.stock("100", "5")
        self.stock("40", "5", warehouse=self.south)
        response = self.client.get(f"/api/inventory/items/{self.item.pk}/stock/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rows"]), 2)

    def test_an_item_can_be_asked_for_its_ledger(self):
        self.stock("100", "5")
        response = self.client.get(f"/api/inventory/items/{self.item.pk}/ledger/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["closing"]), Decimal("100"))

    def test_slow_moving_needs_a_date(self):
        self.assertEqual(
            self.client.get("/api/inventory/stock-reports/slow-moving/").status_code, 400
        )

    def test_the_movement_summary_needs_its_window(self):
        self.assertEqual(
            self.client.get("/api/inventory/stock-reports/movements/").status_code, 400
        )


class ReservationsAreReadOnlyTests(ApiTestCase):
    def test_a_claim_cannot_be_created_out_of_thin_air(self):
        # One created here is a claim nothing will ever give back.
        response = self.client.post(
            "/api/inventory/stock-reservations/",
            {"item": self.item.pk, "warehouse": self.north.pk, "quantity": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 405)


class StandardCostApiTests(ApiTestCase):
    def test_a_standard_cannot_be_typed_over(self):
        # Changing it revalues the stock on hand, which is a posting.
        priced = Item.objects.create(
            sku="ST", name="Standard", uom=self.each,
            costing_method="standard", standard_cost=Decimal("5"),
        )
        self.client.patch(
            f"/api/inventory/items/{priced.pk}/",
            {"standard_cost": "9"}, format="json",
        )
        priced.refresh_from_db()
        self.assertEqual(priced.standard_cost, Decimal("5.0000"))

    def test_setting_it_is_an_action_that_revalues(self):
        priced = Item.objects.create(
            sku="ST", name="Standard", uom=self.each,
            costing_method="standard", standard_cost=Decimal("5"),
        )
        self.stock("100", "5", item=priced)
        response = self.client.post(
            f"/api/inventory/items/{priced.pk}/set_standard_cost/",
            {"standard_cost": "7", "reason": self.reason.pk}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        priced.refresh_from_db()
        self.assertEqual(priced.standard_cost, Decimal("7.0000"))
        self.assertEqual(priced.stock_value_at(self.north), Decimal("700.00"))


class VariantApiTests(ApiTestCase):
    """Variants get a door on the way in, rather than after somebody asks."""

    def setUp(self):
        super().setUp()
        from .models import ItemAttribute, ItemAttributeValue, ItemTemplate

        self.colour = ItemAttribute.objects.create(code="COL", name="Colour")
        for code, name in (("RED", "Red"), ("BLU", "Blue")):
            ItemAttributeValue.objects.create(
                attribute=self.colour, code=code, name=name
            )
        self.template = ItemTemplate.objects.create(
            code="SHIRT", name="Shirt", uom=self.each
        )
        self.template.attributes.set([self.colour])

    def test_the_routes_exist(self):
        for path in (
            "/api/inventory/item-templates/",
            "/api/inventory/item-attributes/",
            "/api/inventory/item-attribute-values/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_generating_variants_is_an_action(self):
        response = self.client.post(
            f"/api/inventory/item-templates/{self.template.pk}/generate-variants/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(
            sorted(row["sku"] for row in response.data["created"]),
            ["SHIRT-BLU", "SHIRT-RED"],
        )

    def test_generating_again_creates_nothing(self):
        path = f"/api/inventory/item-templates/{self.template.pk}/generate-variants/"
        self.client.post(path, {}, format="json")
        response = self.client.post(path, {}, format="json")
        self.assertEqual(response.data["created"], [])
        self.assertEqual(response.data["total"], 2)

    def test_a_product_can_be_asked_how_many_there_are_altogether(self):
        self.client.post(
            f"/api/inventory/item-templates/{self.template.pk}/generate-variants/",
            {}, format="json",
        )
        red = Item.objects.get(sku="SHIRT-RED")
        self.stock("40", "12", item=red)
        response = self.client.get(
            f"/api/inventory/item-templates/{self.template.pk}/stock/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["on_hand"]), Decimal("40"))
        self.assertEqual(Decimal(response.data["value"]), Decimal("480.00"))

    def test_a_product_that_varies_by_nothing_is_refused_in_words(self):
        from .models import ItemTemplate

        plain = ItemTemplate.objects.create(code="P", name="Plain", uom=self.each)
        response = self.client.post(
            f"/api/inventory/item-templates/{plain.pk}/generate-variants/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("varies by nothing", str(response.data))

    def test_retiring_a_product_is_an_action(self):
        self.client.post(
            f"/api/inventory/item-templates/{self.template.pk}/generate-variants/",
            {}, format="json",
        )
        response = self.client.post(
            f"/api/inventory/item-templates/{self.template.pk}/deactivate/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])
        self.assertEqual(
            Item.objects.filter(template=self.template, is_active=True).count(), 0
        )
