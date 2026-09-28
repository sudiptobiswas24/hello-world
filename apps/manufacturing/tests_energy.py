"""
Loom L-17's meter, fitted on 1 Sep reading 10,000 kWh, read at the end
of each twelve-hour shift at 8.50 a kWh.

  1 Sep day    10,120   120 kWh   run A 600 min, run B 120 min
  1 Sep night  10,250   130 kWh   nothing booked: idle
  2 Sep day    (missed)           run A 300 min
  2 Sep night  10,500   250 kWh   covers both 2 Sep shifts; run B 600 min

  Run A: 120 x 600/720 + 250 x 300/900 = 183.333 kWh, 1,558.33.
  Run B: 120 x 120/720 + 250 x 600/900 = 186.667 kWh, 1,586.67.
  Idle: 130 kWh, 1,105.00. 183.333 + 186.667 + 130 = 500, the meter.
  Weaving's standard is 9.5 kWh an hour: run A's 15 hours should have
  drawn 142.5, and drew 40.833 more. Run A made 2,000 kg: 0.091667 kWh
  a kilogramme.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.inventory.models import Lot

from .energy import EnergyMeter, EnergyTariff, MeterReading, by_run, idle_energy, metered_total, run_energy
from .machines import Machine
from .orders import TimeBooking, WorkCentre
from .tests_station import StationTestCase

SEP = lambda day: datetime.date(2026, 9, day)  # noqa: E731


class EnergyTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.run, self.released_run()
        self.meter = EnergyMeter.objects.create(code="EM-L17", machine=self.l17,
                                                installed_on=SEP(1),
                                                initial_reading=Decimal("10000"))
        EnergyTariff.objects.create(valid_from=datetime.date(2026, 1, 1), rate=Decimal("8.50"))
        self.centre.standard_kwh_per_hour = Decimal("9.5")
        self.centre.save()

    def book(self, work_order, day, shift, minutes, machine=None):
        booking = TimeBooking.objects.create(
            work_order=work_order, operation=work_order.operations.first(), booking_date=day,
            shift=shift, machine=machine or self.l17, minutes=Decimal(minutes))
        booking.post()
        return booking

    def read(self, day, shift, value, meter=None):
        return MeterReading.objects.create(meter=meter or self.meter, shift_date=day,
                                           shift=shift, reading=Decimal(value))

    def the_two_days(self):
        self.book(self.a, SEP(1), self.day, "600")
        self.b_first = self.book(self.b, SEP(1), self.day, "120")
        self.book(self.a, SEP(2), self.day, "300")
        self.book(self.b, SEP(2), self.night, "600")
        self.read(SEP(1), self.day, "10120")
        self.middle = self.read(SEP(1), self.night, "10250")
        self.read(SEP(2), self.night, "10500")

    def kwh(self, value):
        return value.quantize(Decimal("0.001"))


class LaidOnTheRunsTests(EnergyTestCase):
    def test_each_shift_by_minutes_and_the_idle_one_apart(self):
        self.the_two_days()
        a, b = run_energy(self.a), run_energy(self.b)
        self.assertEqual((self.kwh(a["kwh"]), a["cost"].quantize(Decimal("0.01"))),
                         (Decimal("183.333"), Decimal("1558.33")))
        self.assertEqual((self.kwh(b["kwh"]), b["cost"].quantize(Decimal("0.01"))),
                         (Decimal("186.667"), Decimal("1586.67")))
        (idle,) = idle_energy(SEP(1), SEP(30))
        self.assertEqual((idle["shift_date"], idle["shift"], idle["kwh"], idle["cost"]),
                         (SEP(1), "N", Decimal("130.000"), Decimal("1105.00000")))

    def test_what_is_laid_out_foots_to_the_meter(self):
        self.the_two_days()
        laid = sum(by_run(self.meter).values()) + sum(row["kwh"] for row in idle_energy(SEP(1), SEP(30)))
        self.assertEqual(self.kwh(laid), self.kwh(metered_total(self.meter)))
        self.assertEqual(metered_total(self.meter), Decimal("500.000"))

    def test_against_the_standard_and_per_kilogramme(self):
        self.the_two_days()
        entry = self.produce(self.a, "2000", lot=Lot.objects.create(item=self.fabric, code="FAB-A"))
        entry.post()
        found = run_energy(self.a)
        self.assertEqual((found["standard_kwh"], self.kwh(found["variance_kwh"])),
                         (Decimal("142.5"), Decimal("40.833")))
        self.assertEqual(found["kwh_per_unit"].quantize(Decimal("0.000001")), Decimal("0.091667"))
        self.assertEqual(found["metered_minutes"], Decimal("900"))

    def test_a_voided_booking_gives_its_share_back(self):
        self.the_two_days()
        self.b_first.void()
        # 1 Sep day's 120 kWh is all run A's now: 120 + 83.333.
        self.assertEqual(self.kwh(run_energy(self.a)["kwh"]), Decimal("203.333"))

    def test_a_voided_reading_hands_its_shift_to_the_next(self):
        self.the_two_days()
        self.middle.void("Read off the wrong meter")
        # 380 kWh from 1 Sep day to 2 Sep night: run A's 300 of 900 minutes.
        self.assertEqual(self.kwh(run_energy(self.a)["kwh"]), Decimal("226.667"))
        self.assertEqual(idle_energy(SEP(1), SEP(30)), [])

    def test_each_day_at_its_own_tariff(self):
        EnergyTariff.objects.create(valid_from=SEP(16), rate=Decimal("9.00"))
        self.book(self.a, SEP(15), self.night, "300")
        self.book(self.a, SEP(16), self.day, "300")
        self.read(SEP(16), self.day, "10200")
        self.assertEqual(run_energy(self.a)["cost"], Decimal("1750.00"))

    def test_a_meter_behind_transformers(self):
        meter = EnergyMeter.objects.create(code="EM-L20", machine=self.l20, installed_on=SEP(1),
                                           initial_reading=Decimal("250"),
                                           multiplier=Decimal("40"))
        self.book(self.a, SEP(1), self.day, "600", machine=self.l20)
        self.read(SEP(1), self.day, "253", meter=meter)
        self.assertEqual(run_energy(self.a)["kwh"], Decimal("120.000"))

    def test_what_the_meters_cannot_see_is_said(self):
        self.book(self.a, SEP(1), self.day, "600")
        self.read(SEP(1), self.day, "10120")
        self.book(self.a, SEP(3), self.day, "200")
        found = run_energy(self.a)
        self.assertEqual((found["metered_minutes"], found["unmetered_minutes"]),
                         (Decimal("600"), Decimal("200")))
        # The standard is for the metered 10 hours only: 95 kWh, not 126.667.
        self.assertEqual(found["standard_kwh"], Decimal("95.0"))

    def test_another_looms_shift_takes_none_of_this_meter(self):
        self.book(self.a, SEP(1), self.day, "600")
        self.book(self.b, SEP(1), self.day, "600", machine=self.l20)
        self.read(SEP(1), self.day, "10120")
        self.assertEqual(run_energy(self.a)["kwh"], Decimal("120.000"))
        self.assertEqual(run_energy(self.b)["kwh"], Decimal("0"))

    def test_no_tariff_is_no_cost_and_says_so(self):
        EnergyTariff.objects.all().delete()
        self.book(self.a, SEP(1), self.day, "600")
        self.read(SEP(1), self.day, "10120")
        found = run_energy(self.a)
        self.assertIsNone(found["cost"])
        self.assertIn("No tariff in force on 2026-09-01", found["warnings"][0])

    def test_idle_in_the_window_asked_for(self):
        self.the_two_days()
        self.assertEqual(idle_energy(SEP(2), SEP(30)), [])

    def test_a_work_centre_meter_takes_only_its_own_centres_bookings(self):
        cutting = WorkCentre.objects.create(code="CUT", name="Cutting")
        meter = EnergyMeter.objects.create(code="EM-CUT", work_centre=cutting,
                                           installed_on=SEP(1))
        self.book(self.a, SEP(1), self.day, "600")
        self.read(SEP(1), self.day, "40", meter=meter)
        (idle,) = idle_energy(SEP(1), SEP(1))
        self.assertEqual((idle["meter"], idle["kwh"]), ("EM-CUT", Decimal("40.000")))

    def test_a_meter_on_a_whole_work_centre(self):
        self.meter.delete()
        centre_meter = EnergyMeter.objects.create(code="EM-WEAVE", work_centre=self.centre,
                                                  installed_on=SEP(1))
        self.book(self.a, SEP(1), self.day, "600", machine=self.l17)
        self.book(self.b, SEP(1), self.day, "600", machine=self.l20)
        self.read(SEP(1), self.day, "240", meter=centre_meter)
        self.assertEqual(run_energy(self.a)["kwh"], Decimal("120.000"))


class ReadingsTests(EnergyTestCase):
    def test_a_meter_does_not_run_backwards(self):
        self.read(SEP(1), self.day, "10120")
        with self.assertRaisesMessage(ValidationError, "does not run backwards"):
            self.read(SEP(1), self.night, "10100")
        with self.assertRaisesMessage(ValidationError, "does not run backwards"):
            self.read(SEP(1), self.night, "9999")

    def test_a_late_reading_fits_between_its_neighbours(self):
        self.read(SEP(1), self.day, "10120")
        self.read(SEP(2), self.day, "10300")
        with self.assertRaisesMessage(ValidationError, "read 10300.000 after this"):
            self.read(SEP(1), self.night, "10400")
        self.read(SEP(1), self.night, "10200")

    def test_one_reading_a_shift_until_it_is_voided(self):
        first = self.read(SEP(1), self.day, "10120")
        with self.assertRaisesMessage(ValidationError, "already read for that shift"):
            self.read(SEP(1), self.day, "10130")
        with self.assertRaisesMessage(ValidationError, "Say why"):
            first.void(" ")
        first.void("Misread")
        self.read(SEP(1), self.day, "10130")

    def test_not_edited_not_deleted(self):
        reading = self.read(SEP(1), self.day, "10120")
        reading.reading = Decimal("10121")
        with self.assertRaisesMessage(ValidationError, "Void it and read again"):
            reading.save()
        with self.assertRaisesMessage(ValidationError, "Void it"):
            reading.delete()

    def test_only_while_the_meter_is_there(self):
        with self.assertRaisesMessage(ValidationError, "not there on 2026-08-31"):
            self.read(datetime.date(2026, 8, 31), self.day, "10010")
        self.meter.retired_on = SEP(5)
        self.meter.save()
        with self.assertRaisesMessage(ValidationError, "not there on 2026-09-06"):
            self.read(SEP(6), self.day, "10010")


class MeterTests(EnergyTestCase):
    def test_what_it_was_read_against_does_not_change(self):
        self.read(SEP(2), self.day, "10120")
        self.meter.multiplier = Decimal("40")
        with self.assertRaisesMessage(ValidationError, "its multiplier is what"):
            self.meter.save()
        self.meter.refresh_from_db()
        self.meter.retired_on = SEP(1)
        with self.assertRaisesMessage(ValidationError, "cannot have been retired on 2026-09-01"):
            self.meter.save()
        with self.assertRaisesMessage(ValidationError, "retire it instead"):
            self.meter.delete()

    def test_one_machine_one_meter_at_a_time(self):
        with self.assertRaisesMessage(ValidationError, "count its shifts twice"):
            EnergyMeter.objects.create(code="EM-L17B", machine=self.l17, installed_on=SEP(10))
        with self.assertRaisesMessage(ValidationError, "count its shifts twice"):
            EnergyMeter.objects.create(code="EM-WEAVE", work_centre=self.centre,
                                       installed_on=SEP(10))
        self.meter.retired_on = SEP(9)
        self.meter.save()
        EnergyMeter.objects.create(code="EM-L17B", machine=self.l17, installed_on=SEP(10))

    def test_a_machine_meter_inside_a_metered_centre(self):
        self.meter.delete()
        EnergyMeter.objects.create(code="EM-WEAVE", work_centre=self.centre, installed_on=SEP(1))
        with self.assertRaisesMessage(ValidationError, "count its shifts twice"):
            EnergyMeter.objects.create(code="EM-L17", machine=self.l17, installed_on=SEP(5))

    def test_on_one_thing(self):
        with self.assertRaisesMessage(ValidationError, "one machine or on one work centre"):
            EnergyMeter.objects.create(code="EM-X", installed_on=SEP(1))
        with self.assertRaisesMessage(ValidationError, "multiplier is more than nothing"):
            EnergyMeter.objects.create(code="EM-Y", machine=Machine.objects.create(
                work_centre=self.centre, code="L-99"), installed_on=SEP(1),
                multiplier=Decimal("0"))


class EnergyApiTests(EnergyTestCase):
    def test_read_voided_and_reported(self):
        self.book(self.a, SEP(1), self.day, "600")
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("energy"))
        response = client.post("/api/manufacturing/meter-readings/", {
            "meter": self.meter.pk, "shift_date": "2026-09-01", "shift": self.day.pk,
            "reading": "10120"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = client.post("/api/manufacturing/meter-readings/", {
            "meter": self.meter.pk, "shift_date": "2026-09-01", "shift": self.night.pk,
            "reading": "10100"}, format="json")
        self.assertEqual(response.status_code, 400)
        body = client.get(f"/api/manufacturing/work-orders/{self.a.pk}/energy/").json()
        self.assertEqual((body["kwh"], body["cost"], body["standard_kwh"], body["variance_kwh"]),
                         ("120.000", "1020.00", "95.000", "25.000"))
        reading = MeterReading.objects.get()
        response = client.post(f"/api/manufacturing/meter-readings/{reading.pk}/void/",
                               {"reason": "Misread"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.patch(f"/api/manufacturing/meter-readings/{reading.pk}/",
                                      {"reading": "1"}, format="json").status_code, 405)
        response = client.get("/api/manufacturing/energy-meters/idle/?start=2026-09-01&end=2026-09-30")
        self.assertEqual(response.status_code, 200)

    def test_the_meter_balances(self):
        self.the_two_days()
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("energy"))
        body = client.get(f"/api/manufacturing/energy-meters/{self.meter.pk}/balance/").json()
        self.assertEqual(body, {"metered": "500.000",
                                "runs": {self.a.number: "183.333", self.b.number: "186.667"},
                                "idle": "130.000", "not_laid_out": "0.000"})
