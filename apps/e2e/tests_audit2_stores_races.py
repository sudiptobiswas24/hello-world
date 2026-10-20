"""
Audit probe (audit2_stores), PostgreSQL: two different documents drawing on one shelf at once leave the
shelf and the inventory account together (each takes its money after the lock, not before).
"""

import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import JournalLine
from apps.inventory import tests_adjustments as stock_fixture
from apps.inventory.models import StockAdjustment, StockMovement, StockTransfer, StockTransferLine, Warehouse

from .tests_races import RaceCase, race

D = Decimal


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoDocumentsOneShelf(RaceCase):
    def ledger(self, made):
        return sum((l.debit - l.credit for l in JournalLine.objects.filter(account=made.inventory)), D("0"))

    def test_two_write_offs_of_the_last_two_kilos(self):
        made = stock_fixture.AdjustmentTestCase()
        made.setUp()
        self.addCleanup(made.doCleanups)
        up = made.adjust("2", reason=made.found, cost="10.005")
        a = made.adjust("-1", reason=made.shrink, post=False)
        b = made.adjust("-1", reason=made.shrink, post=False)
        outcomes = race(StockMovement, lambda: StockAdjustment.objects.get(pk=a.pk).post(),
                        lambda: StockAdjustment.objects.get(pk=b.pk).post())
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertEqual(made.item.valuation_at(made.warehouse), (D("0"), D("0")))
        self.assertEqual(self.ledger(made), D("0"))

    def test_a_write_off_and_a_transfer_out_of_the_last_two_kilos(self):
        made = stock_fixture.AdjustmentTestCase()
        made.setUp()
        self.addCleanup(made.doCleanups)
        south = Warehouse.objects.create(code="S", name="South")
        made.adjust("2", reason=made.found, cost="10.005")
        a = made.adjust("-1", reason=made.shrink, post=False)
        t = StockTransfer.objects.create(transfer_date=datetime.date(2026, 3, 1), from_warehouse=made.warehouse,
                                         to_warehouse=south)
        StockTransferLine.objects.create(transfer=t, item=made.item, uom=made.item.uom, quantity=D("1"))
        outcomes = race(StockMovement, lambda: StockAdjustment.objects.get(pk=a.pk).post(),
                        lambda: StockTransfer.objects.get(pk=t.pk).post())
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        shelf = sum((made.item.valuation_at(w)[1] for w in Warehouse.objects.all()), D("0"))
        self.assertEqual(shelf, self.ledger(made))
        # one kilo left the books at 10.01 (the first) and the other kept the 10.00 that was left
        self.assertEqual(made.item.valuation_at(made.warehouse)[0] + made.item.valuation_at(south)[0], D("1"))
