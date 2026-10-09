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
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=quantity, unit_price=Decimal("5")
        )
        order.confirm()
        return line

    def make_order_line(self, quantity=Decimal("2")):
        return self.make_po_line(quantity)

    def receive(self, order_line, quantity):
        receipt = GoodsReceipt.objects.create(
            purchase_order=order_line.order, receipt_date="2026-01-05"
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.warehouse,
            quantity_received=quantity,
        )
        receipt.post()
        return receipt

    def make_bill(self, quantity=Decimal("2"), unit_price=Decimal("40"), order_line=None):
        bill = Bill.objects.create(
            vendor=self.vendor,
            bill_date="2026-01-01",
            payable_account=self.payable,
        )
        BillLine.objects.create(
            bill=bill,
            order_line=order_line,
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
        # Nothing was received against this bill, so there is no accrual to
        # clear and the cost expenses. A bill does not create stock.
        expense_line = entry.lines.get(account=self.expense)
        self.assertEqual(payable_line.credit, Decimal("80"))
        self.assertEqual(expense_line.debit, Decimal("80"))

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
        dn_expense_line = debit_note.journal_entry.lines.get(account=self.expense)
        self.assertEqual(dn_payable_line.debit, Decimal("80"))
        self.assertEqual(dn_expense_line.credit, Decimal("80"))

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
        order.confirm()
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

    def test_a_stocked_line_clears_the_accrual_a_receipt_made(self):
        order_line = self.make_order_line(Decimal("2"))
        self.receive(order_line, Decimal("2"))
        # Naming the order line it pays: without it the bill is refused (O92), as the order's own
        # bill would then pay for the same goods again.
        with self.assertRaisesMessage(ValidationError, "Name the order line this bill pays"):
            self.make_bill(Decimal("2"), Decimal("5")).post()
        bill = self.make_bill(Decimal("2"), Decimal("5"), order_line=order_line)
        bill.post()

        entry = bill.journal_entry
        self.assertEqual(entry.lines.get(account=self.grni).debit, Decimal("10"))
        self.assertFalse(entry.lines.filter(account=self.expense).exists())

    def test_a_stocked_line_with_no_receipt_behind_it_expenses(self):
        """Stock is created by receiving it, never by being billed for it.
        Debiting GRNI here would leave a balance nothing ever offsets."""
        bill = self.make_bill(Decimal("2"), Decimal("40"))
        bill.post()

        entry = bill.journal_entry
        self.assertEqual(entry.lines.get(account=self.expense).debit, Decimal("80"))
        self.assertFalse(entry.lines.filter(account=self.grni).exists())


import datetime  # noqa: E402

from django.test import TransactionTestCase as _MigrationTestCase  # noqa: E402
from django.test import tag as _tag  # noqa: E402


@_tag("migration")
class SupplierNoteKeyMigrationTests(_MigrationTestCase):
    """
    purchasing 0063 keys the supplier credit notes the old exact constraint
    allowed (O177, review_stat2 #1). "CN-9" and "cn-9" of one vendor stood
    side by side; adding the new constraint over them crashed the migration.
    """

    P62 = [("purchasing", "0062_debit_note_keeps_the_suppliers_credit_note")]
    P63 = [("purchasing", "0063_a_supplier_credit_note_is_one_in_its_year")]

    def migrate(self, target):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor

    def setUp(self):
        self.addCleanup(lambda: self.migrate(self.migrate([]).loader.graph.leaf_nodes()))
        executor = self.migrate(self.P62)
        self.old = executor.loader.project_state(self.P62).apps
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        self.v1 = now.get_model("core", "Party").objects.create(code="V1", name="V1")
        self.v2 = now.get_model("core", "Party").objects.create(code="V2", name="V2")
        self.pay = now.get_model("accounting", "Account").objects.create(code="2000", name="AP",
                                                                         account_type="liability")
        self.made = 0

    def bill(self, vendor, day, number="", note_date=None, debits=None):
        self.made += 1
        return self.old.get_model("purchasing", "Bill").objects.create(
            vendor_id=vendor.pk, bill_date=day, payable_account_id=self.pay.pk, number=f"B{self.made}",
            debits_id=debits.pk if debits else None, supplier_note_number=number, supplier_note_date=note_date)

    def forward(self):
        import contextlib
        import io

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            executor = self.migrate(self.P63)
        Bill = executor.loader.project_state(self.P63).apps.get_model("purchasing", "Bill")
        return Bill, printed.getvalue()

    def test_keys_each_note_in_its_year(self):
        D = datetime.date
        orig1, orig2 = self.bill(self.v1, D(2026, 5, 1)), self.bill(self.v2, D(2026, 5, 1))
        rows = {
            "dated_fy26": self.bill(self.v1, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig1),
            "same_no_fy27": self.bill(self.v1, D(2027, 5, 2), "cn-9", D(2027, 5, 1), orig1),
            "undated_feb": self.bill(self.v1, D(2027, 2, 10), "CN/007", None, orig1),
            "nonum": self.bill(self.v1, D(2026, 6, 21), "", None, orig1),
            "other_vendor": self.bill(self.v2, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig2),
            "31mar": self.bill(self.v1, D(2027, 4, 1), "X 5", D(2027, 3, 31), orig1),
            "1apr": self.bill(self.v1, D(2027, 4, 1), "X 6", D(2027, 4, 1), orig1),
        }
        Bill, printed = self.forward()
        got = {name: Bill.objects.get(pk=row.pk).supplier_note_key for name, row in rows.items()}
        self.assertEqual(got, {"dated_fy26": "2026:CN-9", "same_no_fy27": "2027:CN-9", "undated_feb": "2026:CN/007",
                               "nonum": "", "other_vendor": "2026:CN-9", "31mar": "2026:X 5", "1apr": "2027:X 6"})
        self.assertEqual((Bill.objects.get(pk=orig1.pk).supplier_note_key, printed), ("", ""))

    def test_old_rows_one_under_the_key_are_reported_not_merged(self):
        from django.db import IntegrityError, transaction

        D = datetime.date
        orig = self.bill(self.v1, D(2026, 5, 1))
        first = self.bill(self.v1, D(2026, 6, 20), "CN-9", D(2026, 6, 10), orig)
        second = self.bill(self.v1, D(2026, 6, 21), "cn-9", D(2026, 6, 11), orig)
        third = self.bill(self.v1, D(2026, 6, 22), " CN-9 ", D(2026, 6, 12), orig)
        other = self.bill(self.v1, D(2026, 6, 23), "CN/009", D(2026, 6, 12), orig)
        spare = self.bill(self.v1, D(2026, 6, 24), "", None, orig)
        Bill, printed = self.forward()
        got = [(row.supplier_note_number, row.supplier_note_key)
               for row in Bill.objects.filter(pk__in=[first.pk, second.pk, third.pk, other.pk]).order_by("pk")]
        self.assertEqual(got, [("CN-9", "2026:CN-9"), ("cn-9", ""), (" CN-9 ", ""), ("CN/009", "2026:CN/009")])
        self.assertIn("B3 answers supplier credit note 'cn-9', which is 'CN-9' on B2", printed)
        self.assertIn("B4 answers supplier credit note ' CN-9 ', which is 'CN-9' on B2", printed)
        # The note that kept the key is still one: a further CN-9 of that vendor and year is refused.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bill.objects.filter(pk=spare.pk).update(supplier_note_key="2026:CN-9")
