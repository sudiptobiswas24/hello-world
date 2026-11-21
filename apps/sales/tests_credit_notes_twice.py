"""
Crediting an invoice in full, twice.

Two clicks on Credit, or two people, and the second took the whole
invoice back again: receivables went to minus 1,000 on a 1,000 invoice
nobody had paid. A full credit is of what is still creditable, and
there is none after the first.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .tests_base import SalesTestCase


class ACreditInFullIsOnceTests(SalesTestCase):
    def test_the_second_is_refused(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.create_credit_note()
        with self.assertRaisesMessage(ValidationError, "Nothing to credit"):
            invoice.create_credit_note()
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_after_part_the_rest(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("3")})
        rest = invoice.create_credit_note()
        self.assertEqual((rest.lines.get().quantity, self.balance(self.ar)),
                         (Decimal("7.0000"), Decimal("0")))
