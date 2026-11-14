"""review_floor: the promise against a move, two at once, on PostgreSQL."""
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag
from django.utils import timezone

from apps.e2e.tests_races import RaceCase, fixture, race
from apps.inventory.models import StockMovement, Warehouse
from apps.inventory.transfers import move_stock
from apps.inventory.locking import lock_positions

from .orders import WorkOrder
from .tape_loads import CreelSide, TapeLoad, load_tape
from .tests_station import at
from .tests_orders import TODAY
from .tests_tape_loads import TapeLoadTestCase


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PromiseAgainstMove(RaceCase):
    def test_load_and_move_of_one_doff(self):
        made = fixture(self, TapeLoadTestCase)
        WorkOrder.objects.filter(pk=made.run.pk).update(backflush=True)
        store = Warehouse.objects.create(code="ST", name="Store")
        d1 = made.doffs["D-1"]
        outcomes = race(
            (TapeLoad, StockMovement),
            lambda: load_tape(made.station, made.operator, made.l17, "D-1", "100", CreelSide.WARP, at=at(TODAY, 9)),
            lambda: (lock_positions([(made.tape, made.plant), (made.tape, store)]),
                     move_stock(made.tape, made.plant, store, Decimal("100"), "T-1", timezone.now(), "", lot=d1)))
        print("RACE outcomes", outcomes)
        print("RACE loads", TapeLoad.objects.count(), "on hand plant", d1.on_hand_at(made.plant), "store", d1.on_hand_at(store))
        self.once(outcomes)
