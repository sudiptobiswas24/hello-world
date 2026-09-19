"""Weighted-average costing and the ledger entries that follow from it."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, UnitOfMeasure

from .models import Item, MovementType, StockMovement, Warehouse
from .valuation import post_inventory_entry


class ValuationTestCase(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.cogs = Account.objects.create(
            code="5000", name="Cost of Sales", account_type=AccountType.EXPENSE
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co",
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs,
            grni_account=self.grni,
        )

    def receive(self, quantity, unit_cost, item=None, warehouse=None):
        return StockMovement.objects.create(
            item=item or self.item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, quantity=Decimal(quantity),
            unit_cost=Decimal(unit_cost), occurred_at=timezone.now(),
        )

    def issue(self, quantity, item=None, warehouse=None):
        target_item = item or self.item
        target_warehouse = warehouse or self.warehouse
        return StockMovement.objects.create(
            item=target_item, warehouse=target_warehouse,
            movement_type=MovementType.ISSUE, quantity=-Decimal(quantity),
            unit_cost=target_item.average_cost_at(target_warehouse),
            occurred_at=timezone.now(),
        )


class WeightedAverageTests(ValuationTestCase):
    def test_single_receipt_sets_the_average(self):
        self.receive("10", "5")
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))

    def test_two_receipts_blend(self):
        self.receive("10", "5")   # 50
        self.receive("10", "7")   # 70  -> 120 over 20 units
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("6.0000"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("120.00"))

    def test_uneven_quantities_weight_correctly(self):
        self.receive("100", "1")   # 100
        self.receive("10", "12")   # 120 -> 220 over 110 units = 2.00
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("2.0000"))

    def test_issue_leaves_the_average_unchanged(self):
        self.receive("10", "5")
        self.receive("10", "7")
        self.issue("5")
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("6.0000"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("90.00"))

    def test_receipt_after_an_issue_reblends_from_the_remaining_value(self):
        self.receive("10", "5")
        self.issue("5")            # 25 left over 5 units
        self.receive("5", "9")     # +45 -> 70 over 10 units
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("7.0000"))

    def test_empty_stock_has_no_cost(self):
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("0.00"))

    def test_selling_everything_clears_the_value(self):
        self.receive("10", "5")
        self.issue("10")
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("0.00"))

    def test_valuation_is_per_warehouse(self):
        other = Warehouse.objects.create(code="WH2", name="Other")
        self.receive("10", "5")
        self.receive("10", "20", warehouse=other)
        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))
        self.assertEqual(self.item.average_cost_at(other), Decimal("20.0000"))

    def test_cost_can_be_read_as_it_stood_before_a_movement(self):
        self.receive("10", "5")
        later = self.receive("10", "7")
        self.assertEqual(
            self.item.average_cost_at(self.warehouse, before_id=later.id), Decimal("5.0000")
        )


class InventoryPostingTests(ValuationTestCase):
    def test_receiving_debits_inventory_and_credits_grni(self):
        entry = post_inventory_entry(
            [(self.item, Decimal("120"))], date=datetime.date(2026, 3, 1),
            reference="GR-1", memo="Goods received", direction="in",
        )
        self.assertEqual(entry.lines.get(account=self.inventory).debit, Decimal("120.00"))
        self.assertEqual(entry.lines.get(account=self.grni).credit, Decimal("120.00"))
        self.assertTrue(entry.posted)

    def test_shipping_debits_cost_of_sales_and_credits_inventory(self):
        entry = post_inventory_entry(
            [(self.item, Decimal("60"))], date=datetime.date(2026, 3, 1),
            reference="DO-1", memo="COGS", direction="out",
        )
        self.assertEqual(entry.lines.get(account=self.cogs).debit, Decimal("60.00"))
        self.assertEqual(entry.lines.get(account=self.inventory).credit, Decimal("60.00"))

    def test_a_return_swaps_the_sides(self):
        entry = post_inventory_entry(
            [(self.item, Decimal("60"))], date=datetime.date(2026, 3, 1),
            reference="RET-1", memo="Customer return", direction="out", reverse=True,
        )
        self.assertEqual(entry.lines.get(account=self.inventory).debit, Decimal("60.00"))
        self.assertEqual(entry.lines.get(account=self.cogs).credit, Decimal("60.00"))

    def test_non_stocked_items_post_nothing(self):
        service = Item.objects.create(
            sku="SVC", name="Service", uom=self.uom, item_type="service", track_inventory=False
        )
        self.assertIsNone(
            post_inventory_entry(
                [(service, Decimal("500"))], date=datetime.date(2026, 3, 1),
                reference="X", memo="Service", direction="out",
            )
        )

    def test_zero_value_posts_nothing(self):
        self.assertIsNone(
            post_inventory_entry(
                [(self.item, Decimal("0"))], date=datetime.date(2026, 3, 1),
                reference="X", memo="Nothing", direction="out",
            )
        )

    def test_missing_account_configuration_fails_loudly(self):
        Company.objects.all().delete()
        Company.objects.create(name="Unconfigured")
        with self.assertRaises(ValidationError):
            post_inventory_entry(
                [(self.item, Decimal("10"))], date=datetime.date(2026, 3, 1),
                reference="X", memo="No accounts", direction="out",
            )

    def test_an_item_account_overrides_the_company_default(self):
        special = Account.objects.create(
            code="1299", name="Special Inventory", account_type=AccountType.ASSET
        )
        self.item.inventory_account = special
        self.item.save()
        entry = post_inventory_entry(
            [(self.item, Decimal("10"))], date=datetime.date(2026, 3, 1),
            reference="X", memo="Special", direction="in",
        )
        self.assertEqual(entry.lines.get(account=special).debit, Decimal("10.00"))
