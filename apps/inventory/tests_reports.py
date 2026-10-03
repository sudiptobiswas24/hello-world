"""
Reading the stock ledger back.

Every other module here can be asked what it did. Inventory posted to
the ledger for the whole of this project and could answer nothing about
itself.

The test that matters is the reconciliation one. Stock is a subledger:
what the shelves are worth has to equal what the inventory accounts
say, and until now every test asserted that one item at a time. Nothing
proved it in aggregate, which is exactly where a difference hides.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)

from .models import (
    AdjustmentReason,
    CostingMethod,
    Item,
    MovementType,
    StockAdjustment,
    StockAdjustmentLine,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    Warehouse,
    movement_summary,
    negative_stock,
    reconcile_to_ledger,
    slow_moving,
    stock_aging,
    stock_ledger,
    stock_valuation,
)


class ReportTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.spares_stock = acc("1210", "Inventory - spares", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.ap = acc("2000", "AP", AccountType.LIABILITY)
        self.ar = acc("1100", "AR", AccountType.ASSET)
        self.revenue = acc("4000", "Revenue", AccountType.INCOME)
        self.shrinkage = acc("6200", "Shrinkage", AccountType.EXPENSE)
        self.purchases = acc("5300", "Purchases", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
            default_purchase_expense_account=self.purchases,
        )
        self.widget = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.spare = Item.objects.create(
            sku="S", name="Spare", uom=self.each, inventory_account=self.spares_stock
        )
        self.north = Warehouse.objects.create(code="N", name="North")
        self.south = Warehouse.objects.create(code="S", name="South")
        self.reason = AdjustmentReason.objects.create(
            code="SHRINK", name="Shrinkage", account=self.shrinkage
        )
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def stock(self, quantity, cost="5", item=None, warehouse=None, on=None):
        target = item or self.widget
        when = on or timezone.now()
        if isinstance(when, datetime.date) and not isinstance(when, datetime.datetime):
            when = timezone.make_aware(datetime.datetime.combine(when, datetime.time(9, 0)))
        return StockMovement.objects.create(
            item=target, warehouse=warehouse or self.north,
            movement_type=MovementType.RECEIPT, uom=target.uom,
            quantity=Decimal(quantity), unit_cost=Decimal(cost), occurred_at=when,
        )

    def issue(self, quantity, item=None, warehouse=None, on=None):
        target = item or self.widget
        shelf = warehouse or self.north
        when = on or timezone.now()
        if isinstance(when, datetime.date) and not isinstance(when, datetime.datetime):
            when = timezone.make_aware(datetime.datetime.combine(when, datetime.time(9, 0)))
        return StockMovement.objects.create(
            item=target, warehouse=shelf, movement_type=MovementType.ISSUE,
            uom=target.uom, quantity=-Decimal(quantity),
            unit_cost=target.removal_unit_cost(shelf, Decimal(quantity)),
            occurred_at=when,
        )

    def buy(self, quantity, price, item=None, warehouse=None):
        from apps.purchasing.models import (
            GoodsReceipt,
            GoodsReceiptLine,
            PurchaseOrder,
            PurchaseOrderLine,
        )

        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=item or self.widget, uom=self.each,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=warehouse or self.north,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        return order

    def sell(self, quantity, price, item=None, warehouse=None):
        from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 2, 1),
            currency=self.usd,
        )
        line = SalesOrderLine.objects.create(
            order=order, item=item or self.widget, uom=self.each,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            revenue_account=self.revenue,
        )
        order.confirm()
        delivery = Delivery.objects.create(
            sales_order=order, delivery_date=datetime.date(2026, 2, 2)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=warehouse or self.north,
            quantity_shipped=Decimal(quantity),
        )
        delivery.post()
        return order

    def balance(self, account):
        return sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )


class ReconciliationTests(ReportTestCase):
    """
    The report this module existed without. A stock system that cannot
    prove it agrees with the general ledger is one somebody stops
    believing the first time an auditor asks.
    """

    def test_a_buy_and_a_sell_leave_the_two_agreeing(self):
        self.buy("100", "6")
        self.sell("40", "15")
        report = reconcile_to_ledger()
        self.assertTrue(report["balanced"], report["rows"])
        self.assertEqual(report["difference"], Decimal("0.00"))
        self.assertEqual(report["total_stock_value"], Decimal("360.00"))

    def test_an_adjustment_keeps_them_agreeing(self):
        self.buy("100", "6")
        adjustment = StockAdjustment.objects.create(
            adjustment_date=datetime.date(2026, 3, 1),
            warehouse=self.north, reason=self.reason,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adjustment, item=self.widget, uom=self.each,
            quantity=Decimal("-7"),
        )
        adjustment.post()
        report = reconcile_to_ledger()
        self.assertTrue(report["balanced"], report["rows"])

    def test_a_transfer_moves_nothing_between_accounts(self):
        self.buy("100", "6")
        move = StockTransfer.objects.create(
            transfer_date=datetime.date(2026, 3, 1),
            from_warehouse=self.north, to_warehouse=self.south,
        )
        StockTransferLine.objects.create(
            transfer=move, item=self.widget, uom=self.each, quantity=Decimal("30")
        )
        move.post()
        report = reconcile_to_ledger()
        self.assertTrue(report["balanced"], report["rows"])

    def test_items_are_grouped_by_the_account_they_are_held_in(self):
        # A single company-wide total would net a shortfall on one
        # account against a surplus on another and call it balanced.
        self.buy("100", "6")
        self.buy("50", "4", item=self.spare)
        report = reconcile_to_ledger()
        rows = {row["account"].code: row for row in report["rows"]}
        self.assertEqual(rows["1200"]["stock_value"], Decimal("600.00"))
        self.assertEqual(rows["1210"]["stock_value"], Decimal("200.00"))
        self.assertTrue(report["balanced"])

    def test_a_difference_is_reported_rather_than_netted_away(self):
        self.buy("100", "6")
        # A journal posted straight at inventory, behind the stock
        # ledger's back — which is how a real difference arises.
        from apps.accounting.models import JournalEntry

        entry = JournalEntry.objects.create(
            date=datetime.date(2026, 3, 1), memo="Manual"
        )
        JournalLine.objects.create(entry=entry, account=self.inventory, debit=Decimal("50"))
        JournalLine.objects.create(entry=entry, account=self.shrinkage, credit=Decimal("50"))
        entry.post()
        report = reconcile_to_ledger()
        self.assertFalse(report["balanced"])
        self.assertEqual(report["difference"], Decimal("-50.00"))

    def test_it_can_be_asked_as_at_a_date(self):
        self.buy("100", "6")
        self.sell("40", "15")
        # Before any of it happened.
        report = reconcile_to_ledger(as_of=datetime.date(2025, 12, 31))
        self.assertEqual(report["total_stock_value"], Decimal("0.00"))
        self.assertTrue(report["balanced"])

    def test_an_item_with_nowhere_to_be_valued_is_named_not_dropped(self):
        company = Company.get()
        company.default_inventory_account = None
        company.save()
        orphan = Item.objects.create(sku="O", name="Orphan", uom=self.each)
        report = reconcile_to_ledger()
        self.assertIn(orphan, report["unvalued_items"])


class ValuationTests(ReportTestCase):
    def test_it_lists_what_is_on_each_shelf(self):
        self.stock("100", "5")
        self.stock("40", "5", warehouse=self.south)
        report = stock_valuation()
        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual(report["total_value"], Decimal("700.00"))

    def test_empty_shelves_are_left_out_unless_asked_for(self):
        self.stock("100", "5")
        self.assertEqual(len(stock_valuation()["rows"]), 1)
        self.assertGreater(len(stock_valuation(include_empty=True)["rows"]), 1)

    def test_the_unit_cost_is_per_item_and_warehouse(self):
        # A company-wide unit cost for stock held at two different costs
        # describes nothing.
        self.stock("100", "4")
        self.stock("100", "10", warehouse=self.south)
        rows = {row["warehouse"].code: row for row in stock_valuation()["rows"]}
        self.assertEqual(rows["N"]["unit_cost"], Decimal("4.0000"))
        self.assertEqual(rows["S"]["unit_cost"], Decimal("10.0000"))

    def test_it_can_be_asked_as_at_a_date(self):
        self.stock("100", "5", on=datetime.date(2026, 1, 10))
        self.stock("50", "5", on=datetime.date(2026, 3, 10))
        self.assertEqual(
            stock_valuation(as_of=datetime.date(2026, 2, 1))["total_value"],
            Decimal("500.00"),
        )

    def test_the_report_agrees_with_the_item(self):
        self.stock("100", "4")
        self.stock("100", "6")
        row = stock_valuation()["rows"][0]
        self.assertEqual(row["value"], self.widget.stock_value_at(self.north))


class StockLedgerTests(ReportTestCase):
    def test_every_movement_with_a_running_balance(self):
        self.stock("100", "5")
        self.issue("30")
        self.stock("10", "5")
        report = stock_ledger(self.widget)
        self.assertEqual(
            [row["balance"] for row in report["rows"]],
            [Decimal("100.0000"), Decimal("70.0000"), Decimal("80.0000")],
        )
        self.assertEqual(report["closing"], Decimal("80.0000"))

    def test_a_window_opens_at_what_came_before_it(self):
        self.stock("100", "5", on=datetime.date(2026, 1, 10))
        self.issue("30", on=datetime.date(2026, 2, 10))
        report = stock_ledger(
            self.widget, start=datetime.date(2026, 2, 1), end=datetime.date(2026, 2, 28)
        )
        self.assertEqual(report["opening"], Decimal("100.0000"))
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["closing"], Decimal("70.0000"))

    def test_the_running_balance_always_agrees_with_the_lines_above_it(self):
        self.stock("100", "5")
        self.issue("30")
        self.issue("20")
        report = stock_ledger(self.widget)
        running = report["opening"]
        for row in report["rows"]:
            running += row["quantity"]
            self.assertEqual(row["balance"], running)

    def test_it_can_be_asked_about_one_warehouse(self):
        self.stock("100", "5")
        self.stock("40", "5", warehouse=self.south)
        self.assertEqual(
            stock_ledger(self.widget, warehouse=self.south)["closing"],
            Decimal("40.0000"),
        )


class AgingTests(ReportTestCase):
    def test_stock_is_dated_by_the_arrival_that_left_it_there(self):
        self.stock("100", "5", on=datetime.date(2026, 1, 1))
        report = stock_aging(as_of=datetime.date(2026, 3, 1))
        row = report["rows"][0]
        self.assertEqual(row["oldest_days"], 59)
        self.assertEqual(row["buckets"]["31-60"], Decimal("100.0000"))

    def test_the_oldest_goods_are_the_ones_that_left(self):
        # Aging assumes physical first-in-first-out whatever values it.
        self.stock("50", "5", on=datetime.date(2026, 1, 1))
        self.stock("50", "5", on=datetime.date(2026, 2, 20))
        self.issue("50", on=datetime.date(2026, 2, 25))
        report = stock_aging(as_of=datetime.date(2026, 3, 1))
        row = report["rows"][0]
        self.assertEqual(row["oldest_days"], 9)
        self.assertEqual(row["buckets"]["0-30"], Decimal("50.0000"))

    def test_it_spreads_across_buckets(self):
        self.stock("10", "5", on=datetime.date(2025, 6, 1))
        self.stock("20", "5", on=datetime.date(2026, 2, 20))
        report = stock_aging(as_of=datetime.date(2026, 3, 1))
        buckets = report["rows"][0]["buckets"]
        self.assertEqual(buckets["0-30"], Decimal("20.0000"))
        self.assertEqual(buckets["180+"], Decimal("10.0000"))

    def test_a_value_only_movement_does_not_look_like_an_arrival(self):
        # Landed cost changes what stock is worth, not when it got here.
        self.stock("100", "5", on=datetime.date(2026, 1, 1))
        StockMovement.objects.create(
            item=self.widget, warehouse=self.north,
            movement_type=MovementType.ADJUSTMENT, uom=self.each,
            quantity=Decimal("0"), value_adjustment=Decimal("200"),
            occurred_at=timezone.make_aware(
                datetime.datetime(2026, 2, 25, 9, 0)
            ),
        )
        report = stock_aging(as_of=datetime.date(2026, 3, 1))
        self.assertEqual(report["rows"][0]["oldest_days"], 59)


class SlowMovingTests(ReportTestCase):
    def test_stock_that_has_not_moved_out_is_listed(self):
        self.stock("100", "5", on=datetime.date(2025, 6, 1))
        report = slow_moving(since=datetime.date(2026, 1, 1))
        self.assertEqual(report["rows"][0]["item"], self.widget)
        self.assertEqual(report["total_value"], Decimal("500.00"))

    def test_stock_that_has_moved_is_not(self):
        self.stock("100", "5", on=datetime.date(2025, 6, 1))
        self.issue("10", on=datetime.date(2026, 2, 1))
        self.assertEqual(slow_moving(since=datetime.date(2026, 1, 1))["rows"], [])

    def test_an_empty_shelf_is_not_slow_moving(self):
        self.stock("100", "5", on=datetime.date(2025, 6, 1))
        self.issue("100", on=datetime.date(2025, 7, 1))
        self.assertEqual(slow_moving(since=datetime.date(2026, 1, 1))["rows"], [])

    def test_it_says_when_the_item_last_went_out(self):
        self.stock("100", "5", on=datetime.date(2025, 6, 1))
        self.issue("10", on=datetime.date(2025, 7, 1))
        row = slow_moving(since=datetime.date(2026, 1, 1))["rows"][0]
        self.assertEqual(row["last_issued"].date(), datetime.date(2025, 7, 1))

    def test_the_most_valuable_comes_first(self):
        self.stock("100", "5", on=datetime.date(2025, 6, 1))
        self.stock("10", "4", item=self.spare, on=datetime.date(2025, 6, 1))
        rows = slow_moving(since=datetime.date(2026, 1, 1))["rows"]
        self.assertEqual(rows[0]["item"], self.widget)


class NegativeStockTests(ReportTestCase):
    def test_a_shelf_below_zero_is_an_exception(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        self.stock("10", "5", warehouse=backorders)
        self.issue("15", warehouse=backorders)
        report = negative_stock()
        self.assertEqual(report["rows"][0]["quantity"], Decimal("-5"))

    def test_a_warehouse_that_allows_it_is_not_unexpected(self):
        backorders = Warehouse.objects.create(
            code="B", name="Backorders", allow_negative_stock=True
        )
        self.stock("10", "5", warehouse=backorders)
        self.issue("15", warehouse=backorders)
        self.assertEqual(negative_stock()["unexpected"], [])

    def test_nothing_negative_is_nothing_to_report(self):
        self.stock("100", "5")
        self.assertEqual(negative_stock()["rows"], [])


class MovementSummaryTests(ReportTestCase):
    def test_it_says_what_happened_rather_than_what_is_left(self):
        # Stock is worth the same as last month, but did nothing happen
        # or did a great deal happen twice?
        self.stock("100", "5", on=datetime.date(2026, 2, 1))
        self.issue("100", on=datetime.date(2026, 2, 10))
        self.stock("100", "5", on=datetime.date(2026, 2, 20))
        report = movement_summary(
            datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)
        )
        rows = {row["type"]: row for row in report["rows"]}
        self.assertEqual(rows["receipt"]["in"], Decimal("200.0000"))
        self.assertEqual(rows["receipt"]["count"], 2)
        self.assertEqual(rows["issue"]["out"], Decimal("100.0000"))

    def test_it_respects_its_window(self):
        self.stock("100", "5", on=datetime.date(2026, 1, 15))
        self.stock("50", "5", on=datetime.date(2026, 2, 15))
        report = movement_summary(
            datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)
        )
        self.assertEqual(report["rows"][0]["in"], Decimal("50.0000"))
