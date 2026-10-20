"""Probes of the second audit, trade lane: purchasing, two people at once (PostgreSQL only)."""

import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.e2e.tests_races import RaceCase, fixture, race
from apps.purchasing import tests_lifecycle as purchase_fixture
from apps.purchasing.models import BlanketOrder, BlanketOrderLine, PurchaseOrder


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PurchasingTradeRaces(RaceCase):
    def test_R2_two_releases_at_once_do_not_call_off_more_than_the_agreement(self):
        base = fixture(self, purchase_fixture.PurchasingLifecycleTestCase)
        blanket = BlanketOrder.objects.create(vendor=base.vendor, start_date=datetime.date(2026, 1, 1),
                                              end_date=datetime.date(2026, 12, 31))
        line = BlanketOrderLine.objects.create(blanket=blanket, item=base.item, uom=base.uom,
                                               quantity=Decimal("100"), unit_price=Decimal("5"))
        blanket.confirm()
        release = lambda: BlanketOrder.objects.get(pk=blanket.pk).release(  # noqa: E731
            {BlanketOrderLine.objects.get(pk=line.pk): Decimal("60")}, order_date=datetime.date(2026, 2, 1))
        outcomes = race(PurchaseOrder, release, release)
        self.assertEqual(sorted(o == "done" for o in outcomes), [False, True], outcomes)
