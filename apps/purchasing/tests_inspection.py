"""
Receipt inspection.

Goods used to arrive and land directly in sellable stock, so anything
faulty was shippable to a customer the moment it came off the lorry.
Quarantine is not "not yet received" — the goods are owned, valued and
on the books. They are simply not yet cleared.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Warehouse
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    ReceiptInspection,
    vendor_performance,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class InspectionTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.quarantine = Warehouse.objects.create(
            code="QA", name="Inspection bay", is_quarantine=True
        )
        self.ppv = Account.objects.create(
            code="5900", name="PPV", account_type=AccountType.EXPENSE
        )
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.purchase_price_variance_account = self.ppv
        company.save()

    def inspected_receipt(self, quantity="10", price="5"):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            inspect_on_receipt=True,
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line, warehouse=self.quarantine,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        return order, receipt

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class QuarantineTests(InspectionTestCase):
    def test_received_goods_are_owned_and_valued(self):
        """Quarantine is not 'not yet received'. The purchase happened."""
        order, receipt = self.inspected_receipt("10", "5")

        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("10"))
        self.assertEqual(self.item.stock_value_at(self.quarantine), Decimal("50.00"))
        self.assertEqual(self.balance(self.inventory), Decimal("50"))
        self.assertEqual(self.balance(self.grni), Decimal("-50"))

    def test_but_they_are_not_available(self):
        order, receipt = self.inspected_receipt("10", "5")
        self.assertEqual(self.item.available_at(self.quarantine), 0)
        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("10"))

    def test_an_ordinary_warehouse_is_unaffected(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("10"))

    def test_the_receipt_reports_what_is_waiting(self):
        order, receipt = self.inspected_receipt("10", "5")
        waiting = receipt.awaiting_inspection()
        self.assertEqual(list(waiting.values()), [Decimal("10")])


class ShippingFromQuarantineTests(InspectionTestCase):
    def test_nothing_ships_out_of_quarantine(self):
        """Nothing else stops a picker choosing that warehouse, so the
        refusal lives where the stock actually moves."""
        order, receipt = self.inspected_receipt("10", "5")
        customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 1, 6), currency=self.usd
        )
        sale_line = SalesOrderLine.objects.create(
            order=sale, item=self.item, uom=self.uom, quantity=Decimal("5"),
            unit_price=Decimal("9"), revenue_account=revenue,
        )
        sale.confirm()
        delivery = Delivery.objects.create(
            sales_order=sale, delivery_date=datetime.date(2026, 1, 7)
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=sale_line,
            warehouse=self.quarantine, quantity_shipped=Decimal("5"),
        )

        with self.assertRaisesMessage(ValidationError, "awaiting inspection"):
            delivery.post()


class AcceptTests(InspectionTestCase):
    def test_accepting_moves_the_goods_without_buying_them_again(self):
        """A transfer, not a receipt. Receiving them again would book the
        purchase twice."""
        order, receipt = self.inspected_receipt("10", "5")
        before = self.balance(self.inventory)

        receipt.accept(self.warehouse)

        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("0"))
        self.assertEqual(self.item.available_at(self.warehouse), Decimal("10"))
        self.assertEqual(self.balance(self.inventory), before)
        self.assertEqual(self.balance(self.grni), Decimal("-50"))

    def test_the_value_goes_with_them(self):
        order, receipt = self.inspected_receipt("10", "5")
        receipt.accept(self.warehouse)
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("50.00"))

    def test_part_can_be_accepted(self):
        order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()

        receipt.accept(self.warehouse, quantities={line: Decimal("6")})

        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("4"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("6"))
        self.assertEqual(line.quantity_uninspected(), Decimal("4"))

    def test_the_same_goods_cannot_be_decided_twice(self):
        order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()
        receipt.accept(self.warehouse, quantities={line: Decimal("6")})

        with self.assertRaisesMessage(ValidationError, "awaiting inspection"):
            receipt.accept(self.warehouse, quantities={line: Decimal("6")})

    def test_accepting_into_quarantine_clears_nothing(self):
        order, receipt = self.inspected_receipt("10", "5")
        second = Warehouse.objects.create(code="QA2", name="Other bay", is_quarantine=True)
        with self.assertRaisesMessage(ValidationError, "clears nothing"):
            receipt.accept(second)

    def test_the_decision_is_recorded(self):
        order, receipt = self.inspected_receipt("10", "5")
        receipt.accept(self.warehouse)

        inspection = ReceiptInspection.objects.get()
        self.assertTrue(inspection.accepted)
        self.assertEqual(inspection.quantity, Decimal("10"))
        self.assertEqual(inspection.warehouse, self.warehouse)


class RejectTests(InspectionTestCase):
    def test_rejecting_sends_them_back(self):
        order, receipt = self.inspected_receipt("10", "5")

        returned = receipt.reject(note="Wrong grade")

        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("0"))
        self.assertEqual(order.lines.first().quantity_received(), Decimal("0"))
        self.assertTrue(returned.posted)

    def test_it_records_the_failure_as_well_as_the_return(self):
        """Two facts: that they failed, which is vendor performance, and
        that they left, which is stock and money."""
        order, receipt = self.inspected_receipt("10", "5")
        receipt.reject(note="Wrong grade")

        inspection = ReceiptInspection.objects.get()
        self.assertFalse(inspection.accepted)
        self.assertEqual(inspection.note, "Wrong grade")

    def test_rejecting_billed_goods_debits_the_bill(self):
        order, receipt = self.inspected_receipt("10", "5")
        bill = order.create_bill(self.payable)
        bill.post()

        receipt.reject()

        self.assertEqual(bill.amount_due(), Decimal("0"))

    def test_part_can_be_rejected_and_the_rest_accepted(self):
        order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()

        receipt.reject(quantities={line: Decimal("3")})
        receipt.accept(self.warehouse, quantities={line: Decimal("7")})

        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("7"))
        self.assertEqual(self.item.on_hand_at(self.quarantine), Decimal("0"))
        self.assertEqual(order.lines.first().quantity_received(), Decimal("7"))

    def test_deciding_more_than_arrived_is_refused(self):
        order, receipt = self.inspected_receipt("10", "5")
        line = receipt.lines.get()
        with self.assertRaisesMessage(ValidationError, "awaiting inspection"):
            receipt.accept(self.warehouse, quantities={line: Decimal("11")})

    def test_a_receipt_with_nothing_in_quarantine_has_nothing_to_decide(self):
        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        with self.assertRaisesMessage(ValidationError, "nothing awaiting inspection"):
            receipt.accept(self.warehouse)
