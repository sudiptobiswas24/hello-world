"""
Demand nobody has ordered yet, and the rule that stops it being
counted twice.

Forty tonnes forecast for October and twenty-five ordered is forty
tonnes of demand, of which twenty-five is now real. Adding the two is
how a forecast doubles a plant's stock and then gets switched off.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .forecast import Forecast, coverage
from .models import DemandSource
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class ForecastTestCase(PlanningTestCase):
    def forecast(self, quantity, first=10, last=40, item=None, warehouse=None):
        return Forecast.objects.create(
            item=item or self.fabric, warehouse=warehouse or self.plant,
            starts_on=self.day(first), ends_on=self.day(last),
            quantity=Decimal(quantity),
        )

    def fabrics(self):
        return list(self.plan().orders.filter(item=self.fabric))


class ConsumingTheForecastTests(ForecastTestCase):
    def test_an_unconsumed_forecast_is_planned_for(self):
        """
        The point of it: the material has to be there for the orders
        that have not come in yet.
        """
        self.forecast("4000")
        orders = self.fabrics()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, Decimal("4000"))
        self.assertEqual(orders[0].needed_by, self.day(10))
        self.assertEqual(
            orders[0].demands.get().source, DemandSource.FORECAST
        )

    def test_an_order_inside_the_period_eats_the_forecast(self):
        self.forecast("4000")
        self.sell(self.fabric, "2500", self.day(20))
        total = sum(order.quantity for order in self.fabrics())
        self.assertEqual(total, Decimal("4000"))

    def test_ordering_more_than_forecast_plans_for_the_orders(self):
        self.forecast("4000")
        self.sell(self.fabric, "5000", self.day(20))
        total = sum(order.quantity for order in self.fabrics())
        self.assertEqual(total, Decimal("5000"))

    def test_a_fully_consumed_forecast_adds_nothing(self):
        self.forecast("4000")
        self.sell(self.fabric, "4000", self.day(20))
        orders = self.fabrics()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, Decimal("4000"))
        self.assertEqual(orders[0].demands.get().source, DemandSource.SALES)

    def test_an_order_outside_the_period_does_not_eat_it(self):
        """
        An order for the third of November does not eat October's
        forecast, however much October has left. Plants that let it
        end up with a forecast that never expires.
        """
        self.forecast("4000", first=10, last=40)
        self.sell(self.fabric, "2500", self.day(60))
        total = sum(order.quantity for order in self.fabrics())
        self.assertEqual(total, Decimal("6500"))


class WhenTheForecastIsWantedTests(ForecastTestCase):
    def test_it_is_dated_at_the_start_of_its_period(self):
        """
        A forecast for October means "we expect to ship this during
        October", so the material has to be there at the beginning.
        """
        self.forecast("4000", first=30, last=60)
        self.assertEqual(self.fabrics()[0].needed_by, self.day(30))

    def test_a_period_already_under_way_is_wanted_now(self):
        self.forecast("4000", first=-10, last=20)
        self.assertEqual(self.fabrics()[0].needed_by, TODAY)

    def test_a_period_entirely_past_is_never_going_to_be_ordered(self):
        self.forecast("4000", first=-60, last=-10)
        self.assertEqual(self.fabrics(), [])

    def test_a_past_period_adds_nothing_on_an_item_being_planned_anyway(self):
        """
        The date filter is applied twice — once to decide which items
        the planner looks at and once to the demand itself — and both
        have to hold. An item reached because somebody ordered it
        would otherwise drag in every forecast the plant ever wrote.
        """
        self.forecast("4000", first=-60, last=-10)
        self.sell(self.fabric, "500", self.day(20))
        orders = self.fabrics()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, Decimal("500"))

    def test_a_period_past_the_horizon_is_left_alone(self):
        self.forecast("4000", first=200, last=260)
        self.assertEqual(
            self.plan(horizon_days=90).orders.filter(item=self.fabric).count(),
            0,
        )


class TwoForecastsForOneWeekTests(ForecastTestCase):
    def test_overlapping_periods_are_refused(self):
        """
        Two forecasts covering the same week would each be consumed by
        the same orders, and between them plan for twice the material.
        """
        self.forecast("4000", first=10, last=40)
        with self.assertRaisesMessage(ValidationError, "twice the material"):
            self.forecast("2000", first=30, last=60)

    def test_periods_that_touch_but_do_not_overlap_are_fine(self):
        self.forecast("4000", first=10, last=40)
        self.forecast("2000", first=41, last=70)
        total = sum(order.quantity for order in self.fabrics())
        self.assertEqual(total, Decimal("6000"))

    def test_another_shelfs_forecast_is_its_own(self):
        from apps.inventory.models import Warehouse

        other = Warehouse.objects.create(code="N", name="Nagpur")
        self.forecast("4000", first=10, last=40)
        self.forecast("2000", first=10, last=40, warehouse=other)
        self.assertEqual(
            sum(o.quantity for o in self.fabrics()), Decimal("4000")
        )


class WasTheForecastAnyGoodTests(ForecastTestCase):
    def test_it_reports_expected_against_ordered(self):
        """
        A plant whose forecasts are consistently double what arrives
        is carrying stock for orders that never come, and one whose
        forecasts are short is the plant that keeps running out.
        Neither shows up anywhere else.
        """
        self.forecast("4000")
        self.sell(self.fabric, "3000", self.day(20))
        row = coverage(self.fabric, self.plant, planned_on=TODAY)[0]
        self.assertEqual(row["expected"], Decimal("4000"))
        self.assertEqual(row["ordered"], Decimal("3000"))
        self.assertEqual(row["unconsumed"], Decimal("1000"))
        self.assertEqual(row["accuracy_percent"], Decimal("75.00"))

    def test_over_ordering_is_reported_as_its_own_figure(self):
        self.forecast("4000")
        self.sell(self.fabric, "5000", self.day(20))
        row = coverage(self.fabric, self.plant, planned_on=TODAY)[0]
        self.assertEqual(row["unconsumed"], Decimal("0"))
        self.assertEqual(row["over_ordered"], Decimal("1000"))
        self.assertEqual(row["accuracy_percent"], Decimal("125.00"))


class WhatItPullsThroughTests(ForecastTestCase):
    def test_a_forecast_pulls_the_whole_plant_like_an_order_does(self):
        self.forecast("1000")
        found = {o.item.sku: o for o in self.plan().orders.all()}
        self.assertIn("TAPE-1000", found)
        self.assertIn("PP-RAFFIA", found)

    def test_the_reason_says_it_is_a_forecast_and_not_a_promise(self):
        self.forecast("1000")
        fabric = self.fabrics()[0]
        self.assertIn("forecast for", fabric.explanation())
        self.assertIn("not yet ordered", fabric.explanation())
