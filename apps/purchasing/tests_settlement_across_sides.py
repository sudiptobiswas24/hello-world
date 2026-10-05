"""
A payment applied to documents: what it settles with, not only how much.

Two holes found by probing this period's money paths, both with every
allocation test green:

- A party that buys and sells: its receipt of 100 could be applied in
  full to its invoice and again in full to its debit note, because the
  invoice side counted only invoice applications and the bill side only
  bill ones. Both documents read settled on 100 received.
- A payment booked against one account applied to a document booked to
  another: the document read paid while its control account kept the
  whole balance.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import PartyRole, PartyRoleAssignment
from apps.sales.models import InvoicePayment

from .models import BillPayment
from .tests_drop_ship import DropShipTestCase


class SettlementTestCase(DropShipTestCase):
    def setUp(self):
        super().setUp()
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.VENDOR)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)

    def invoice(self):
        from apps.sales.models import InvoicePolicy

        sale = self.sales_order("10", "10", policy=InvoicePolicy.ORDERED)  # 100.00, billed on order
        invoice = sale.create_invoice(self.ar)
        invoice.post()
        return invoice

    def debit_note(self, account=None):
        order = self.make_order(quantity="10", price="10")
        order.vendor = self.customer
        order.save()
        self.receive(order, "10")
        account = account or self.payable
        bill = order.create_bill(account)
        bill.post()
        # Paid first, so the debit note is money the vendor owes back.
        paid = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 1, 20),
            amount=Decimal("100"), currency=self.usd, bank_account=self.bank, counterpart_account=account)
        paid.post()
        BillPayment.objects.create(bill=bill, payment=paid, amount=Decimal("100"))
        return bill.create_debit_note(memo="Short weight", quantities={bill.lines.get(): Decimal("10")})

    def receipt(self, amount, account):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 2, 1),
            amount=Decimal(amount), currency=self.usd, bank_account=self.bank, counterpart_account=account)
        payment.post()
        return payment


class OnePaymentIsAppliedOnceTests(SettlementTestCase):
    def test_a_receipt_spent_on_an_invoice_is_not_spent_again_on_a_debit_note(self):
        invoice = self.invoice()
        # Booked to one account on both sides, so only the amount can refuse it.
        note = self.debit_note(account=self.ar)
        money = self.receipt("100", self.ar)
        InvoicePayment.objects.create(invoice=invoice, payment=money, amount=Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "unallocated"):
            BillPayment.objects.create(bill=note, payment=money, amount=Decimal("100"))
        self.assertFalse(BillPayment.objects.filter(bill=note).exists())


class TheControlAccountMustMatchTests(SettlementTestCase):
    def test_money_booked_to_revenue_does_not_settle_an_invoice(self):
        invoice = self.invoice()
        cash_sale = self.receipt("100", self.revenue)
        with self.assertRaisesMessage(ValidationError, "both accounts would be left wrong"):
            InvoicePayment.objects.create(invoice=invoice, payment=cash_sale, amount=Decimal("100"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), Decimal("100.00"))

    def test_a_vendors_refund_booked_to_the_receivable_does_not_settle_its_debit_note(self):
        note = self.debit_note()
        refund = self.receipt("100", self.ar)
        with self.assertRaisesMessage(ValidationError, "both accounts would be left wrong"):
            BillPayment.objects.create(bill=note, payment=refund, amount=Decimal("100"))
        refund_to_payables = self.receipt("100", self.payable)
        BillPayment.objects.create(bill=note, payment=refund_to_payables, amount=Decimal("100"))
        note.refresh_from_db()
        self.assertEqual(note.amount_due(), Decimal("0.00"))
