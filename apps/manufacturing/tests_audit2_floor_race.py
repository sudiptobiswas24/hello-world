"""Audit 2, floor lane: two people at once, on PostgreSQL."""
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag
from django.utils import timezone

from apps.e2e.tests_races import RaceCase, fixture, race
from apps.inventory.models import Lot

from .rolls import FabricRoll
from .station import LoomStation, record_roll
from .station_scale import ScaleReading
from .tests_station import StationTestCase


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class F10OneReadingOneRollTests(RaceCase):
    def test_two_looms_confirming_on_one_scale_reading_make_one_roll(self):
        made = fixture(self, StationTestCase)
        LoomStation.objects.filter(pk=made.station.pk).update(scale_code="SC-1", scale_bridged=True)
        station = LoomStation.objects.get(pk=made.station.pk)
        ScaleReading.objects.create(scale_code="SC-1", gross_kg=Decimal("106.8"), stable=True,
                                    read_at=timezone.now())
        outcomes = race(
            Lot,
            lambda: record_roll(station, made.operator, made.l17, None, made.core, Decimal("1006")),
            lambda: record_roll(station, made.operator, made.l20, None, made.core, Decimal("1006")))
        self.once(outcomes)
        self.assertEqual(FabricRoll.objects.count(), 1)
