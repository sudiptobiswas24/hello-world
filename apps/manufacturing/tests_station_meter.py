"""
The meter read from the station at 10:42 on the day shift: the operator
copies the board's three dials (10,120 kWh, 412.5 kVA maximum demand,
power factor 0.93) against the meter on loom L-17. Only a meter on this
station's lines is offered; a power factor over 1 and a second reading
in the same shift are refused; nobody signed in records nothing.
"""

from decimal import Decimal

from rest_framework.test import APIClient

from .energy import EnergyMeter, MeterReading
from .machines import Machine
from .orders import WorkCentre
from .tests_orders import TODAY
from .tests_station_api import StationApiTestCase


class StationMeterTests(StationApiTestCase):
    def setUp(self):
        super().setUp()
        self.meter = EnergyMeter.objects.create(code="EM-L17", machine=self.l17, installed_on=TODAY,
                                                initial_reading=Decimal("10000"))
        elsewhere = WorkCentre.objects.create(code="PRINT", name="Printing")
        press = Machine.objects.create(work_centre=elsewhere, code="PR-1")
        EnergyMeter.objects.create(code="EM-PR1", machine=press, installed_on=TODAY, initial_reading=Decimal("0"))

    def dials(self, **extra):
        values = {"meter": "EM-L17", "reading": "10120", "max_demand_kva": "412.5", "power_factor": "0.93"}
        values.update(extra)
        return self.post("meter/", values)

    def test_refused_before_it_is_kept(self):
        stranger = APIClient()
        stranger.force_authenticate(self.device)
        refused = stranger.post(self.base + "meter/", {"meter": "EM-L17", "reading": "10120"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Sign in with your PIN first", refused.content.decode())

        self.sign_in()
        self.assertEqual(self.dials(meter="EM-PR1").status_code, 400)
        self.assertIn("on this station's lines", self.dials(meter="EM-9").content.decode())
        over = self.dials(power_factor="1.2")
        self.assertEqual(over.status_code, 400)
        self.assertIn("over 0 and up to 1", over.content.decode())
        self.assertEqual(self.dials(reading="ten").status_code, 400)
        self.assertFalse(MeterReading.objects.exists())

    def test_the_three_dials_against_the_shift_and_the_meter_offered(self):
        self.sign_in()
        offered = self.client.get(self.base).json()["meters"]
        self.assertEqual([row["code"] for row in offered], ["EM-L17"])
        kept = self.dials()
        self.assertEqual(kept.status_code, 201, kept.content)
        self.assertEqual((kept.json()["meter"], kept.json()["reading"], kept.json()["shift"]),
                         ("EM-L17", "10120", "D"))
        reading = MeterReading.objects.get()
        self.assertEqual((reading.meter, reading.shift_date, reading.shift, reading.read_by,
                          reading.max_demand_kva, reading.power_factor),
                         (self.meter, TODAY, self.day, self.operator, Decimal("412.50"), Decimal("0.930")))
        # The same shift read twice is the same reading twice.
        self.assertEqual(self.dials(reading="10121").status_code, 400)
        self.assertEqual(MeterReading.objects.count(), 1)

    def test_the_two_dials_may_be_left_blank(self):
        self.sign_in()
        kept = self.dials(max_demand_kva="", power_factor="")
        self.assertEqual(kept.status_code, 201, kept.content)
        reading = MeterReading.objects.get()
        self.assertEqual((reading.max_demand_kva, reading.power_factor), (None, None))
