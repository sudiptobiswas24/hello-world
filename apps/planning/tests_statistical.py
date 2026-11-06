"""
Fabric shipped from the plant in a sack plant's year, in hundreds of kg,
January to December: 50 50 60 40 30 20 20 20 30 50 60 50. An average
month is 40, so November's index is 60 / 40 = 1.5 and July's is 0.5.

  Two identical years to September 2026: October to March forecast at
  5,000, 6,000, 5,000, 5,000, 5,000 and 6,000 kg.
  The second year 20% up: the level is 4,800 a month, the indices are
  unchanged, October is 4,800 x 1.25 = 6,000. With trend, September
  2027 is 4,800 x 0.75 x 1.2 = 4,320.
  Three years, the third 10% up: last year forecast from the two before
  it is 10% short of every month, a mean error of 9.09% and a bias of
  -9.09%.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .forecast import Forecast
from .statistical import accept, monthly_shipments, propose
from .tests_mrp import PlanningTestCase

PATTERN = [50, 50, 60, 40, 30, 20, 20, 20, 30, 50, 60, 50]
AS_OF = datetime.date(2026, 10, 15)


class StatisticalTestCase(PlanningTestCase):
    def ship(self, day, quantity, job_work=False, returned=None):
        order = SalesOrder.objects.create(customer=self.customer, order_date=day,
                                          currency=self.inr, is_job_work=job_work)
        line = SalesOrderLine.objects.create(order=order, item=self.fabric, uom=self.kg,
                                             quantity=Decimal(quantity),
                                             unit_price=Decimal("90"),
                                             revenue_account=self.revenue, warehouse=self.plant)
        delivery = Delivery.objects.create(sales_order=order, delivery_date=day,
                                           reverses=returned)
        DeliveryLine.objects.create(delivery=delivery, order_line=line, warehouse=self.plant,
                                    quantity_shipped=Decimal(quantity))
        # History is what posted deliveries say; posting itself is tested
        # with stock where it lives.
        Delivery.objects.filter(pk=delivery.pk).update(posted=True)
        return delivery

    def years(self, *factors, start=datetime.date(2024, 10, 1), pattern=PATTERN):
        month = start
        for factor in factors:
            for _ in range(12):
                quantity = Decimal(pattern[month.month - 1]) * 100 * Decimal(str(factor))
                if quantity:
                    self.ship(month.replace(day=15), str(quantity))
                month = datetime.date(month.year + month.month // 12, month.month % 12 + 1, 1)

    def quantities(self, proposal):
        return [row["quantity"] for row in proposal["rows"]]


class SeasonTests(StatisticalTestCase):
    def test_two_like_years_give_back_the_season(self):
        self.years(1, 1)
        found = propose(self.fabric, self.plant, as_of=AS_OF)
        self.assertEqual((found["method"], found["history_months"]), ("seasonal", 24))
        self.assertEqual((found["indices"][11], found["indices"][7]),
                         (Decimal("1.5000"), Decimal("0.5000")))
        self.assertEqual(self.quantities(found),
                         [Decimal(q) for q in ("5000", "6000", "5000", "5000", "5000", "6000")])
        self.assertEqual(found["rows"][0]["starts_on"], datetime.date(2026, 10, 1))
        self.assertIn("needs 36 months", found["backtest"]["note"])

    def test_a_better_year_moves_the_level_not_the_season(self):
        self.years(1, "1.2")
        found = propose(self.fabric, self.plant, as_of=AS_OF)
        self.assertEqual((found["level"], found["year_on_year_growth"], found["trend_applied"]),
                         (Decimal("4800.000"), Decimal("1.2000"), False))
        self.assertEqual(self.quantities(found)[:2], [Decimal("6000"), Decimal("7200")])

    def test_trend_only_when_asked(self):
        self.years(1, "1.2")
        found = propose(self.fabric, self.plant, as_of=AS_OF, months_ahead=12, with_trend=True)
        self.assertEqual((found["rows"][-1]["starts_on"], found["rows"][-1]["quantity"]),
                         (datetime.date(2027, 9, 1), Decimal("4320.000")))
        # A month ahead carries a twelfth of a year's growth: 6,000 x 1.2^(1/12).
        self.assertEqual(found["rows"][0]["quantity"], Decimal("6091.857"))

    def test_a_month_that_shipped_nothing_is_left_out_of_the_score(self):
        july_dead = PATTERN[:6] + [0] + PATTERN[7:]
        self.years(1, 1, "1.1", start=datetime.date(2023, 10, 1), pattern=july_dead)
        backtest = propose(self.fabric, self.plant, as_of=AS_OF)["backtest"]
        self.assertEqual(backtest["mape_percent"], Decimal("9.09"))

    def test_more_back_than_went_out_forecasts_nothing_not_less(self):
        july_dead = PATTERN[:6] + [0] + PATTERN[7:]
        self.years(1, 1, pattern=july_dead)
        sent = Delivery.objects.filter(delivery_date=datetime.date(2026, 6, 15)).get()
        self.ship(datetime.date(2026, 7, 20), "500", returned=sent)
        found = propose(self.fabric, self.plant, as_of=AS_OF, months_ahead=10)
        self.assertLess(found["indices"][7], 0)
        self.assertEqual(found["rows"][-1]["starts_on"], datetime.date(2027, 7, 1))
        self.assertEqual(found["rows"][-1]["quantity"], Decimal("0.000"))

    def test_last_year_scored_against_the_years_before_it(self):
        self.years(1, 1, "1.1", start=datetime.date(2023, 10, 1))
        backtest = propose(self.fabric, self.plant, as_of=AS_OF)["backtest"]
        self.assertEqual((backtest["months"], backtest["mape_percent"], backtest["bias_percent"]),
                         (12, Decimal("9.09"), Decimal("-9.09")))


class HistoryTests(StatisticalTestCase):
    def test_returns_net_off_and_job_work_is_not_ours(self):
        sent = self.ship(datetime.date(2026, 3, 10), "5000")
        self.ship(datetime.date(2026, 3, 20), "1000", returned=sent)
        self.ship(datetime.date(2026, 3, 25), "3000", job_work=True)
        months = monthly_shipments(self.fabric, self.plant, datetime.date(2026, 3, 1),
                                   datetime.date(2026, 3, 1))
        self.assertEqual(months[datetime.date(2026, 3, 1)], Decimal("4000"))

    def test_job_work_long_ago_does_not_start_the_history(self):
        self.ship(datetime.date(2022, 1, 15), "9000", job_work=True)
        self.years(1, 1)
        self.assertEqual(propose(self.fabric, self.plant, as_of=AS_OF)["history_months"], 24)

    def test_a_year_without_a_second_is_flat(self):
        self.years(1, start=datetime.date(2025, 4, 1))
        self.ship(datetime.date(2026, 4, 15), "4000")
        found = propose(self.fabric, self.plant, as_of=AS_OF)
        self.assertEqual((found["method"], found["indices"]), ("level", None))
        self.assertIn("no second year", found["notes"][0])
        # Oct-Mar 32,000 kg and April's 4,000: 36,000 over twelve months.
        self.assertEqual(set(self.quantities(found)), {Decimal("3000.000")})

    def test_under_a_year_is_refused(self):
        self.ship(datetime.date(2026, 3, 15), "4000")
        with self.assertRaisesMessage(ValidationError, "needs at least 12"):
            propose(self.fabric, self.plant, as_of=AS_OF)

    def test_nothing_ever_shipped(self):
        with self.assertRaisesMessage(ValidationError, "has never shipped"):
            propose(self.fabric, self.plant, as_of=AS_OF)


class AcceptTests(StatisticalTestCase):
    def test_written_as_forecasts_once_and_nothing_for_a_dead_month(self):
        july_dead = PATTERN[:6] + [0] + PATTERN[7:]
        self.years(1, 1, pattern=july_dead)
        created, skipped = accept(self.fabric, self.plant, as_of=AS_OF, months_ahead=10)
        self.assertEqual(len(created), 9)
        ((row, reason),) = skipped
        self.assertEqual((row["starts_on"], reason),
                         (datetime.date(2027, 7, 1), "history says nothing ships that month"))
        self.assertTrue(created[0].notes.startswith("Statistical, seasonal, from 24 months"))
        again, skipped = accept(self.fabric, self.plant, as_of=AS_OF, months_ahead=10)
        self.assertEqual(again, [])
        self.assertEqual(sum(1 for _, reason in skipped if reason.startswith("already")), 9)
        self.assertEqual(Forecast.objects.count(), 9)


class StatisticalApiTests(StatisticalTestCase):
    def test_proposed_then_accepted(self):
        self.years(1, 1)
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        params = {"item": self.fabric.pk, "warehouse": self.plant.pk, "on": "2026-10-15",
                  "months": 3}
        body = client.get("/api/planning/forecasts/propose/", params).json()
        self.assertEqual([row["quantity"] for row in body["rows"]],
                         ["5000.000", "6000.000", "5000.000"])
        self.assertEqual(Forecast.objects.count(), 0)
        response = client.post("/api/planning/forecasts/accept/", params, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(response.json()["created"]), 3)
        response = client.get("/api/planning/forecasts/propose/",
                              {"item": self.fabric.pk, "warehouse": self.plant.pk, "months": "x"})
        self.assertEqual(response.status_code, 400)
