"""
Vendor performance.

Every input already existed — receipt dates against expected dates,
received against ordered, billed price against ordered price. Nobody had
put them together, which meant the only way to know a vendor was
consistently late was to have noticed.
"""

import datetime
from decimal import Decimal

from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    vendor_performance,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class PerformanceTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()

    def order(self, quantity="100", price="5", expected=datetime.date(2026, 1, 20),
              vendor=None, confirm=True):
        order = PurchaseOrder.objects.create(
            vendor=vendor or self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        self.line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
            unit_price=Decimal(price), expected_date=expected,
        )
        if confirm:
            order.confirm()
        return order

    def receive_on(self, order, quantity, when):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=when)
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def row_for(self, vendor=None):
        rows = vendor_performance(vendor=vendor or self.vendor)
        return rows[0] if rows else None


class OnTimeTests(PerformanceTestCase):
    def test_delivering_on_the_expected_date_is_on_time(self):
        order = self.order(expected=datetime.date(2026, 1, 20))
        self.receive_on(order, "100", datetime.date(2026, 1, 20))

        row = self.row_for()
        self.assertEqual(row["on_time_rate"], Decimal("100.00"))
        self.assertEqual(row["average_days_late"], Decimal("0"))

    def test_delivering_early_is_on_time(self):
        order = self.order(expected=datetime.date(2026, 1, 20))
        self.receive_on(order, "100", datetime.date(2026, 1, 15))
        self.assertEqual(self.row_for()["on_time_rate"], Decimal("100.00"))

    def test_delivering_late_is_measured_in_days(self):
        order = self.order(expected=datetime.date(2026, 1, 20))
        self.receive_on(order, "100", datetime.date(2026, 1, 27))

        row = self.row_for()
        self.assertEqual(row["on_time_rate"], Decimal("0.00"))
        self.assertEqual(row["average_days_late"], Decimal("7.00"))

    def test_it_is_weighted_by_quantity(self):
        """One late pallet out of ten must not read the same as ten."""
        order = self.order("100", expected=datetime.date(2026, 1, 20))
        self.receive_on(order, "90", datetime.date(2026, 1, 20))
        self.receive_on(order, "10", datetime.date(2026, 1, 30))

        self.assertEqual(self.row_for()["on_time_rate"], Decimal("90.00"))

    def test_a_line_with_no_expected_date_is_left_out(self):
        """A vendor never given a date cannot be late, and scoring them
        punctual would flatter them."""
        order = self.order(expected=None)
        self.receive_on(order, "100", datetime.date(2026, 3, 1))

        self.assertIsNone(self.row_for()["on_time_rate"])

    def test_a_return_does_not_count_as_a_delivery(self):
        order = self.order(expected=datetime.date(2026, 1, 20))
        receipt = self.receive_on(order, "100", datetime.date(2026, 1, 20))
        receipt.create_return()

        self.assertEqual(self.row_for()["quantity_received"], Decimal("0"))


class FillRateTests(PerformanceTestCase):
    def test_a_short_delivery_shows_as_a_fill_rate(self):
        order = self.order("100")
        self.receive_on(order, "80", datetime.date(2026, 1, 20))

        row = self.row_for()
        self.assertEqual(row["fill_rate"], Decimal("80.00"))
        self.assertEqual(row["open_lines"], 1)

    def test_a_complete_delivery_closes_the_line(self):
        order = self.order("100")
        self.receive_on(order, "100", datetime.date(2026, 1, 20))

        row = self.row_for()
        self.assertEqual(row["fill_rate"], Decimal("100.00"))
        self.assertEqual(row["open_lines"], 0)


class PriceVarianceTests(PerformanceTestCase):
    def test_billing_above_the_ordered_price_shows_up(self):
        order = self.order("100", "5")
        self.receive_on(order, "100", datetime.date(2026, 1, 20))
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 25),
            purchase_order=order, payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, order_line=self.line, item=self.item,
            quantity=Decimal("100"), unit_price=Decimal("5.20"),
            expense_account=self.expense,
        )
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("10")
        from apps.accounting.models import Account, AccountType

        company.purchase_price_variance_account = Account.objects.create(
            code="5900", name="PPV", account_type=AccountType.EXPENSE
        )
        company.save()
        bill.post()

        self.assertEqual(self.row_for()["price_variance"], Decimal("20.00"))

    def test_billing_at_the_ordered_price_shows_nothing(self):
        order = self.order("100", "5")
        self.receive_on(order, "100", datetime.date(2026, 1, 20))
        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(self.row_for()["price_variance"], Decimal("0"))


class ScopeTests(PerformanceTestCase):
    def test_vendors_are_reported_separately(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.order("100", vendor=self.vendor)
        self.order("50", vendor=other)

        rows = vendor_performance()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["vendor"], self.vendor)

    def test_a_cancelled_order_is_left_out(self):
        order = self.order("100", confirm=False)
        order.cancel()
        self.assertIsNone(self.row_for())

    def test_the_period_is_respected(self):
        self.order("100")
        self.assertEqual(vendor_performance(start=datetime.date(2026, 6, 1)), [])
        self.assertEqual(len(vendor_performance(end=datetime.date(2026, 6, 1))), 1)

    def test_charges_are_not_counted_as_goods(self):
        from apps.accounting.models import Account, AccountType, ChargeType

        freight = ChargeType.objects.create(
            code="FRT", name="Freight",
            expense_account=Account.objects.create(
                code="5300", name="Freight", account_type=AccountType.EXPENSE
            ),
        )
        order = self.order("100")
        order.add_charge(freight, Decimal("50"))

        self.assertEqual(self.row_for()["order_lines"], 1)
