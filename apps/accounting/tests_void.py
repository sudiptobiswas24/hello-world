"""
Voiding a payment.

A bounced cheque is the commonest reason to void a receipt, and it used
to leave the ledger right and every document wrong: the invoice still
read as paid, so dunning never chased it, aging never showed it and the
customer's credit limit was quietly freed — while the ledger insisted
the money was still owed.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    PaymentTerms,
    UnitOfMeasure,
)
from apps.inventory.models import Item
from apps.sales.models import (
    DunningLevel,
    Invoice,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
    SettlementStatus,
    ar_aging,
    outstanding_balance,
    run_dunning,
)

from .models import Account, AccountType, JournalLine, Payment, PaymentDirection


class VoidTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.uom)
        self.ar = Account.objects.create(code="1100", name="AR", account_type=AccountType.ASSET)
        self.bank = Account.objects.create(
            code="1010", name="Bank", account_type=AccountType.ASSET
        )
        self.revenue = Account.objects.create(
            code="4000", name="Revenue", account_type=AccountType.INCOME
        )
        Company.objects.create(name="Test Co", base_currency=self.usd)
        terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        self.customer = Party.objects.create(
            code="C-1", name="Acme", default_currency=self.usd,
            payment_terms=terms, email="ap@acme.example",
        )
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)

    def invoice_of(self, amount="1000"):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 1, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal(amount), revenue_account=self.revenue,
        )
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 1, 1))
        invoice.post()
        return invoice

    def receipt(self, amount, on=datetime.date(2026, 1, 5)):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=on, amount=Decimal(amount), currency=self.usd,
            bank_account=self.bank, counterpart_account=self.ar,
        )
        payment.post()
        return payment

    def ledger(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class BouncedReceiptTests(VoidTestCase):
    def setUp(self):
        super().setUp()
        self.invoice = self.invoice_of("1000")
        self.payment = self.receipt("1000")
        InvoicePayment.objects.create(
            invoice=self.invoice, payment=self.payment, amount=Decimal("1000")
        )

    def test_the_document_agrees_with_the_ledger_again(self):
        self.payment.void(memo="Cheque bounced")

        self.assertEqual(self.invoice.amount_due(), Decimal("1000"))
        self.assertEqual(self.invoice.amount_due(), self.ledger(self.ar))

    def test_the_invoice_goes_back_to_unpaid(self):
        self.payment.void()
        self.assertEqual(self.invoice.settlement_status(), SettlementStatus.UNPAID)

    def test_it_is_chased_again(self):
        DunningLevel.objects.create(name="First", days_overdue=7)
        self.payment.void()

        run_dunning(as_of=datetime.date(2026, 6, 1), send=False)

        self.assertEqual(self.invoice.dunning_notices.count(), 1)

    def test_it_comes_back_onto_the_aging_report(self):
        self.payment.void()
        aging = ar_aging(as_of=datetime.date(2026, 6, 1))
        self.assertEqual(aging["90+"]["total"], Decimal("1000"))

    def test_it_counts_against_the_credit_limit_again(self):
        self.payment.void()
        self.assertEqual(outstanding_balance(self.customer), Decimal("1000"))

    def test_the_allocation_stays_as_history(self):
        """It records what the money was once believed to settle, which is
        worth keeping; what changes is that nothing counts it."""
        self.payment.void()
        self.assertEqual(self.invoice.payment_allocations.count(), 1)
        self.assertTrue(self.payment.is_voided())

    def test_a_voided_payment_cannot_be_allocated_again(self):
        second = self.invoice_of("500")
        self.payment.void()
        with self.assertRaisesMessage(ValidationError, "has been voided"):
            InvoicePayment.objects.create(
                invoice=second, payment=self.payment, amount=Decimal("500")
            )

    def test_it_cannot_be_voided_twice(self):
        self.payment.void()
        with self.assertRaisesMessage(ValidationError, "already been voided"):
            self.payment.void()

    def test_an_unposted_payment_cannot_be_voided(self):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 1, 5), amount=Decimal("10"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.ar,
        )
        with self.assertRaisesMessage(ValidationError, "Only a posted payment"):
            payment.void()

    def test_a_partial_void_still_leaves_other_payments_counting(self):
        second = self.receipt("400", on=datetime.date(2026, 1, 8))
        # the first already settled it in full, so release room first
        self.payment.void()
        InvoicePayment.objects.create(
            invoice=self.invoice, payment=second, amount=Decimal("400")
        )
        self.assertEqual(self.invoice.amount_due(), Decimal("600"))


class VoidedDisbursementTests(VoidTestCase):
    """The same hole existed on the purchase side."""

    def test_a_voided_disbursement_reopens_its_bill(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment

        payable = Account.objects.create(
            code="2000", name="AP", account_type=AccountType.LIABILITY
        )
        expense = Account.objects.create(
            code="5000", name="Purchases", account_type=AccountType.EXPENSE
        )
        vendor = Party.objects.create(code="V-1", name="Supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)

        bill = Bill.objects.create(
            vendor=vendor, bill_date=datetime.date(2026, 1, 1), payable_account=payable
        )
        BillLine.objects.create(
            bill=bill, description="Parts", quantity=Decimal("1"),
            unit_price=Decimal("500"), expense_account=expense,
        )
        bill.post()

        payment = Payment.objects.create(
            party=vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 10), amount=Decimal("500"),
            currency=self.usd, bank_account=self.bank, counterpart_account=payable,
        )
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("500"))
        self.assertEqual(bill.amount_due(), Decimal("0"))

        payment.void(memo="Transfer recalled")

        self.assertEqual(bill.amount_due(), Decimal("500"))
        self.assertEqual(-self.ledger(payable), Decimal("500"))
