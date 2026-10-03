"""
A confirmed order line that names no warehouse.

The planner selected lines with `warehouse__in=[warehouse, None]`, and
Django drops None from an IN list because NULL equals nothing. Such a
line was never planned, and never ate its forecast: 2,500 ordered
against a forecast of 4,000 planned for 4,000 + 2,500 = 6,500.
"""

from decimal import Decimal

from apps.sales.models import SalesOrderLine

from .tests_forecast import ForecastTestCase


class LinesNamingNoWarehouseTests(ForecastTestCase):
    def unplaced(self, quantity):
        line = self.sell(self.fabric, quantity, self.day(20))
        SalesOrderLine.objects.filter(pk=line.pk).update(warehouse=None)
        return line

    def test_it_is_planned(self):
        self.unplaced("2500")
        self.assertEqual(sum(order.quantity for order in self.fabrics()), Decimal("2500"))

    def test_it_eats_the_forecast(self):
        self.forecast("4000")
        self.unplaced("2500")
        self.assertEqual(sum(order.quantity for order in self.fabrics()), Decimal("4000"))
