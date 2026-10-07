"""
A credit note on an invoice settled without money.

  Written off 1,000 and then credited in full: the credit undoes the bad
  debt, and the customer is owed nothing, since they never paid. Credited
  400, the rest written off, then credited 600: the same for the 600.
  Paid 980 with a 2% discount, then credited in full: they are owed the
  980 they paid, and the discount is undone. Credited one sack of ten
  instead: owed 98, and 2 of the discount undone; the other nine later:
  owed 882 more, and the rest of the discount. A write-off a credit note
  undid is not recovered again.

Each used to count the write-off or the discount as paid and owe it back.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .tests_base import SalesTestCase


class CreditAfterSettlementTests(SalesTestCase):
    def credit(self, invoice, sacks=None):
        line = invoice.lines.get()
        return invoice.create_credit_note(memo="Faulty sacks",
                                          quantities={line: Decimal(sacks)} if sacks else None)

    def paid_with_discount(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt("980", on=datetime.date(2026, 3, 8)), invoice, "980")
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        return invoice

    def test_a_written_off_invoice_credited_in_full_owes_nothing_back(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(reason="Customer gone")
        note = self.credit(invoice)
        self.assertEqual((note.reversed_write_off, note.refund_due()), (Decimal("1000.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.ar), self.balance(self.bad_debt)), (Decimal("0"), Decimal("0")))

    def test_what_was_written_off_after_a_credit_is_undone_by_the_next(self):
        invoice = self.bill(self.make_order("10", "100"))
        first = self.credit(invoice, "4")
        invoice.write_off(reason="The rest is uncollectable")
        second = self.credit(invoice, "6")
        self.assertEqual((first.refund_due(), second.reversed_write_off, second.refund_due()),
                         (Decimal("0.00"), Decimal("600.00"), Decimal("0.00")))
        self.assertEqual((self.balance(self.ar), self.balance(self.bad_debt)), (Decimal("0"), Decimal("0")))

    def test_a_discounted_invoice_credited_in_full_owes_back_what_was_paid(self):
        invoice = self.paid_with_discount()
        note = self.credit(invoice)
        self.assertEqual((note.reversed_discount, note.refund_due()), (Decimal("20.00"), Decimal("980.00")))
        self.assertEqual((self.balance(self.ar), self.balance(self.discount_account)), (Decimal("-980"), Decimal("0")))

    def test_the_discount_comes_back_in_proportion_and_whole_at_the_end(self):
        invoice = self.paid_with_discount()
        one = self.credit(invoice, "1")
        self.assertEqual((one.reversed_discount, one.refund_due()), (Decimal("2.00"), Decimal("98.00")))
        self.assertEqual(self.balance(self.discount_account), Decimal("18"))
        rest = self.credit(invoice, "9")
        self.assertEqual((rest.reversed_discount, rest.refund_due()), (Decimal("18.00"), Decimal("882.00")))
        self.assertEqual((self.balance(self.ar), self.balance(self.discount_account)), (Decimal("-980"), Decimal("0")))

    def test_a_write_off_a_credit_note_undid_is_not_recovered_again(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(reason="Customer gone")
        self.credit(invoice)
        with self.assertRaisesMessage(ValidationError, "is not recovered again"):
            invoice.recover_write_off(invoice.write_offs.get())
        self.assertEqual(self.balance(self.bad_debt), Decimal("0"))

    def test_the_last_credit_takes_what_the_shares_left_of_the_discount(self):
        # Three sacks at 16.67: 50.01, a 1.00 discount, 49.01 paid. Each sack's share rounds to
        # 0.33; the last takes the 0.34 left, so the refunds come to what was paid.
        invoice = self.bill(self.make_order("3", "16.67"))
        self.allocate(self.receipt("49.01", on=datetime.date(2026, 3, 8)), invoice, "49.01")
        invoice.apply_settlement_discount(on_date=datetime.date(2026, 3, 8))
        notes = [self.credit(invoice, "1") for _ in range(3)]
        self.assertEqual([note.reversed_discount for note in notes], [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")])
        self.assertEqual(sum(note.refund_due() for note in notes), Decimal("49.01"))
        self.assertEqual(self.balance(self.discount_account), Decimal("0"))
