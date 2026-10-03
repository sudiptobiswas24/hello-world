"""
C-1's clock on the bag run: started at 16:00 by the operator, joined at
17:00 by operator B, stopped at 22:00 with 1,200 sacks made. The day
shift runs 08:00 to 20:00, the night 20:00 to 08:00: 240 minutes on the
day shift and 120 on the night, both crew named on each, the 1,200 on
the last.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .station_clock import MachineClock, join_clock, start_clock, stop_clock, void_clock
from .tests_bag_counts import BagStationApiTests, ConversionTestCase
from .tests_orders import TODAY
from .tests_station import at


class ClockTests(ConversionTestCase):
    def test_one_clock_per_machine_split_at_the_shift_change(self):
        start_clock(self.cv, self.operator, self.c1, at=at(TODAY, 16))
        join_clock(self.cv, self.other, self.c1, at=at(TODAY, 17))
        clock = stop_clock(self.cv, self.other, self.c1, "1200", at=at(TODAY, 22))
        rows = [(booking.minutes, booking.shift, booking.booking_date, booking.machine,
                 booking.quantity_completed, booking.posted,
                 sorted(person.pk for person in booking.operators.all()))
                for booking in clock.bookings.order_by("started_at")]
        crew = sorted([self.operator.pk, self.other.pk])
        self.assertEqual(rows, [
            (Decimal("240.00"), self.day, TODAY, self.c1, None, True, crew),
            (Decimal("120.00"), self.night, TODAY, self.c1, Decimal("1200.0000"), True, crew),
        ])
        self.assertEqual((clock.stopped_by, clock.operation.work_order), (self.other,
                                                                           self.bag_run))

    def test_what_a_clock_will_not_do(self):
        from .machines import Machine

        stranger = Machine.objects.create(work_centre=self.cutting, code="C-9")
        with self.assertRaisesMessage(ValidationError, "does not serve C-9"):
            start_clock(self.cv, self.operator, stranger, at=at(TODAY, 9))
        with self.assertRaisesMessage(ValidationError, "is not running; start it"):
            join_clock(self.cv, self.other, self.c1, at=at(TODAY, 9))
        with self.assertRaisesMessage(ValidationError, "is not running."):
            stop_clock(self.cv, self.other, self.c1, at=at(TODAY, 9))
        start_clock(self.cv, self.operator, self.c1, at=at(TODAY, 10))
        with self.assertRaisesMessage(ValidationError, "running since 10:00; join it"):
            start_clock(self.cv, self.other, self.c1, at=at(TODAY, 11))
        with self.assertRaisesMessage(ValidationError, "stops after it started"):
            stop_clock(self.cv, self.operator, self.c1, at=at(TODAY, 10))
        with self.assertRaisesMessage(ValidationError, "What was made is more than nothing"):
            stop_clock(self.cv, self.operator, self.c1, "0", at=at(TODAY, 11))

    def test_a_clock_left_running_needs_a_supervisor_to_stop(self):
        import datetime

        start_clock(self.cv, self.operator, self.c1, at=at(TODAY, 10))
        later = at(TODAY + datetime.timedelta(days=1), 11)
        with self.assertRaisesMessage(ValidationError, "needs a supervisor's PIN"):
            stop_clock(self.cv, self.operator, self.c1, at=later)
        # The run is planned at 500 minutes and allows half as much again:
        # 1,500 minutes of clock is refused as any booking that long is,
        # and the clock is still running.
        with self.assertRaisesMessage(ValidationError, "allows 50.000% over"):
            stop_clock(self.cv, self.operator, self.c1, supervisor=self.supervisor, at=later)
        self.assertTrue(MachineClock.objects.filter(stopped_at__isnull=True).exists())
        type(self.bag_run).objects.filter(pk=self.bag_run.pk).update(
            time_allowance_percent=Decimal("300"))
        clock = stop_clock(self.cv, self.operator, self.c1, supervisor=self.supervisor,
                           at=later)
        # 10:00 to 20:00, the night through to 08:00, then 08:00 to 11:00.
        self.assertEqual([booking.minutes for booking in clock.bookings.order_by("started_at")],
                         [Decimal("600.00"), Decimal("720.00"), Decimal("180.00")])

    def test_withdrawn_together_with_a_supervisors_pin(self):
        start_clock(self.cv, self.operator, self.c1, at=at(TODAY, 16))
        clock = stop_clock(self.cv, self.operator, self.c1, at=at(TODAY, 22))
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_clock(clock, self.cv, self.supervisor, self.operator, " ")
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_clock(clock, self.cv, self.operator, self.operator, "Wrong machine")
        void_clock(clock, self.cv, self.supervisor, self.operator, "Wrong machine")
        self.assertTrue(all(booking.voided_at for booking in clock.bookings.all()))
        with self.assertRaisesMessage(ValidationError, "not a stopped, standing clock"):
            void_clock(clock, self.cv, self.supervisor, self.operator, "Again")
        running = start_clock(self.cv, self.operator, self.c2, at=at(TODAY, 16))
        with self.assertRaisesMessage(ValidationError, "not a stopped, standing clock"):
            void_clock(running, self.cv, self.supervisor, self.operator, "x")

    def test_only_this_stations_clocks(self):
        from .station import LoomStation

        start_clock(self.cv, self.operator, self.c1, at=at(TODAY, 16))
        clock = stop_clock(self.cv, self.operator, self.c1, at=at(TODAY, 17))
        other = LoomStation.objects.create(code="CV-2", name="Two", warehouse=self.plant)
        other.supervisors.add(self.supervisor)
        with self.assertRaisesMessage(ValidationError, "was not clocked at CV-2"):
            void_clock(clock, other, self.supervisor, self.operator, "x")
        self.assertEqual(MachineClock.objects.count(), 1)


class ClockApiTests(BagStationApiTests):
    def test_started_joined_and_stopped_at_the_station(self):
        self.post_cv("sign-in/", {"pin": self.pin})
        response = self.post_cv("clock/start/", {"machine": "C-1"})
        self.assertEqual(response.status_code, 201, response.content)
        response = self.post_cv("clock/start/", {"machine": "C-1"})
        self.assertEqual(response.status_code, 400)
        response = self.post_cv("clock/join/", {"machine": "C-1"})
        self.assertEqual(response.status_code, 200, response.content)
        MachineClock.objects.update(started_at=MachineClock.objects.get().started_at
                                    - __import__("datetime").timedelta(minutes=30))
        response = self.post_cv("clock/stop/", {"machine": "C-1", "quantity": "200"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["run"], len(body["bookings"])), (self.bag_run.number, 1))
        response = self.post_cv(f"clock/{body['id']}/void/",
                                {"supervisor_pin": self.supervisor_pin, "reason": "Test"})
        self.assertEqual(response.status_code, 200, response.content)
