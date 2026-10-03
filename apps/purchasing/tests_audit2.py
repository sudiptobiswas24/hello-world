"""
Audit pass over the eight new purchasing features.

All four findings are the same shape as before: a guard that exists on
one path and not on its mirror. Three of them are returns — the reverse
of a flow that had been built and tested only forwards.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item, MovementType, StockMovement, Warehouse
from apps.sales.models import InvoicePolicy, SalesOrder, SalesOrderLine

from .models import (
    BlanketOrder,
    BlanketOrderLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseApprovalPolicy,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
    VendorPrice,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class ReleaseBeyondAgreementTests(PurchasingLifecycleTestCase):
    """release() checked the commitment; editing the line afterwards did
    not — the guard existed on one path and not its mirror."""

    def agreement(self):
        blanket = BlanketOrder.objects.create(
            vendor=self.vendor, start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31), currency=self.usd,
        )
        self.line = BlanketOrderLine.objects.create(
            blanket=blanket, item=self.item, uom=self.uom,
            quantity=Decimal("100"), unit_price=Decimal("5"),
        )
        blanket.confirm()
        return blanket

    def test_editing_a_release_beyond_the_commitment_is_refused(self):
        blanket = self.agreement()
        order = blanket.release({self.line: Decimal("40")},
                                order_date=datetime.date(2026, 2, 1))
        order_line = order.lines.get()
        order_line.quantity = Decimal("500")

        with self.assertRaisesMessage(ValidationError, "would exceed it"):
            order_line.save()

    def test_editing_within_the_commitment_is_allowed(self):
        blanket = self.agreement()
        order = blanket.release({self.line: Decimal("40")},
                                order_date=datetime.date(2026, 2, 1))
        order_line = order.lines.get()
        order_line.quantity = Decimal("60")
        order_line.save()

        self.assertEqual(self.line.quantity_released(), Decimal("60"))

    def test_a_second_release_still_respects_the_first(self):
        blanket = self.agreement()
        blanket.release({self.line: Decimal("70")}, order_date=datetime.date(2026, 2, 1))
        second = blanket.release({self.line: Decimal("30")},
                                 order_date=datetime.date(2026, 3, 1))
        line = second.lines.get()
        line.quantity = Decimal("60")

        with self.assertRaisesMessage(ValidationError, "would exceed it"):
            line.save()


class ApprovalStalenessTests(PurchasingLifecycleTestCase):
    """Re-pricing dropped the approval; adding a line did not, because a
    new line has no previous version to compare against."""

    def setUp(self):
        super().setUp()
        self.approver = get_user_model().objects.create_user(username="boss", password="x")
        PurchaseApprovalPolicy.objects.create(
            code="P", name="Policy", max_order_value=Decimal("1000")
        )

    def approved_order(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("300"), unit_price=Decimal("5"),
        )
        order.approve(by=self.approver)
        return order

    def test_adding_a_line_after_approval_drops_it(self):
        order = self.approved_order()

        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("300"), unit_price=Decimal("5"),
        )

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)
        with self.assertRaisesMessage(ValidationError, "needs approval"):
            order.confirm()

    def test_removing_a_line_after_approval_drops_it(self):
        order = self.approved_order()
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.refresh_from_db()
        order.approve(by=self.approver)

        order.lines.last().delete()

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)


class SalesApprovalStalenessTests(PurchasingLifecycleTestCase):
    """The identical hole existed on the sales side."""

    def test_adding_a_line_to_an_approved_sales_order_drops_it(self):
        from apps.sales.models import ApprovalPolicy

        approver = get_user_model().objects.create_user(username="ctrl", password="x")
        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        ApprovalPolicy.objects.create(
            code="P", name="Policy", max_order_value=Decimal("1000")
        )

        order = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 1, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("300"),
            unit_price=Decimal("5"), revenue_account=revenue,
        )
        order.approve(by=approver)

        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("300"),
            unit_price=Decimal("5"), revenue_account=revenue,
        )

        order.refresh_from_db()
        self.assertIsNone(order.approved_at)


class SubcontractReturnTests(PurchasingLifecycleTestCase):
    """Reversing only the finished item destroyed the components, which
    the vendor still has."""

    def setUp(self):
        super().setUp()
        self.sub = Warehouse.objects.create(code="SUB", name="Sub")
        self.component = Item.objects.create(sku="CMP", name="Component", uom=self.uom)
        self.finished = Item.objects.create(sku="FIN", name="Finished", uom=self.uom)
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        from django.utils import timezone

        StockMovement.objects.create(
            item=self.component, warehouse=self.warehouse,
            movement_type=MovementType.RECEIPT, uom=self.component.uom, quantity=Decimal("100"),
            unit_cost=Decimal("10"), occurred_at=timezone.now(),
        )

    def subcontract(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.sub,
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.finished, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        SubcontractComponent.objects.create(
            order_line=line, item=self.component, quantity_per=Decimal("1")
        )
        order.confirm()
        order.issue_components(self.warehouse)
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 2, 1)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.warehouse,
            quantity_received=Decimal("10"),
        )
        receipt.post()
        return order, receipt

    def test_returning_gives_the_components_back(self):
        order, receipt = self.subcontract()
        self.assertEqual(self.component.on_hand_at(self.sub), Decimal("0"))

        receipt.create_return()

        self.assertEqual(self.component.on_hand_at(self.sub), Decimal("10"))
        self.assertEqual(self.finished.on_hand_at(self.warehouse), Decimal("0"))

    def test_the_finished_goods_go_back_at_their_full_cost(self):
        order, receipt = self.subcontract()
        receipt.create_return()
        self.assertEqual(self.finished.stock_value_at(self.warehouse), Decimal("0.00"))


class DropShipReturnTests(PurchasingLifecycleTestCase):
    """The forward path never moved stock; the return did."""

    def setUp(self):
        super().setUp()
        self.cogs = Account.objects.create(
            code="5001", name="COGS", account_type=AccountType.EXPENSE
        )
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.default_purchase_expense_account = self.expense
        company.save()
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        VendorPrice.objects.create(
            vendor=self.vendor, item=self.item, currency=self.usd, unit_price=Decimal("6")
        )

    def drop_shipped(self):
        sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 1, 1),
            currency=self.usd, invoice_policy=InvoicePolicy.DELIVERED,
        )
        self.sales_line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("10"),
            unit_price=Decimal("10"), revenue_account=self.revenue,
        )
        sale.confirm()
        order = PurchaseOrder.create_for_drop_ship(
            sale, self.vendor, order_date=datetime.date(2026, 1, 2)
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 10)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal("10"),
        )
        receipt.post()
        return sale, order, receipt

    def test_returning_invents_no_stock(self):
        sale, order, receipt = self.drop_shipped()

        receipt.create_return()

        self.assertEqual(StockMovement.objects.filter(item=self.item).count(), 0)
        self.assertEqual(self.item.on_hand_at(self.warehouse), 0)

    def test_the_cost_of_sales_is_reversed(self):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        sale, order, receipt = self.drop_shipped()
        receipt.create_return()

        rows = JournalLine.objects.filter(
            account=self.cogs, entry__posted=True
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        self.assertEqual((rows["debit"] or 0) - (rows["credit"] or 0), Decimal("0"))

    def test_the_customer_line_is_unshipped_again(self):
        sale, order, receipt = self.drop_shipped()
        self.assertEqual(self.sales_line.quantity_shipped(), Decimal("10"))

        receipt.create_return()

        self.assertEqual(self.sales_line.quantity_shipped(), Decimal("0"))


class MechanicalAuditTests(PurchasingLifecycleTestCase):
    """The findings `manage.py audit_invariants` turned up."""

    def test_a_purchase_line_cannot_have_a_negative_quantity(self):
        """Sales order lines had this constraint; the purchase mirror did
        not, which is the shape the checklist puts first."""
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        with self.assertRaises(Exception):
            PurchaseOrderLine.objects.create(
                order=order, item=self.item, uom=self.uom,
                quantity=Decimal("-5"), unit_price=Decimal("5"),
            )

    def test_a_receipt_line_cannot_have_a_negative_quantity(self):
        """clean() checked it and nothing calls clean() on the code paths
        that build receipts."""
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        with self.assertRaises(Exception):
            GoodsReceiptLine.objects.create(
                receipt=receipt, order_line=line, warehouse=self.warehouse,
                quantity_received=Decimal("-3"),
            )

    def test_withdraw_approval_can_be_called_directly(self):
        approver = get_user_model().objects.create_user(username="w", password="x")
        PurchaseApprovalPolicy.objects.create(
            code="P", name="P", max_order_value=Decimal("10")
        )
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.approve(by=approver)

        order.withdraw_approval()

        self.assertIsNone(order.approved_at)
        self.assertEqual(order.approval_note, "")

    def test_the_company_base_currency_must_agree_with_the_currency_flag(self):
        """Two sources of truth for one fact is a defect whichever wins."""
        from apps.core.models import Currency

        eur = Currency.objects.create(code="EUR", name="Euro")
        company = Company.get()
        company.base_currency = eur
        with self.assertRaisesMessage(ValidationError, "not flagged as the base currency"):
            company.clean()
