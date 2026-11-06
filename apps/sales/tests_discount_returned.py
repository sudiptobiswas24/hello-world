"""
A discount for paying early, and the payment that earned it returned.

  1,000 on 2/10 net 30: 980 paid on the 8th of March and 20 discounted.
  The cheque comes back on the 20th: the 20 is withdrawn that day, the
  customer owes 1,000 as receivables say, and the statement shows it.
  Paid in two, 500 and 480, the 480 returned: the discount goes and 500
  is owed. The discount is taken back once, however the return is asked.
  Credited a sack before the cheque came back: only what still stands of
  it is withdrawn. Paid again less the discount and then credited in
  full: the 980 comes back and the note undoes none of the discount.
  Either way the documents foot to the ledger.

The discount stood after its payment bounced, and the invoice read 980
owed for a sale the customer had not paid early at all.
"""

import datetime
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import JournalLine

from .models import Invoice, customer_statement, outstanding_balance
from .tests_base import SalesTestCase

PAID, RETURNED = datetime.date(2026, 3, 8), datetime.date(2026, 3, 20)


class DiscountReturnedTests(SalesTestCase):
    def discounted(self, *amounts):
        invoice = self.bill(self.make_order("10", "100"))
        receipts = [self.receipt(amount, on=PAID) for amount in amounts]
        for receipt, amount in zip(receipts, amounts):
            self.allocate(receipt, invoice, amount)
        invoice.apply_settlement_discount(on_date=PAID)
        return invoice, receipts

    def receivable(self):
        lines = JournalLine.objects.filter(account=self.ar, party=self.customer, entry__posted=True)
        return lines.aggregate(owed=Sum("debit") - Sum("credit"))["owed"]

    def test_the_discount_goes_back_with_the_cheque(self):
        invoice, (cheque,) = self.discounted("980")
        cheque.void(memo="Returned unpaid", on_date=RETURNED)
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.amount_due(), invoice.settlement_discount_withdrawn), (Decimal("1000.00"), Decimal("20.00")))
        self.assertEqual(invoice.settlement_discount_withdrawal_entry.date, RETURNED)
        self.assertEqual((self.receivable(), self.balance(self.discount_account)), (Decimal("1000.00"), Decimal("0")))
        statement = customer_statement(self.customer, as_of=datetime.date(2099, 12, 31))
        self.assertEqual(statement["closing_balance"], Decimal("1000.00"))
        self.assertIn(("Discount withdrawn", Decimal("20.00")),
                      [(entry.kind, entry.debit) for entry in statement["entries"]])

    def test_paid_in_two_and_one_returned(self):
        invoice, (_, second) = self.discounted("500", "480")
        second.void(memo="Returned unpaid", on_date=RETURNED)
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.amount_due(), self.receivable()), (Decimal("500.00"), Decimal("500.00")))

    def test_taken_back_once(self):
        invoice, (cheque,) = self.discounted("980")
        cheque.void(memo="Returned unpaid", on_date=RETURNED)
        self.assertIsNone(Invoice.objects.get(pk=invoice.pk).withdraw_unearned_discount(on_date=RETURNED))
        self.assertEqual(self.balance(self.discount_account), Decimal("0"))

    def test_what_a_credit_note_undid_is_not_withdrawn_again(self):
        invoice, (cheque,) = self.discounted("980")
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("1")})
        cheque.void(memo="Returned unpaid", on_date=RETURNED)
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual(invoice.settlement_discount_withdrawn, Decimal("18.00"))
        self.assertEqual(self.balance(self.discount_account), Decimal("0"))
        self.assertEqual(outstanding_balance(self.customer), self.receivable())

    def test_a_credit_note_after_the_return_undoes_no_discount(self):
        # Returned, paid again less the discount (owing the 20), then credited in full: the 980
        # paid comes back, and the withdrawn discount is not undone a second time.
        invoice, (cheque,) = self.discounted("980")
        cheque.void(memo="Returned unpaid", on_date=RETURNED)
        self.allocate(self.receipt("980", on=RETURNED), invoice, "980")
        note = Invoice.objects.get(pk=invoice.pk).create_credit_note()
        self.assertEqual((note.reversed_discount, note.refund_due()), (Decimal("0"), Decimal("980.00")))
        self.assertEqual(self.balance(self.discount_account), Decimal("0"))
        self.assertEqual(outstanding_balance(self.customer), self.receivable())
