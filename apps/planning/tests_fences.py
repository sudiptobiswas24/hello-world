"""
The frozen zone and the demand fence.

TODAY is Monday 1 June, and these tests put the plant on weekdays —
its default is continuous, which an earlier draft of this docstring
forgot. A planning fence of five working days then ends on Monday 8
June: day 7, not day 5, because the weekend inside it freezes nothing. Polymer is bought on
a seven-day lead with nothing on the shelf, so every sale of it is a
shortage on the day it is due.
"""

from decimal import Decimal

from .forecast import Forecast
from .models import PlanningSettings, RescheduleAction
from .tests_base import TODAY
from .tests_reschedule import RescheduleTestCase


class FenceTestCase(RescheduleTestCase):
    def fences(self, planning=0, demand=0):
        settings = PlanningSettings.get()
        settings.working_days = "12345"
        settings.planning_fence_days = planning
        settings.demand_fence_days = demand
        settings.save()


class WithoutAFenceNothingMovesTests(FenceTestCase):
    def test_a_shortage_tomorrow_is_planned_for_tomorrow(self):
        self.sell(self.virgin, "100", self.day(3))
        run = self.plan()
        order = run.orders.get(item=self.virgin)
        self.assertEqual(order.needed_by, self.day(3))
        self.assertIsNone(order.fenced_from)
        self.assertIsNone(run.fence_ends)


class TheFrozenZoneTests(FenceTestCase):
    def setUp(self):
        super().setUp()
        self.fences(planning=5)

    def test_the_fence_is_counted_in_working_days(self):
        self.assertEqual(self.plan().fence_ends, self.day(7))

    def test_a_shortage_inside_is_planned_for_the_first_day_outside(self):
        self.sell(self.virgin, "100", self.day(3))
        run = self.plan()
        order = run.orders.get(item=self.virgin)
        self.assertEqual(order.needed_by, self.day(7))
        self.assertEqual(order.fenced_from, self.day(3))
        self.assertTrue(order.was_fenced())
        self.assertEqual(list(run.fenced()), [order])
        self.assertIn("inside the frozen zone", order.explanation())

    def test_a_shortage_already_overdue_is_fenced_too(self):
        """
        Nothing can be done before the fence, least of all yesterday.
        Netting already reads past-due demand as due today, so today is
        the date the order says it was wanted — and the demand row
        underneath keeps the real one, which the explanation shows.
        """
        self.sell(self.virgin, "100", self.day(-2))
        order = self.plan().orders.get(item=self.virgin)
        self.assertEqual(order.needed_by, self.day(7))
        self.assertEqual(order.fenced_from, TODAY)
        self.assertEqual(order.demands.get().needed_by, self.day(-2))

    def test_the_first_day_outside_is_outside(self):
        self.sell(self.virgin, "100", self.day(7))
        order = self.plan().orders.get(item=self.virgin)
        self.assertEqual(order.needed_by, self.day(7))
        self.assertIsNone(order.fenced_from)

    def test_a_shortage_beyond_it_is_left_alone(self):
        self.sell(self.virgin, "100", self.day(20))
        order = self.plan().orders.get(item=self.virgin)
        self.assertEqual(order.needed_by, self.day(20))
        self.assertIsNone(order.fenced_from)


class AMessageIntoTheFrozenZoneIsFlaggedTests(FenceTestCase):
    def setUp(self):
        super().setUp()
        self.fences(planning=5)

    def action(self, kind):
        return [a for a in self.actions() if a.action == kind]

    def test_pulling_an_order_in_to_a_day_inside_is_flagged(self):
        self.sell(self.virgin, "100", self.day(3))
        self.purchase(self.virgin, "100", self.day(20))
        (expedite,) = self.action(RescheduleAction.EXPEDITE)
        self.assertEqual(expedite.wanted_on, self.day(3))
        self.assertTrue(expedite.inside_fence)

    def test_pulling_one_in_to_a_day_outside_is_not(self):
        self.sell(self.virgin, "100", self.day(15))
        self.purchase(self.virgin, "100", self.day(30))
        (expedite,) = self.action(RescheduleAction.EXPEDITE)
        self.assertFalse(expedite.inside_fence)

    def test_moving_an_order_already_inside_is_flagged(self):
        """
        Arriving on day two and not wanted until day thirty: pushing it
        out changes the frozen zone as surely as pulling one in does.
        """
        self.sell(self.virgin, "100", self.day(30))
        self.purchase(self.virgin, "100", self.day(2))
        (defer,) = self.action(RescheduleAction.DEFER)
        self.assertTrue(defer.inside_fence)
        run = self.plan()
        self.assertEqual(
            [a.action for a in run.into_the_fence()], [RescheduleAction.DEFER]
        )


class TheDemandFenceTests(FenceTestCase):
    def forecast(self, starts, ends, quantity="100"):
        return Forecast.objects.create(
            item=self.virgin, warehouse=self.plant, starts_on=self.day(starts),
            ends_on=self.day(ends), quantity=Decimal(quantity),
        )

    def test_forecast_inside_it_is_left_out_and_said(self):
        self.fences(demand=5)
        self.forecast(2, 6)
        run = self.plan()
        self.assertFalse(run.orders.filter(item=self.virgin).exists())
        self.assertIn("PP-RAFFIA", run.unforecast)
        self.assertIn("left out", run.unforecast)

    def test_forecast_beyond_it_still_counts(self):
        self.fences(demand=5)
        self.forecast(20, 26)
        run = self.plan()
        self.assertEqual(
            run.orders.get(item=self.virgin).quantity, Decimal("100")
        )
        self.assertEqual(run.unforecast, "")

    def test_a_real_order_inside_it_still_counts(self):
        self.fences(demand=5)
        self.sell(self.virgin, "40", self.day(3))
        self.assertEqual(
            self.plan().orders.get(item=self.virgin).quantity, Decimal("40")
        )

    def test_without_the_fence_the_forecast_counts(self):
        self.forecast(2, 6)
        self.assertEqual(
            self.plan().orders.get(item=self.virgin).quantity, Decimal("100")
        )
