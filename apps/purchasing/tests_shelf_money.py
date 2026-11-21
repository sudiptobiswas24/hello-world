"""
O269: a receipt and its return to the vendor move the shelf by exactly what they book.

2.5 at 4.01 (a price keeps two places) are 10.025, booked 10.03, and the shelf takes 10.03. One sent
back takes 4.01 off the shelf, 10.03 over 2.5 to the paisa, and the accrual gives back 10.03 less the
6.02 for the 1.5 left, 4.01.
Figures: /home/user/work/calc_fix_o269/o269.py.
"""

from decimal import Decimal

from .tests_lifecycle import PurchasingLifecycleTestCase

D = Decimal


class AReceiptMovesTheShelfByItsEntryTests(PurchasingLifecycleTestCase):
    def balance(self, account):
        return sum((line.debit - line.credit for line in account.lines.all()), D("0"))

    def moved(self, step):
        shelf, books = self.item.valuation_at(self.warehouse)[1], self.balance(self.inventory)
        result = step()
        return result, self.item.valuation_at(self.warehouse)[1] - shelf, self.balance(self.inventory) - books

    def test_a_receipt_and_its_return_move_shelf_and_books_alike(self):
        order = self.make_order(quantity="2.5", price="4.01")
        receipt, shelf, books = self.moved(lambda: self.receive(order, "2.5"))
        self.assertEqual((shelf, books), (D("10.03"), D("10.03")))
        line = receipt.lines.get()
        _back, shelf, books = self.moved(lambda: receipt.create_return(quantities={line: D("1")}, debit_bills=False))
        self.assertEqual((shelf, books), (D("-4.01"), D("-4.01")))
