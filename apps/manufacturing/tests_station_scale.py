"""
Scale SC-1 at loom exit LX-1, bridged. A roll of 104.4 kg on a 2.4 kg
core reads 106.8 kg gross. Put on at 10:42:00 and confirmed at 10:42:30,
it is weighed from the reading; confirmed again on the same reading it
is refused as the same roll twice. A reading from 10:41:30 is exactly a
minute old at 10:42:30 and taken; one from 10:41:29 is not.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from .station import LineKind, LoomStation
from .station_scale import ScaleReading, post_reading
from .station_tape import record_tape
from .tests_orders import TODAY
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase
from .tests_station_tape import ON, build_tape_line

CONFIRM = at(TODAY, 10, 42) + datetime.timedelta(seconds=30)


def moment(minute, second=0):
    return at(TODAY, 10, minute) + datetime.timedelta(seconds=second)


class BridgedTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        LoomStation.objects.filter(pk=self.station.pk).update(scale_code="SC-1",
                                                               scale_bridged=True)
        self.station.refresh_from_db()

    def read(self, kg="106.8", stable=True, when=None, scale="SC-1"):
        return post_reading(scale, kg, stable, at=when or moment(42))

    def weigh_it(self, gross=None, when=CONFIRM, **extra):
        return self.weigh(gross=gross, when=when, **extra)

    def weigh(self, gross="106.8", declared="1006", machine=None, when=None, **extra):
        from .station import record_roll

        return record_roll(self.station, extra.pop("operator", self.operator),
                           machine or self.l17, gross, self.core, Decimal(declared),
                           at=when or CONFIRM, **extra)


class WeighedFromTheScaleTests(BridgedTestCase):
    def test_the_weight_is_the_scales(self):
        reading = self.read()
        roll = self.weigh_it()
        self.assertEqual((roll.net_weight_kg, roll.weight_source, roll.scale_reading),
                         (Decimal("104.400"), "scale", reading))
        with self.assertRaisesMessage(ValidationError,
                                      f"This reading already weighed {roll.lot.code}"):
            self.weigh_it(when=CONFIRM + datetime.timedelta(seconds=10))

    def test_a_figure_sent_with_it_must_agree(self):
        self.read()
        with self.assertRaisesMessage(ValidationError, "Scale SC-1 reads 106.800 kg, not 107.0"):
            self.weigh_it("107.0")
        self.assertEqual(self.weigh_it("106.8").net_weight_kg, Decimal("104.400"))

    def test_a_minute_old_and_no_older(self):
        self.read(when=moment(41, 29))
        with self.assertRaisesMessage(ValidationError, "last reported at 10:41:29"):
            self.weigh_it()
        self.read(when=moment(41, 30))
        self.assertIsNotNone(self.weigh_it().scale_reading)

    def test_what_is_not_a_weight(self):
        with self.assertRaisesMessage(ValidationError, "Scale SC-1 has reported nothing"):
            self.weigh_it()
        self.read(scale="SC-2")
        with self.assertRaisesMessage(ValidationError, "Scale SC-1 has reported nothing"):
            self.weigh_it()
        self.read(when=moment(43))
        with self.assertRaisesMessage(ValidationError, "Scale SC-1 has reported nothing"):
            self.weigh_it()
        self.read(when=moment(42))
        self.read("80", stable=False, when=moment(42, 10))
        with self.assertRaisesMessage(ValidationError, "has not settled"):
            self.weigh_it()
        self.read("0", when=moment(42, 20))
        with self.assertRaisesMessage(ValidationError, "reads 0.000 kg: nothing is on it"):
            self.weigh_it()

    def test_typed_with_a_supervisor_when_the_scale_is_down(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.weigh_it("106.8", source="manual")
        with self.assertRaisesMessage(ValidationError, "'banana' is not a reason"):
            self.weigh_it("106.8", source="manual", supervisor=self.supervisor,
                          reason="banana")
        roll = self.weigh_it("106.8", source="manual", supervisor=self.supervisor,
                             reason="scale_offline")
        self.assertEqual((roll.weight_source, roll.scale_reading, roll.approved_by),
                         ("manual", None, self.supervisor))

    def test_a_bridged_station_names_its_scale(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            LoomStation.objects.filter(pk=self.station.pk).update(scale_code="")

    def test_an_unbridged_station_takes_the_figure_it_is_sent(self):
        LoomStation.objects.filter(pk=self.station.pk).update(scale_bridged=False)
        self.station.refresh_from_db()
        roll = self.weigh_it("106.8")
        self.assertEqual((roll.weight_source, roll.scale_reading), ("scale", None))
        with self.assertRaisesMessage(ValidationError, "Enter the gross weight"):
            self.weigh_it()
        for figure in ("heavy", "NaN"):
            with self.assertRaisesMessage(ValidationError, "The gross weight is a number"):
                self.weigh_it(figure)


class BridgedTapeTests(BridgedTestCase):
    def setUp(self):
        super().setUp()
        build_tape_line(self)
        LoomStation.objects.filter(pk=self.tx.pk).update(scale_code="SC-1",
                                                          scale_bridged=True)
        self.tx.refresh_from_db()

    def doff(self, gross=None, **extra):
        return record_tape(self.tx, self.operator, self.e1, gross, 8, self.bobbin, ON,
                           at=CONFIRM, **extra)

    def test_a_doff_weighed_from_the_scale(self):
        reading = self.read("60.0")
        doff = self.doff()
        self.assertEqual((doff.net_kg, doff.weight_source, doff.scale_reading),
                         (Decimal("56.000"), "scale", reading))

    def test_one_reading_weighs_one_thing_whatever_it_is(self):
        self.read()
        roll = self.weigh_it()
        with self.assertRaisesMessage(ValidationError, f"already weighed {roll.lot.code}"):
            self.doff()
        self.read("60.0", when=moment(42, 5))
        doff = self.doff()
        with self.assertRaisesMessage(ValidationError, f"already weighed {doff.lot.code}"):
            self.weigh_it()

    def test_a_denier_concession_does_not_make_the_weight_typed(self):
        from .tests_station_tape import OFF

        self.read("60.0")
        doff = record_tape(self.tx, self.operator, self.e1, None, 8, self.bobbin, OFF,
                           at=CONFIRM, supervisor=self.supervisor, reason="Heavy is fine")
        self.assertEqual((doff.weight_source, doff.weight_approved_by, doff.conceded_by),
                         ("scale", None, self.supervisor))

    def test_typed_with_a_supervisor_and_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            self.doff("60.0", source="manual")
        with self.assertRaisesMessage(ValidationError, "Say what the other reason was"):
            self.doff("60.0", source="manual", supervisor=self.supervisor,
                      typed_reason="other")
        with self.assertRaisesMessage(ValidationError, "'guess' is not a weight source"):
            self.doff("60.0", source="guess")
        doff = self.doff("60.0", source="manual", supervisor=self.supervisor,
                         typed_reason="other", typed_note="  Platform cable cut ")
        self.assertEqual((doff.weight_source, doff.weight_approved_by, doff.typed_reason,
                          doff.typed_note, doff.scale_reading, doff.conceded_by),
                         ("manual", self.supervisor, "other", "Platform cable cut", None,
                          None))


class BridgeApiTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        LoomStation.objects.filter(pk=self.station.pk).update(scale_code="SC-1",
                                                               scale_bridged=True)
        stamp = patch("apps.manufacturing.station_scale.timezone.now",
                      return_value=at(TODAY, 10, 42))
        stamp.start()
        self.addCleanup(stamp.stop)
        self.bridge = APIClient()
        user = User.objects.create_user("bridge-sc1")
        user.user_permissions.add(Permission.objects.get(codename="add_scalereading"))
        self.bridge.force_authenticate(user)

    def post_reading(self, **data):
        data = {"scale": "SC-1", "gross_kg": "106.8", "stable": True, **data}
        return self.bridge.post("/api/manufacturing/scale-readings/", data, format="json")

    def test_the_bridge_posts_and_the_station_weighs_from_it(self):
        self.sign_in()
        self.assertEqual(self.client.get(self.base + "scale/").json()["refusal"],
                         "Scale SC-1 has reported nothing. Check its bridge, or type the "
                         "weight with a supervisor's PIN.")
        response = self.post_reading()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["gross_kg"], "106.8")
        body = self.client.get(self.base + "scale/").json()
        self.assertEqual((body["gross_kg"], body["stable"], body["usable"]),
                         ("106.800", True, True))
        response = self.roll(gross_kg="")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["gross_kg"], response.json()["source"]),
                         ("106.800", "scale"))
        body = self.client.get(self.base + "scale/").json()
        self.assertFalse(body["usable"])
        self.assertIn("already weighed", body["refusal"])

    def test_what_the_bridge_may_post(self):
        for data, message in (({"gross_kg": "NaN"}, "is a number of kilogrammes"),
                              ({"gross_kg": "heavy"}, "is a number of kilogrammes"),
                              ({"stable": "yes"}, "true or false"),
                              ({"scale": " "}, "which scale")):
            response = self.post_reading(**data)
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.json()[0])
        stranger = APIClient()
        stranger.force_authenticate(self.device)
        self.assertEqual(stranger.post("/api/manufacturing/scale-readings/",
                                       {"scale": "SC-1", "gross_kg": "1", "stable": True},
                                       format="json").status_code, 403)

    def test_an_unbridged_station_says_so(self):
        LoomStation.objects.filter(pk=self.station.pk).update(scale_bridged=False)
        self.sign_in()
        self.assertEqual(self.client.get(self.base + "scale/").json(), {"bridged": False})
