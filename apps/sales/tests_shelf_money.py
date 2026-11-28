"""
O269: a delivery and its return move the shelf by exactly what they book.

The fixture's 500 at 4 on the shelf, then three more booked 20.01: 2,020.01 over 503. Shipping seven takes
its share of the average to the paisa, and the return puts exactly that back. Figures:
/home/user/work/calc_fix_o269/o269.py.
"""

from decimal import Decimal

from django.utils import timezone

from apps.inventory.models import MovementType, StockMovement

from .tests_base import SalesTestCase

D = Decimal


class ADeliveryMovesTheShelfByItsEntryTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        StockMovement.objects.create(
            item=self.item, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            uom=self.item.uom, quantity=D("3"), unit_cost=D("6.67"), value=D("20.01"),
            occurred_at=timezone.now())

    def moved(self, step):
        shelf, books = self.item.valuation_at(self.warehouse)[1], self.balance(self.inventory)
        result = step()
        return result, self.item.valuation_at(self.warehouse)[1] - shelf, self.balance(self.inventory) - books

    def test_a_shipment_and_its_return_move_shelf_and_books_alike(self):
        held, value = self.item.valuation_at(self.warehouse)
        self.assertNotEqual((value * 7 / held) % D("0.01"), 0)  # seven do not come out even
        order = self.make_order(quantity="7")
        delivery, shelf, books = self.moved(lambda: self.ship(order, "7"))
        self.assertEqual(shelf, books)
        self.assertEqual(shelf, D("-28.11"))  # 2,020.01 x 7 / 503 = 28.1115...
        self.assertEqual(delivery.lines.get().allocations.get().cost, D("28.11"))
        _back, shelf, books = self.moved(lambda: delivery.create_return(credit_invoices=False))
        self.assertEqual((shelf, books), (D("28.11"), D("28.11")))
