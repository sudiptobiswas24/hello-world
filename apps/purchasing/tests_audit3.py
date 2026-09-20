"""
Audit pass over the six features built after the checklist landed.

Zero findings, which is the first time that has happened here — and
worth reading carefully rather than celebrating. The probe checked six
hypotheses drawn from the checklist's shapes, and the shapes it did not
think to check are, as ever, where the next defect is.

These tests pin the behaviours the probe verified, so they stay true.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from apps.core.models import Company
from apps.inventory.models import Warehouse

from .models import (
    ApprovalTier,
    Bill,
    BillLine,
    Budget,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseApprovalPolicy,
    PurchaseOrder,
    PurchaseOrderLine,
    ReorderRule,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class AuditThreeTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()


class InspectionDrawdownTests(AuditThreeTestCase):
    """Accept and reject share one pool; neither may spend the other's."""

    def inspected(self, quantity="10"):
        quarantine = Warehouse.objects.create(
            code="QA", name="Bay", is_quarantine=True
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal("5"),
            inspect_on_receipt=True,
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        receipt_line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=quarantine,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt, receipt_line

    def test_rejected_goods_cannot_then_be_accepted(self):
        receipt, line = self.inspected("10")
        receipt.reject(quantities={line: Decimal("10")}, debit_bills=False)

        with self.assertRaisesMessage(ValidationError, "awaiting inspection"):
            receipt.accept(self.warehouse, quantities={line: Decimal("10")})

    def test_accepted_goods_cannot_then_be_rejected(self):
        receipt, line = self.inspected("10")
        receipt.accept(self.warehouse, quantities={line: Decimal("10")})

        with self.assertRaisesMessage(ValidationError, "awaiting inspection"):
            receipt.reject(quantities={line: Decimal("10")})


class ReorderReleaseTests(AuditThreeTestCase):
    def test_cancelling_an_order_releases_what_it_had_on_order(self):
        rule = ReorderRule.objects.create(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal("20"), target=Decimal("100"),
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("500"), unit_price=Decimal("5"),
        )
        order.confirm()
        before = rule.on_order()

        order.cancel()

        self.assertEqual(before - rule.on_order(), Decimal("500"))

    def test_goods_rejected_back_to_the_vendor_are_still_on_order(self):
        """The vendor owes ten good units; the order is not satisfied."""
        quarantine = Warehouse.objects.create(
            code="QA", name="Bay", is_quarantine=True
        )
        rule = ReorderRule.objects.create(
            item=self.item, warehouse=self.warehouse,
            minimum=Decimal("20"), target=Decimal("100"),
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
            inspect_on_receipt=True,
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        receipt_line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=quarantine,
            quantity_received=Decimal("10"),
        )
        receipt.post()
        receipt.reject(debit_bills=False)

        self.assertEqual(rule.on_order(), Decimal("10"))


class BudgetReversalTests(AuditThreeTestCase):
    def test_a_debit_note_returns_the_money_to_the_budget(self):
        budget = Budget.objects.create(
            code="B", name="B", account=self.expense,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
            amount=Decimal("10000"),
        )
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 2, 1),
            payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, description="Services", quantity=Decimal("1"),
            unit_price=Decimal("3000"), expense_account=self.expense,
        )
        bill.post()
        self.assertEqual(budget.spent(), Decimal("3000"))

        bill.create_debit_note()

        self.assertEqual(budget.spent(), Decimal("0"))
        self.assertEqual(budget.available(), Decimal("10000"))


class TierRecheckTests(AuditThreeTestCase):
    def test_an_approval_dropped_by_a_change_is_re_checked_against_the_new_total(self):
        """Withdrawing the approval is not enough on its own: the second
        approval has to be measured against what the order now is."""
        policy = PurchaseApprovalPolicy.objects.create(
            code="P", name="P", max_order_value=Decimal("100")
        )
        managers = Group.objects.create(name="Managers")
        ApprovalTier.objects.create(
            policy=policy, group=managers, up_to_amount=Decimal("5000")
        )
        manager = get_user_model().objects.create_user(username="m", password="x")
        manager.groups.add(managers)

        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("100"), unit_price=Decimal("10"),
        )
        order.approve(by=manager)

        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("900"), unit_price=Decimal("10"),
        )
        order.refresh_from_db()

        self.assertIsNone(order.approved_at)
        with self.assertRaisesMessage(ValidationError, "cannot approve"):
            order.approve(by=manager)
