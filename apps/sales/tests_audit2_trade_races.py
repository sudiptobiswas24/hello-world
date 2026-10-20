"""Probes of the second audit, trade lane: two people at once (PostgreSQL only)."""

import datetime
import unittest

from django.db import connection
from django.test import tag

from apps.e2e.tests_races import RaceCase, fixture, race
from apps.purchasing import tests_lifecycle as purchase_fixture
from apps.sales import tests_base as sales_fixture
from apps.sales.models import DunningLevel, DunningNotice, run_dunning


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TradeRaces(RaceCase):
    def test_R1_two_dunning_runs_at_once_send_each_level_once_and_neither_fails(self):
        sales = fixture(self, sales_fixture.SalesTestCase)
        DunningLevel.objects.create(name="First", days_overdue=7)
        order = sales.make_order("10", "100")
        sales.ship(order, 10)
        invoice = sales.bill(order, on=datetime.date(2026, 3, 1))
        day = invoice.due_date + datetime.timedelta(days=10)
        outcomes = race(DunningNotice, lambda: run_dunning(as_of=day, send=False),
                        lambda: run_dunning(as_of=day, send=False))
        self.assertEqual(outcomes, ["done", "done"])
        self.assertEqual(DunningNotice.objects.filter(invoice=invoice).count(), 1)
