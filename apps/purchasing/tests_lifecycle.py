"""
Purchase order lifecycle and document numbering.

Before this, every purchasing document identified itself by primary key
— BILL-7, GR-12 — which is not a document number: it is not sequential
per year, it leaks how many rows the table has, and a vendor cannot
quote it back at you. Orders also had a status field that nothing ever
set, so goods could be received against an order nobody had agreed to.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    ExchangeRate,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item, Warehouse

from .models import (
    Bill,
    BillLine,
    FulfilmentStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    OrderStatus,
    PurchaseOrder,
    PurchaseOrderLine,
)


class PurchasingLifecycleTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main")

        self.payable = Account.objects.create(
            code="2000", name="AP", account_type=AccountType.LIABILITY
        )
        self.expense = Account.objects.create(
            code="5000", name="Purchases", account_type=AccountType.EXPENSE
        )
        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory, grni_account=self.grni,
        )
        self.terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd, payment_terms=self.terms
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def make_order(self, quantity="10", price="5", confirm=True):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom,
            quantity=Decimal(quantity), unit_price=Decimal(price),
        )
        if confirm:
            order.confirm()
        return order

    def receive(self, order, quantity):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal(quantity),
        )
        receipt.post()
        return receipt

    def make_bill(self, quantity="10", price="5"):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal(quantity),
            unit_price=Decimal(price), expense_account=self.expense,
        )
        return bill


class OrderLifecycleTests(PurchasingLifecycleTestCase):
    def test_a_draft_order_has_no_number(self):
        order = self.make_order(confirm=False)
        self.assertEqual(order.number, "")
        self.assertIn("PO-draft", str(order))

    def test_confirming_assigns_a_sequential_number(self):
        first = self.make_order()
        second = self.make_order()
        self.assertEqual(first.number, "PO-2026-00001")
        self.assertEqual(second.number, "PO-2026-00002")

    def test_two_drafts_can_coexist(self):
        """Both carry an empty number; a plain unique index would collide."""
        self.make_order(confirm=False)
        self.make_order(confirm=False)
        self.assertEqual(PurchaseOrder.objects.filter(number="").count(), 2)

    def test_an_empty_order_cannot_be_confirmed(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        with self.assertRaisesMessage(ValidationError, "no lines"):
            order.confirm()

    def test_it_cannot_be_confirmed_twice(self):
        order = self.make_order()
        with self.assertRaisesMessage(ValidationError, "already confirmed"):
            order.confirm()

    def test_a_cancelled_order_cannot_be_confirmed(self):
        order = self.make_order(confirm=False)
        order.cancel()
        with self.assertRaisesMessage(ValidationError, "cancelled order cannot be confirmed"):
            order.confirm()

    def test_an_order_with_goods_in_cannot_be_cancelled(self):
        order = self.make_order()
        self.receive(order, "4")
        with self.assertRaisesMessage(ValidationError, "cannot be cancelled"):
            order.cancel()

    def test_the_currency_defaults_to_the_vendor(self):
        order = self.make_order()
        self.assertEqual(order.currency, self.usd)

    def test_receipt_status_follows_the_lines(self):
        order = self.make_order("10")
        self.assertEqual(order.receipt_status(), FulfilmentStatus.NONE)
        self.receive(order, "4")
        self.assertEqual(order.receipt_status(), FulfilmentStatus.PARTIAL)
        self.receive(order, "6")
        self.assertEqual(order.receipt_status(), FulfilmentStatus.FULL)


class ReceiptGateTests(PurchasingLifecycleTestCase):
    def test_goods_cannot_be_received_against_a_draft_order(self):
        """Receiving books stock and a GRNI liability for goods nobody
        agreed to buy."""
        order = self.make_order(confirm=False)
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal("4"),
        )
        with self.assertRaisesMessage(ValidationError, "confirm it before receiving"):
            receipt.post()

    def test_goods_cannot_be_received_against_a_cancelled_order(self):
        order = self.make_order()
        order.cancel()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order.lines.first(),
            warehouse=self.warehouse, quantity_received=Decimal("4"),
        )
        with self.assertRaisesMessage(ValidationError, "confirm it before receiving"):
            receipt.post()

    def test_a_return_is_allowed_against_a_cancelled_order(self):
        """Calling the order off is exactly when the goods go back."""
        order = self.make_order()
        receipt = self.receive(order, "4")
        order.status = OrderStatus.CANCELLED
        order.save(update_fields=["status"])

        returned = receipt.create_return()

        self.assertTrue(returned.posted)
        self.assertEqual(order.lines.first().quantity_received(), Decimal("0"))


class NumberingTests(PurchasingLifecycleTestCase):
    def test_receipts_and_returns_number_separately(self):
        order = self.make_order()
        receipt = self.receive(order, "10")
        returned = receipt.create_return()

        self.assertEqual(receipt.number, "GRN-2026-00001")
        self.assertEqual(returned.number, "PRTN-2026-00001")

    def test_bills_and_debit_notes_number_separately(self):
        bill = self.make_bill()
        bill.post()
        note = bill.create_debit_note()

        self.assertEqual(bill.number, "BILL-2026-00001")
        self.assertEqual(note.number, "DN-2026-00001")

    def test_the_number_reaches_the_ledger(self):
        bill = self.make_bill()
        bill.post()
        self.assertEqual(bill.journal_entry.memo, f"Bill {bill.number} from {self.vendor}")

    def test_a_zero_value_bill_is_refused(self):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal("1"),
            unit_price=Decimal("0"), expense_account=self.expense,
        )
        with self.assertRaisesMessage(ValidationError, "no value to post"):
            bill.post()


class BillTermsAndCurrencyTests(PurchasingLifecycleTestCase):
    def test_the_due_date_comes_from_the_vendor_terms(self):
        bill = self.make_bill()
        bill.post()
        self.assertEqual(bill.due_date, datetime.date(2026, 2, 9))

    def test_a_foreign_bill_posts_at_the_rate_on_its_date(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.2"), valid_from=datetime.date(2026, 1, 1)
        )
        self.vendor.default_currency = eur
        self.vendor.save()

        bill = self.make_bill("10", "5")
        bill.post()

        self.assertEqual(bill.exchange_rate, Decimal("1.2"))
        self.assertEqual(bill.journal_entry.lines.get(account=self.payable).credit,
                         Decimal("60.00"))

    def test_a_debit_note_uses_the_rate_the_bill_was_booked_at(self):
        """Correcting an old foreign bill must not book an FX gain."""
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.2"), valid_from=datetime.date(2026, 1, 1)
        )
        self.vendor.default_currency = eur
        self.vendor.save()
        bill = self.make_bill("10", "5")
        bill.post()

        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.9"), valid_from=datetime.date(2026, 6, 1)
        )
        note = bill.create_debit_note()

        self.assertEqual(note.exchange_rate, Decimal("1.2"))
        self.assertEqual(
            note.journal_entry.lines.get(account=self.payable).debit, Decimal("60.00")
        )
