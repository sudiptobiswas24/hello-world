"""Payments applied to invoices, settlement status, and AR aging."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import (
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item

from .models import Invoice, InvoiceLine, InvoicePayment, SettlementStatus, ar_aging


class SettlementTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="each", name="Each")
        self.item = Item.objects.create(sku="WDG-1", name="Widget", uom=self.uom)

        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )

        self.net30 = PaymentTerms.objects.create(code="NET30", name="Net 30", net_days=30)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd, payment_terms=self.net30
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def make_invoice(self, amount="100", invoice_date=datetime.date(2026, 3, 1), post=True):
        invoice = Invoice.objects.create(
            customer=self.customer, invoice_date=invoice_date, receivable_account=self.ar
        )
        InvoiceLine.objects.create(
            invoice=invoice, item=self.item, description="Widgets",
            quantity=Decimal("1"), unit_price=Decimal(amount), revenue_account=self.revenue,
        )
        if post:
            invoice.post()
        return invoice

    def make_payment(self, amount="100", payment_date=datetime.date(2026, 3, 10), post=True,
                     party=None, direction=PaymentDirection.RECEIPT):
        payment = Payment.objects.create(
            party=party or self.customer,
            direction=direction,
            payment_date=payment_date,
            amount=Decimal(amount),
            bank_account=self.bank,
            counterpart_account=self.ar,
        )
        if post:
            payment.post()
        return payment


class PaymentPostingTests(SettlementTestCase):
    def test_receipt_posts_debit_bank_credit_receivable(self):
        payment = self.make_payment("250")
        entry = payment.journal_entry
        self.assertEqual(entry.lines.get(account=self.bank).debit, Decimal("250.00"))
        self.assertEqual(entry.lines.get(account=self.ar).credit, Decimal("250.00"))
        self.assertTrue(entry.posted)

    def test_disbursement_reverses_the_direction(self):
        payable = Account.objects.create(
            code="2000", name="AP", account_type=AccountType.LIABILITY
        )
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal("80"),
            bank_account=self.bank, counterpart_account=payable,
        )
        payment.post()
        entry = payment.journal_entry
        self.assertEqual(entry.lines.get(account=payable).debit, Decimal("80.00"))
        self.assertEqual(entry.lines.get(account=self.bank).credit, Decimal("80.00"))

    def test_posting_assigns_a_sequence_number(self):
        payment = self.make_payment()
        self.assertEqual(payment.number, "PAY-2026-00001")

    def test_posted_payment_is_immutable(self):
        payment = self.make_payment()
        payment.memo = "changed"
        with self.assertRaises(ValidationError):
            payment.save()

    def test_posted_payment_cannot_be_deleted(self):
        payment = self.make_payment()
        with self.assertRaises(ValidationError):
            payment.delete()

    def test_void_reverses_the_entry(self):
        payment = self.make_payment("100")
        reversal = payment.void()
        self.assertEqual(reversal.reverses, payment.journal_entry)
        self.assertTrue(payment.is_voided())

    def test_cannot_void_twice(self):
        payment = self.make_payment()
        payment.void()
        with self.assertRaises(ValidationError):
            payment.void()

    def test_cannot_void_an_unposted_payment(self):
        payment = self.make_payment(post=False)
        with self.assertRaises(ValidationError):
            payment.void()


class AllocationTests(SettlementTestCase):
    def test_full_payment_settles_the_invoice(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("100")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

        self.assertEqual(invoice.amount_paid(), Decimal("100.00"))
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PAID)

    def test_partial_payment_leaves_a_balance(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("40")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("40"))

        self.assertEqual(invoice.amount_due(), Decimal("60.00"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PARTIAL)

    def test_unpaid_posted_invoice(self):
        invoice = self.make_invoice("100")
        self.assertEqual(invoice.settlement_status(), SettlementStatus.UNPAID)

    def test_draft_invoice_is_not_settleable(self):
        invoice = self.make_invoice("100", post=False)
        self.assertEqual(invoice.settlement_status(), SettlementStatus.DRAFT)
        payment = self.make_payment("100")
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

    def test_cannot_allocate_more_than_the_payment_holds(self):
        invoice = self.make_invoice("500")
        payment = self.make_payment("100")
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("200"))

    def test_cannot_allocate_more_than_the_invoice_owes(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("500")
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("150"))

    def test_one_payment_can_settle_several_invoices(self):
        first = self.make_invoice("60")
        second = self.make_invoice("40")
        payment = self.make_payment("100")

        InvoicePayment.objects.create(invoice=first, payment=payment, amount=Decimal("60"))
        InvoicePayment.objects.create(invoice=second, payment=payment, amount=Decimal("40"))

        self.assertEqual(first.settlement_status(), SettlementStatus.PAID)
        self.assertEqual(second.settlement_status(), SettlementStatus.PAID)
        self.assertEqual(InvoicePayment.unallocated_for(payment), Decimal("0"))

    def test_over_allocating_across_invoices_is_caught(self):
        first = self.make_invoice("80")
        second = self.make_invoice("80")
        payment = self.make_payment("100")
        InvoicePayment.objects.create(invoice=first, payment=payment, amount=Decimal("80"))
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=second, payment=payment, amount=Decimal("80"))

    def test_several_payments_can_settle_one_invoice(self):
        invoice = self.make_invoice("100")
        InvoicePayment.objects.create(
            invoice=invoice, payment=self.make_payment("30"), amount=Decimal("30")
        )
        InvoicePayment.objects.create(
            invoice=invoice, payment=self.make_payment("70"), amount=Decimal("70")
        )
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))

    def test_unposted_payment_cannot_be_allocated(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("100", post=False)
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

    def test_payment_from_a_different_party_is_rejected(self):
        other = Party.objects.create(code="C-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        invoice = self.make_invoice("100")
        payment = self.make_payment("100", party=other)
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

    def test_a_disbursement_cannot_settle_a_customer_invoice(self):
        invoice = self.make_invoice("100")
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 3, 10), amount=Decimal("100"),
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        with self.assertRaises(ValidationError):
            InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

    def test_unallocated_amount_tracks_applications(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("100")
        self.assertEqual(InvoicePayment.unallocated_for(payment), Decimal("100"))
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("35"))
        self.assertEqual(InvoicePayment.unallocated_for(payment), Decimal("65"))


class CreditNoteSettlementTests(SettlementTestCase):
    def test_credit_note_reduces_what_the_customer_owes(self):
        invoice = self.make_invoice("100")
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))

        invoice.create_credit_note(memo="Goods returned")

        self.assertEqual(invoice.amount_credited(), Decimal("100.00"))
        self.assertEqual(invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PAID)

    def test_credit_note_and_partial_payment_combine(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("40")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("40"))
        self.assertEqual(invoice.amount_due(), Decimal("60.00"))

        invoice.create_credit_note()
        self.assertEqual(invoice.amount_due(), Decimal("-40.00"))

    def test_credit_notes_are_excluded_from_aging(self):
        invoice = self.make_invoice("100")
        invoice.create_credit_note()
        aging = ar_aging(as_of=datetime.date(2026, 6, 1))
        total = sum(bucket["total"] for bucket in aging.values())
        self.assertEqual(total, Decimal("0"))


class OverdueTests(SettlementTestCase):
    def test_invoice_is_not_overdue_before_its_due_date(self):
        invoice = self.make_invoice("100")  # due 2026-03-31
        self.assertFalse(invoice.is_overdue(as_of=datetime.date(2026, 3, 15)))

    def test_invoice_is_overdue_after_its_due_date(self):
        invoice = self.make_invoice("100")
        self.assertTrue(invoice.is_overdue(as_of=datetime.date(2026, 4, 10)))
        self.assertEqual(invoice.days_overdue(as_of=datetime.date(2026, 4, 10)), 10)

    def test_a_paid_invoice_is_never_overdue(self):
        invoice = self.make_invoice("100")
        payment = self.make_payment("100")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))
        self.assertFalse(invoice.is_overdue(as_of=datetime.date(2026, 12, 1)))


class ArAgingTests(SettlementTestCase):
    def test_buckets_by_days_overdue(self):
        self.make_invoice("100", invoice_date=datetime.date(2026, 5, 20))  # due 06-19: current
        self.make_invoice("200", invoice_date=datetime.date(2026, 4, 20))  # due 05-20: 16 days
        self.make_invoice("300", invoice_date=datetime.date(2026, 3, 10))  # due 04-09: 57 days
        self.make_invoice("400", invoice_date=datetime.date(2026, 1, 1))   # due 01-31: 126 days

        aging = ar_aging(as_of=datetime.date(2026, 6, 5))

        self.assertEqual(aging["current"]["total"], Decimal("100.00"))
        self.assertEqual(aging["1-30"]["total"], Decimal("200.00"))
        self.assertEqual(aging["31-60"]["total"], Decimal("300.00"))
        self.assertEqual(aging["90+"]["total"], Decimal("400.00"))

    def test_settled_invoices_drop_out_of_aging(self):
        invoice = self.make_invoice("100", invoice_date=datetime.date(2026, 1, 1))
        payment = self.make_payment("100")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("100"))

        aging = ar_aging(as_of=datetime.date(2026, 6, 5))
        self.assertEqual(sum(bucket["total"] for bucket in aging.values()), Decimal("0"))

    def test_partially_paid_invoice_ages_only_its_balance(self):
        invoice = self.make_invoice("100", invoice_date=datetime.date(2026, 1, 1))
        payment = self.make_payment("70")
        InvoicePayment.objects.create(invoice=invoice, payment=payment, amount=Decimal("70"))

        aging = ar_aging(as_of=datetime.date(2026, 6, 5))
        self.assertEqual(aging["90+"]["total"], Decimal("30.00"))

    def test_draft_invoices_are_not_aged(self):
        self.make_invoice("100", invoice_date=datetime.date(2026, 1, 1), post=False)
        aging = ar_aging(as_of=datetime.date(2026, 6, 5))
        self.assertEqual(sum(bucket["count"] for bucket in aging.values()), 0)
