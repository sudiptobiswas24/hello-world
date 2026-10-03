from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import Party, PartyRole, PartyRoleAssignment, UnitOfMeasure
from apps.inventory.models import Item

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine


class SalesTestCase(TestCase):
    def setUp(self):
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WIDGET-1", name="Widget", uom=self.uom)

        self.customer = Party.objects.create(code="CUST-1", name="Acme Co")
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

        self.receivable = Account.objects.create(
            code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
        )
        self.revenue = Account.objects.create(
            code="4000", name="Sales Revenue", account_type=AccountType.INCOME
        )

    def make_invoice(self, quantity=Decimal("2"), unit_price=Decimal("50")):
        invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_date="2026-01-01",
            receivable_account=self.receivable,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            item=self.item,
            description="Widgets",
            quantity=quantity,
            unit_price=unit_price,
            revenue_account=self.revenue,
        )
        return invoice


class CustomerRoleTests(SalesTestCase):
    def test_party_without_customer_role_is_rejected(self):
        vendor = Party.objects.create(code="VEND-1", name="Not A Customer")
        invoice = Invoice(
            customer=vendor, invoice_date="2026-01-01", receivable_account=self.receivable
        )
        with self.assertRaises(ValidationError):
            invoice.full_clean()

    def test_sales_order_requires_customer_role(self):
        vendor = Party.objects.create(code="VEND-2", name="Not A Customer Either")
        order = SalesOrder(customer=vendor, order_date="2026-01-01")
        with self.assertRaises(ValidationError):
            order.full_clean()


class SalesOrderTests(SalesTestCase):
    def test_total_sums_line_subtotals(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date="2026-01-01")
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("3"), unit_price=Decimal("10")
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"), unit_price=Decimal("5")
        )
        self.assertEqual(order.total(), Decimal("35"))


class InvoicePostingTests(SalesTestCase):
    def test_posting_creates_balanced_journal_entry(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        invoice.refresh_from_db()

        self.assertTrue(invoice.posted)
        entry = invoice.journal_entry
        self.assertTrue(entry.posted)
        self.assertEqual(entry.total_debit(), Decimal("100"))
        self.assertEqual(entry.total_credit(), Decimal("100"))

        ar_line = entry.lines.get(account=self.receivable)
        revenue_line = entry.lines.get(account=self.revenue)
        self.assertEqual(ar_line.debit, Decimal("100"))
        self.assertEqual(revenue_line.credit, Decimal("100"))

    def test_cannot_post_invoice_with_no_lines(self):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date="2026-01-01", receivable_account=self.receivable
        )
        with self.assertRaises(ValidationError):
            invoice.post()

    def test_cannot_post_twice(self):
        invoice = self.make_invoice()
        invoice.post()
        with self.assertRaises(ValidationError):
            invoice.post()


class InvoiceImmutabilityTests(SalesTestCase):
    def test_posted_invoice_cannot_be_edited(self):
        invoice = self.make_invoice()
        invoice.post()
        invoice.reference = "changed"
        with self.assertRaises(ValidationError):
            invoice.save()

    def test_posted_invoice_cannot_be_deleted(self):
        invoice = self.make_invoice()
        invoice.post()
        with self.assertRaises(ValidationError):
            invoice.delete()

    def test_line_on_posted_invoice_cannot_be_edited(self):
        invoice = self.make_invoice()
        invoice.post()
        line = invoice.lines.first()
        line.quantity = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()


class CreditNoteTests(SalesTestCase):
    def test_credit_note_reverses_original_journal_entry(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()

        credit_note = invoice.create_credit_note(memo="Customer returned goods")

        self.assertEqual(credit_note.credits, invoice)
        self.assertTrue(credit_note.posted)
        self.assertEqual(credit_note.journal_entry.reverses, invoice.journal_entry)

        cn_ar_line = credit_note.journal_entry.lines.get(account=self.receivable)
        cn_revenue_line = credit_note.journal_entry.lines.get(account=self.revenue)
        self.assertEqual(cn_ar_line.credit, Decimal("100"))
        self.assertEqual(cn_revenue_line.debit, Decimal("100"))

    def test_original_invoice_is_untouched_by_credit_note(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        original_journal_entry_id = invoice.journal_entry_id

        invoice.create_credit_note()
        invoice.refresh_from_db()

        self.assertEqual(invoice.journal_entry_id, original_journal_entry_id)
        self.assertTrue(invoice.journal_entry.posted)
        self.assertEqual(invoice.total(), Decimal("100"))

    def test_net_receivable_impact_is_zero_after_credit_note(self):
        invoice = self.make_invoice(Decimal("2"), Decimal("50"))
        invoice.post()
        invoice.create_credit_note()

        ar_lines = self.receivable.lines.all()
        debit_total = sum(line.debit for line in ar_lines)
        credit_total = sum(line.credit for line in ar_lines)
        self.assertEqual(debit_total, credit_total)

    def test_cannot_credit_an_unposted_invoice(self):
        invoice = self.make_invoice()
        with self.assertRaises(ValidationError):
            invoice.create_credit_note()

    def test_cannot_credit_a_credit_note(self):
        invoice = self.make_invoice()
        invoice.post()
        credit_note = invoice.create_credit_note()
        with self.assertRaises(ValidationError):
            credit_note.create_credit_note()
