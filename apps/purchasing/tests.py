from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item, Warehouse

from .models import Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine


class PurchasingTestCase(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="PART-1", name="Part", uom=self.uom)
        self.warehouse = Warehouse.objects.create(code="WH1", name="Main Warehouse")

        self.vendor = Party.objects.create(code="VEND-1", name="Supplier Co")
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

        self.payable = Account.objects.create(
            code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY
        )
        self.expense = Account.objects.create(
            code="5000", name="Cost of Goods Sold", account_type=AccountType.EXPENSE
        )
        self.inventory = Account.objects.create(
            code="1200", name="Inventory", account_type=AccountType.ASSET
        )
        self.grni = Account.objects.create(
            code="2150", name="Goods Received Not Invoiced", account_type=AccountType.LIABILITY
        )
        Company.objects.create(
            name="Test Co",
            default_inventory_account=self.inventory,
            default_cogs_account=self.expense,
            grni_account=self.grni,
        )

    def make_po_line(self, quantity=Decimal("10")):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date="2026-01-01")
        return PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=quantity, unit_price=Decimal("5")
        )

    def make_bill(self, quantity=Decimal("2"), unit_price=Decimal("40")):
        bill = Bill.objects.create(
            vendor=self.vendor,
            bill_date="2026-01-01",
            payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill,
            item=self.item,
            description="Parts",
            quantity=quantity,
            unit_price=unit_price,
            expense_account=self.expense,
        )
        return bill


class VendorRoleTests(PurchasingTestCase):
    def test_party_without_vendor_role_is_rejected(self):
        customer = Party.objects.create(code="CUST-1", name="Not A Vendor")
        bill = Bill(vendor=customer, bill_date="2026-01-01", payable_account=self.payable)
        with self.assertRaises(ValidationError):
            bill.full_clean()

    def test_purchase_order_requires_vendor_role(self):
        customer = Party.objects.create(code="CUST-2", name="Not A Vendor Either")
        order = PurchaseOrder(vendor=customer, order_date="2026-01-01")
        with self.assertRaises(ValidationError):
            order.full_clean()


class PurchaseOrderTests(PurchasingTestCase):
    def test_total_sums_line_subtotals(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date="2026-01-01")
        PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("4"), unit_price=Decimal("10")
        )
        self.assertEqual(order.total(), Decimal("40"))


class BillPostingTests(PurchasingTestCase):
    def test_posting_creates_balanced_journal_entry(self):
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()
        bill.refresh_from_db()

        self.assertTrue(bill.posted)
        entry = bill.journal_entry
        self.assertTrue(entry.posted)
        self.assertEqual(entry.total_debit(), Decimal("80"))
        self.assertEqual(entry.total_credit(), Decimal("80"))

        payable_line = entry.lines.get(account=self.payable)
        # A stocked item was already capitalised into Inventory on receipt,
        # so the bill clears the GRNI accrual rather than expensing again.
        grni_line = entry.lines.get(account=self.grni)
        self.assertEqual(payable_line.credit, Decimal("80"))
        self.assertEqual(grni_line.debit, Decimal("80"))

    def test_cannot_post_bill_with_no_lines(self):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date="2026-01-01", payable_account=self.payable
        )
        with self.assertRaises(ValidationError):
            bill.post()

    def test_cannot_post_twice(self):
        bill = self.make_bill()
        bill.post()
        with self.assertRaises(ValidationError):
            bill.post()


class BillImmutabilityTests(PurchasingTestCase):
    def test_posted_bill_cannot_be_edited(self):
        bill = self.make_bill()
        bill.post()
        bill.reference = "changed"
        with self.assertRaises(ValidationError):
            bill.save()

    def test_posted_bill_cannot_be_deleted(self):
        bill = self.make_bill()
        bill.post()
        with self.assertRaises(ValidationError):
            bill.delete()

    def test_line_on_posted_bill_cannot_be_edited(self):
        bill = self.make_bill()
        bill.post()
        line = bill.lines.first()
        line.quantity = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()


class DebitNoteTests(PurchasingTestCase):
    def test_debit_note_reverses_original_journal_entry(self):
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()

        debit_note = bill.create_debit_note(memo="Returned defective parts")

        self.assertEqual(debit_note.debits, bill)
        self.assertTrue(debit_note.posted)
        self.assertEqual(debit_note.journal_entry.reverses, bill.journal_entry)

        dn_payable_line = debit_note.journal_entry.lines.get(account=self.payable)
        dn_grni_line = debit_note.journal_entry.lines.get(account=self.grni)
        self.assertEqual(dn_payable_line.debit, Decimal("80"))
        self.assertEqual(dn_grni_line.credit, Decimal("80"))

    def test_original_bill_is_untouched_by_debit_note(self):
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()
        original_journal_entry_id = bill.journal_entry_id

        bill.create_debit_note()
        bill.refresh_from_db()

        self.assertEqual(bill.journal_entry_id, original_journal_entry_id)
        self.assertTrue(bill.journal_entry.posted)
        self.assertEqual(bill.total(), Decimal("80"))

    def test_net_payable_impact_is_zero_after_debit_note(self):
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()
        bill.create_debit_note()

        payable_lines = self.payable.lines.all()
        debit_total = sum(line.debit for line in payable_lines)
        credit_total = sum(line.credit for line in payable_lines)
        self.assertEqual(debit_total, credit_total)

    def test_cannot_debit_an_unposted_bill(self):
        bill = self.make_bill()
        with self.assertRaises(ValidationError):
            bill.create_debit_note()

    def test_cannot_debit_a_debit_note(self):
        bill = self.make_bill()
        bill.post()
        debit_note = bill.create_debit_note()
        with self.assertRaises(ValidationError):
            debit_note.create_debit_note()


class GoodsReceiptPostingTests(PurchasingTestCase):
    def test_posting_creates_a_stock_movement(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("4")
        )

        receipt.post()
        receipt.refresh_from_db()

        self.assertTrue(receipt.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("4"))
        self.assertEqual(order_line.quantity_received(), Decimal("4"))

    def test_cannot_post_receipt_with_no_lines(self):
        order_line = self.make_po_line()
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        with self.assertRaises(ValidationError):
            receipt.post()

    def test_cannot_exceed_ordered_quantity_across_receipts(self):
        order_line = self.make_po_line(Decimal("10"))

        first = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=first, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("7")
        )
        first.post()

        second = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-06")
        GoodsReceiptLine.objects.create(
            receipt=second, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("4")
        )
        with self.assertRaises(ValidationError):
            second.post()

    def test_partial_receipts_across_multiple_deliveries_accumulate(self):
        order_line = self.make_po_line(Decimal("10"))

        first = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=first, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("6")
        )
        first.post()

        second = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-06")
        GoodsReceiptLine.objects.create(
            receipt=second, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("4")
        )
        second.post()

        self.assertEqual(order_line.quantity_received(), Decimal("10"))
        self.assertTrue(order_line.is_fully_received())
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("10"))


class GoodsReceiptImmutabilityTests(PurchasingTestCase):
    def test_posted_receipt_cannot_be_edited(self):
        order_line = self.make_po_line()
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("2")
        )
        receipt.post()
        receipt.reference = "changed"
        with self.assertRaises(ValidationError):
            receipt.save()

    def test_posted_receipt_cannot_be_deleted(self):
        order_line = self.make_po_line()
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("2")
        )
        receipt.post()
        with self.assertRaises(ValidationError):
            receipt.delete()

    def test_line_on_posted_receipt_cannot_be_edited(self):
        order_line = self.make_po_line()
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("2")
        )
        receipt.post()
        line.quantity_received = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()


class GoodsReceiptReturnTests(PurchasingTestCase):
    def make_receipt(self, order_line, quantity=Decimal("6")):
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=quantity
        )
        receipt.post()
        return receipt

    def test_return_creates_offsetting_stock_movement(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("6"))

        return_receipt = receipt.create_return()

        self.assertEqual(return_receipt.reverses, receipt)
        self.assertTrue(return_receipt.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))

    def test_original_receipt_is_untouched_by_return(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("6"))

        receipt.create_return()
        receipt.refresh_from_db()

        self.assertTrue(receipt.posted)
        self.assertEqual(receipt.lines.first().quantity_received, Decimal("6"))

    def test_quantity_received_nets_to_zero_after_full_return(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("6"))
        receipt.create_return()

        self.assertEqual(order_line.quantity_received(), Decimal("0"))

    def test_can_reorder_ordered_quantity_after_a_return(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("10"))
        receipt.create_return()

        second = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-10")
        GoodsReceiptLine.objects.create(
            receipt=second, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("10")
        )
        second.post()  # should not raise: the returned quantity freed up room

        self.assertEqual(order_line.quantity_received(), Decimal("10"))

    def test_cannot_return_an_unposted_receipt(self):
        order_line = self.make_po_line()
        receipt = GoodsReceipt.objects.create(purchase_order=order_line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse, quantity_received=Decimal("2")
        )
        with self.assertRaises(ValidationError):
            receipt.create_return()

    def test_cannot_return_a_return(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("6"))
        return_receipt = receipt.create_return()
        with self.assertRaises(ValidationError):
            return_receipt.create_return()

    def test_cannot_return_the_same_receipt_twice(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = self.make_receipt(order_line, Decimal("6"))
        receipt.create_return()
        with self.assertRaises(ValidationError):
            receipt.create_return()


class NonStockedReceiptTests(PurchasingTestCase):
    """
    Regression: GoodsReceipt used to create a StockMovement for every line,
    including service items whose track_inventory is False — phantom stock
    for something that isn't stock.
    """

    def test_receiving_a_service_line_creates_no_stock_movement(self):
        from apps.inventory.models import StockMovement

        service = Item.objects.create(
            sku="SVC-INSTALL", name="Installation", uom=self.uom,
            item_type="service", track_inventory=False,
        )
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date="2026-01-01")
        order_line = PurchaseOrderLine.objects.create(
            order=order, item=service, uom=self.uom,
            quantity=Decimal("3"), unit_price=Decimal("100"),
        )
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse,
            quantity_received=Decimal("3"),
        )
        before = StockMovement.objects.count()

        receipt.post()

        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(order_line.quantity_received(), Decimal("3"))

    def test_stocked_lines_still_move_stock(self):
        order_line = self.make_po_line(Decimal("10"))
        receipt = GoodsReceipt.objects.create(
            purchase_order=order_line.order, receipt_date="2026-01-05"
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse,
            quantity_received=Decimal("4"),
        )
        receipt.post()
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("4"))


class BillPostingAccountTests(PurchasingTestCase):
    def test_a_service_line_expenses_directly(self):
        service = Item.objects.create(
            sku="SVC-1", name="Consulting", uom=self.uom,
            item_type="service", track_inventory=False,
        )
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date="2026-01-01", payable_account=self.payable
        )
        BillLine.objects.create(
            bill=bill, item=service, description="Consulting",
            quantity=Decimal("1"), unit_price=Decimal("500"), expense_account=self.expense,
        )
        bill.post()

        entry = bill.journal_entry
        self.assertEqual(entry.lines.get(account=self.expense).debit, Decimal("500"))
        self.assertFalse(entry.lines.filter(account=self.grni).exists())

    def test_a_stocked_line_clears_the_accrual(self):
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()
        entry = bill.journal_entry
        self.assertEqual(entry.lines.get(account=self.grni).debit, Decimal("80"))
        self.assertFalse(entry.lines.filter(account=self.expense).exists())
