from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item

from .models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine


class PurchasingTestCase(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="PART-1", name="Part", uom=self.uom)

        self.vendor = Party.objects.create(code="VEND-1", name="Supplier Co")
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

        self.payable = Account.objects.create(
            code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY
        )
        self.expense = Account.objects.create(
            code="5000", name="Cost of Goods Sold", account_type=AccountType.EXPENSE
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
