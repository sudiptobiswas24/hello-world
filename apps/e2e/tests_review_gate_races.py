"""Reviewer races for the gate (review_gate), PostgreSQL only."""
import datetime
import unittest
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.db import connection
from django.test import tag

from apps.e2e.tests_races import RaceCase, race
from apps.purchasing import tests_weight_claims as fx


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class GateReviewRaces(fx.WeighedReceiptTestCase, RaceCase):
    def test_x1_one_reading_two_vehicles_at_once(self):
        from apps.inventory.gate import GateVisit, Weighing
        from apps.manufacturing.station_scale import ScaleReading, post_reading, weigh_from_bridge
        post_reading("WB-1", "25860", True)
        ws = []
        for n in ("MH12AB1", "MH12AB2"):
            v = GateVisit.objects.create(vehicle_number=n, driver_name="R", purpose="inward_order")
            ws.append(v.start_weighing("WB-1").pk)
        calls = [lambda pk=pk: weigh_from_bridge(Weighing.objects.get(pk=pk), "gross") for pk in ws]
        outcomes = race(Weighing, *calls)
        weighed = Weighing.objects.filter(pk__in=ws, gross_kg__isnull=False).count()
        self.assertEqual(weighed, 1, f"one reading weighed {weighed} vehicles: {outcomes}")

    def test_x2_one_movement_two_nonreturnable_passes_at_once(self):
        from apps.inventory.gate import GatePass, GatePassLine, GateVisit
        from apps.inventory.models import StockMovement
        su = User.objects.create_superuser("s", "s@x.com", "p")
        self.receipt(self.order("100", "95"), "100", None, documented=None)
        mv = StockMovement.objects.create(item=self.polymer, warehouse=self.warehouse, movement_type="out",
                                          uom=self.kg, quantity=D("-10"), document_quantity=D("10"),
                                          unit_cost=D("95")) if True else None
        passes = []
        for n in ("MH12AB1", "MH12AB2"):
            v = GateVisit.objects.create(vehicle_number=n, driver_name="R", purpose="gate_pass")
            gp = GatePass.objects.create(kind="non_returnable", party_name="Buyer", visit=v)
            GatePassLine.objects.create(gate_pass=gp, item=self.polymer, quantity=D("10"), movement=mv)
            passes.append(gp.pk)
        calls = [lambda pk=pk: GatePass.objects.get(pk=pk).approve(su) for pk in passes]
        outcomes = race(GatePass, *calls)
        self.once(outcomes)

    def test_x3_two_claims_for_the_whole_shortage_at_once(self):
        from apps.purchasing.weight_claims import WeightClaim
        line = self.order()
        received = self.receipt(line, "9940", self.weighing())
        calls = [lambda: WeightClaim.objects.create(receipt=received, against="supplier")] * 2
        self.once(race(WeightClaim, *calls))
