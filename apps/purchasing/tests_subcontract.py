"""
Subcontracting.

What this covers is the part that touches purchasing and the ledger:
components leaving, a finished item arriving, and its cost being what
the components cost plus what the vendor charged to assemble them. The
routing stays with the job worker and is theirs to run.

The components themselves are the other half, and they live in
`tests_job_work.py`: a bill of materials already says what goes into
the thing, and a hand-typed copy of it on a purchase order goes stale
the first time the specification moves.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import JournalLine
from apps.core.models import Company
from django.utils import timezone

from apps.inventory.models import Item, MovementType, StockMovement, Warehouse

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class SubcontractTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.subcontractor = Warehouse.objects.create(code="SUB", name="Acme Assembly")
        self.frame = Item.objects.create(sku="FRM", name="Frame", uom=self.uom)
        self.motor = Item.objects.create(sku="MTR", name="Motor", uom=self.uom)
        self.assembly = Item.objects.create(sku="ASM", name="Assembly", uom=self.uom)
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()
        self.stock(self.frame, "100", "8")
        self.stock(self.motor, "100", "12")

    def stock(self, item, quantity, cost, warehouse=None):
        StockMovement.objects.create(
            item=item, warehouse=warehouse or self.warehouse,
            movement_type=MovementType.RECEIPT, uom=item.uom, quantity=Decimal(quantity),
            unit_cost=Decimal(cost), occurred_at=timezone.now(),
        )

    def subcontract_order(self, quantity="10", service="5", warehouse=True, confirm=True):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor if warehouse else None,
        )
        self.line = PurchaseOrderLine.objects.create(
            order=order, item=self.assembly, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(service),
        )
        SubcontractComponent.objects.create(
            order_line=self.line, item=self.frame, quantity_per=Decimal("1")
        )
        SubcontractComponent.objects.create(
            order_line=self.line, item=self.motor, quantity_per=Decimal("2")
        )
        if confirm:
            order.confirm()
        return order

    def receive(self, order, quantity):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 2, 1)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class ComponentIssueTests(SubcontractTestCase):
    def test_components_move_rather_than_leaving_the_company(self):
        """Stock at a subcontractor is still stock you own and still
        stock you can lose."""
        order = self.subcontract_order("10")
        before = self.balance(self.inventory)

        order.issue_components(self.warehouse)

        self.assertEqual(self.frame.on_hand_at(self.warehouse), Decimal("90"))
        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("10"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("20"))
        self.assertEqual(self.balance(self.inventory), before)

    def test_the_value_goes_with_them(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.assertEqual(self.frame.stock_value_at(self.subcontractor), Decimal("80.00"))
        self.assertEqual(self.motor.stock_value_at(self.subcontractor), Decimal("240.00"))

    def test_a_draft_order_cannot_issue(self):
        order = self.subcontract_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.issue_components(self.warehouse)

    def test_it_needs_somewhere_to_send_them(self):
        order = self.subcontract_order(warehouse=False)
        with self.assertRaisesMessage(ValidationError, "subcontract warehouse"):
            order.issue_components(self.warehouse)

    def test_an_order_with_no_components_has_nothing_to_issue(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
            subcontract_warehouse=self.subcontractor,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("1"), unit_price=Decimal("5"),
        )
        order.confirm()
        with self.assertRaisesMessage(ValidationError, "no components to issue"):
            order.issue_components(self.warehouse)


class SubcontractReceiptTests(SubcontractTestCase):
    def test_the_finished_item_carries_the_component_cost(self):
        """Valuing it at the vendor's charge alone would report a part
        built from 32 of components as worth the 5 of labour."""
        order = self.subcontract_order("10", service="5")
        order.issue_components(self.warehouse)

        self.receive(order, "10")

        # frame 8 + two motors at 12 = 32, plus 5 of assembly = 37
        self.assertEqual(self.assembly.average_cost_at(self.warehouse), Decimal("37.0000"))
        self.assertEqual(self.assembly.stock_value_at(self.warehouse), Decimal("370.00"))

    def test_the_components_are_consumed(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.receive(order, "10")

        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("0"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("0"))

    def test_only_the_vendor_s_charge_reaches_the_ledger(self):
        """The component value has merely moved between items inside the
        same inventory account; posting it again would double it."""
        order = self.subcontract_order("10", service="5")
        order.issue_components(self.warehouse)
        before = self.balance(self.inventory)

        self.receive(order, "10")

        self.assertEqual(self.balance(self.inventory) - before, Decimal("50"))
        self.assertEqual(self.balance(self.grni), Decimal("-50"))

    def stock_value(self):
        return sum(
            item.stock_value_at(warehouse)
            for item in (self.frame, self.motor, self.assembly)
            for warehouse in (self.warehouse, self.subcontractor)
        )

    def test_the_books_and_the_warehouse_move_together(self):
        """Subcontracting shuffles value between items and warehouses; the
        only thing that may change the total is the vendor's charge."""
        order = self.subcontract_order("10", service="5")
        stock_before, ledger_before = self.stock_value(), self.balance(self.inventory)

        order.issue_components(self.warehouse)
        self.assertEqual(self.stock_value(), stock_before)
        self.assertEqual(self.balance(self.inventory), ledger_before)

        self.receive(order, "10")

        self.assertEqual(self.stock_value() - stock_before, Decimal("50.00"))
        self.assertEqual(self.balance(self.inventory) - ledger_before, Decimal("50.00"))

    def test_a_partial_receipt_consumes_its_share(self):
        order = self.subcontract_order("10")
        order.issue_components(self.warehouse)

        self.receive(order, "4")

        self.assertEqual(self.frame.on_hand_at(self.subcontractor), Decimal("6"))
        self.assertEqual(self.motor.on_hand_at(self.subcontractor), Decimal("12"))
        self.assertEqual(self.assembly.on_hand_at(self.warehouse), Decimal("4"))

    def test_receiving_without_issuing_is_refused(self):
        """The subcontractor cannot have used what they were never sent."""
        order = self.subcontract_order("10")
        with self.assertRaisesMessage(ValidationError, "is short"):
            self.receive(order, "10")

    def test_a_missing_subcontract_warehouse_is_refused(self):
        order = self.subcontract_order("10", warehouse=False)
        with self.assertRaisesMessage(ValidationError, "nowhere to be consumed from"):
            self.receive(order, "10")

    def test_an_ordinary_line_is_untouched(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.confirm()
        self.receive(order, "10")

        self.assertEqual(self.item.average_cost_at(self.warehouse), Decimal("5.0000"))
