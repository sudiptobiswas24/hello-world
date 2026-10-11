"""
How much to order at once.

Lot for lot is exact and raises twelve orders for twelve daily
call-offs. Everything here trades a little stock for fewer orders, or
respects a limit somebody else set: a vendor's minimum, a bag size, a
silo.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import PlannedOrderKind
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class LotSizingTestCase(PlanningTestCase):
    def policy(self, **kwargs):
        rule = self.rule(self.fabric, minimum="0", target="0")
        for field, value in kwargs.items():
            setattr(rule, field, Decimal(value) if isinstance(value, str) else value)
        rule.save()
        return rule

    def fabrics(self):
        return list(
            self.plan().orders.filter(item=self.fabric).order_by("id")
        )


class LotForLotTests(LotSizingTestCase):
    def test_twelve_call_offs_raise_twelve_orders(self):
        for day in range(10, 22):
            self.sell(self.fabric, "100", self.day(day))
        self.assertEqual(len(self.fabrics()), 12)


class OrderingForAPeriodTests(LotSizingTestCase):
    def test_a_week_of_demand_becomes_one_order(self):
        self.policy(order_period_days=7)
        for day in range(10, 22):
            self.sell(self.fabric, "100", self.day(day))
        orders = self.fabrics()
        self.assertLess(len(orders), 12)
        self.assertEqual(
            sum(order.quantity for order in orders), Decimal("1200")
        )

    def test_the_first_order_carries_the_week(self):
        self.policy(order_period_days=7)
        for day in (10, 12, 14):
            self.sell(self.fabric, "100", self.day(day))
        orders = self.fabrics()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, Decimal("300"))
        self.assertEqual(orders[0].needed_by, self.day(10))

    def test_a_receipt_inside_the_window_is_not_bought_twice(self):
        """
        The look-ahead is the deepest the balance would go, not the
        window's total. Demand a receipt already covers does not need
        buying again.
        """
        from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine

        self.policy(order_period_days=7)
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg,
            quantity=Decimal("100"), unit_price=Decimal("90"),
            expected_date=self.day(12),
        )
        order.confirm()
        for day in (10, 12, 14):
            self.sell(self.fabric, "100", self.day(day))
        orders = self.fabrics()
        self.assertEqual(sum(o.quantity for o in orders), Decimal("200"))

    def test_demand_past_the_window_gets_its_own_order(self):
        self.policy(order_period_days=7)
        self.sell(self.fabric, "100", self.day(10))
        self.sell(self.fabric, "100", self.day(40))
        orders = self.fabrics()
        self.assertEqual(len(orders), 2)
        self.assertEqual(
            [o.needed_by for o in orders], [self.day(10), self.day(40)]
        )


class TheLeastAnybodyWillSupplyTests(LotSizingTestCase):
    def test_a_vendor_minimum_lifts_a_small_order(self):
        self.policy(minimum_order_quantity="1000")
        self.sell(self.fabric, "200", self.day(30))
        orders = self.fabrics()
        self.assertEqual(orders[0].quantity, Decimal("1000"))
        self.assertEqual(orders[0].rounded_up_by, Decimal("800"))

    def test_a_larger_requirement_is_left_alone(self):
        self.policy(minimum_order_quantity="1000")
        self.sell(self.fabric, "2000", self.day(30))
        self.assertEqual(self.fabrics()[0].quantity, Decimal("2000"))

    def test_the_minimum_applies_before_the_bag_size(self):
        """
        Order matters, and only at a bag size that does not divide the
        minimum. A one-tonne minimum in three-hundred-kilo bags is
        four bags — twelve hundred kilos. Rounded first and lifted
        afterwards it would come to a round tonne, which is a quantity
        nobody can actually deliver.
        """
        self.policy(minimum_order_quantity="1000", multiple_of="300")
        self.sell(self.fabric, "200", self.day(30))
        self.assertEqual(self.fabrics()[0].quantity, Decimal("1200"))


class WhatFitsInOneOrderTests(LotSizingTestCase):
    def test_a_requirement_too_big_for_one_order_is_split(self):
        self.policy(maximum_order_quantity="5000")
        self.sell(self.fabric, "12000", self.day(30))
        orders = self.fabrics()
        self.assertEqual(len(orders), 3)
        self.assertEqual(
            sum(order.quantity for order in orders), Decimal("12000")
        )

    def test_it_splits_evenly_rather_than_into_full_loads(self):
        """
        Three lots of four tonnes runs better than two of five and one
        of two, and the plant would have levelled them itself.
        """
        self.policy(maximum_order_quantity="5000")
        self.sell(self.fabric, "12000", self.day(30))
        self.assertEqual(
            [o.quantity for o in self.fabrics()],
            [Decimal("4000"), Decimal("4000"), Decimal("4000")],
        )

    def test_no_part_is_over_the_limit(self):
        self.policy(maximum_order_quantity="5000")
        self.sell(self.fabric, "11000", self.day(30))
        for order in self.fabrics():
            self.assertLessEqual(order.quantity, Decimal("5000"))

    def test_the_reasons_go_with_the_first_part(self):
        """
        Apportioning one customer's line across three orders reads as
        three separate promises and is not.
        """
        line = self.sell(self.fabric, "12000", self.day(30))
        self.policy(maximum_order_quantity="5000")
        orders = self.fabrics()
        self.assertEqual(orders[0].demands.get().sales_order_line, line)
        self.assertEqual(orders[1].demands.count(), 0)

    def test_a_requirement_that_fits_is_not_split(self):
        self.policy(maximum_order_quantity="5000")
        self.sell(self.fabric, "3000", self.day(30))
        self.assertEqual(len(self.fabrics()), 1)


class PoliciesThatContradictTests(LotSizingTestCase):
    def test_a_maximum_below_the_minimum_is_refused(self):
        rule = self.rule(self.fabric, minimum="0", target="0")
        rule.minimum_order_quantity = Decimal("1000")
        rule.maximum_order_quantity = Decimal("500")
        with self.assertRaisesMessage(ValidationError, "no order is possible"):
            rule.full_clean()

    def test_a_maximum_below_the_bag_size_is_refused(self):
        rule = self.rule(self.fabric, minimum="0", target="0")
        rule.multiple_of = Decimal("1000")
        rule.maximum_order_quantity = Decimal("500")
        with self.assertRaisesMessage(ValidationError, "refused by one rule"):
            rule.full_clean()
