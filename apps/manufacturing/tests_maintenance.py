"""
Hours a machine is not available, planned before they happen.

Downtime recorded a stoppage after the fact and nothing took an hour
out of the machine in advance, so a plan scheduled a run straight
through a service the plant fully intended to do.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .maintenance import MaintenanceJob, MaintenanceSchedule, due_now
from .shifts import Downtime, DowntimeReason
from .tests_orders import TODAY, RunTestCase


class MaintenanceTestCase(RunTestCase):
    def schedule(self, days=None, hours=None, minutes="480", last=None):
        return MaintenanceSchedule.objects.create(
            work_centre=self.loom, name="Gearbox service",
            every_days=days, every_run_hours=Decimal(hours) if hours else None,
            duration_minutes=Decimal(minutes),
            last_done_on=last,
        )

    def routed(self):
        """A run on the loom, so its bookings name a work centre."""
        from .routing import Routing, RoutingOperation

        if not hasattr(self, "_routing"):
            self._routing = Routing.objects.create(code="R-EXT", name="Extrude")
            RoutingOperation.objects.create(
                routing=self._routing, sequence=10, name="Extrude",
                work_centre=self.loom, setup_minutes=Decimal("0"),
                units_per_hour=Decimal("180"), rate_uom=self.kg,
            )
            self.bom.routing = self._routing
            self.bom.save()
        # A big run, so that booking a hundred hours against it does
        # not trip the operation's own time allowance — which is a
        # different guard doing its job, not this one failing.
        order = self.order("20000")
        order.release(TODAY)
        return order

    def book_hours(self, hours, on_date=None):
        """Machine time, through a real run on the loom."""
        from .orders import TimeBooking

        order = self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=on_date or TODAY,
            minutes=Decimal(hours) * 60,
        )
        booking.post()
        return booking


class TwoClocksTests(MaintenanceTestCase):
    def test_a_schedule_needs_at_least_one_clock(self):
        with self.assertRaises(Exception):
            MaintenanceSchedule.objects.create(
                work_centre=self.loom, name="Nothing",
                duration_minutes=Decimal("60"),
            )

    def test_never_done_is_due_now(self):
        schedule = self.schedule(days=90)
        self.assertEqual(schedule.due_on(TODAY), TODAY)
        self.assertTrue(schedule.is_due(TODAY))

    def test_the_calendar_clock_counts_from_when_it_was_last_done(self):
        schedule = self.schedule(days=90, last=TODAY)
        self.assertEqual(
            schedule.due_on(TODAY), TODAY + datetime.timedelta(days=90)
        )
        self.assertFalse(schedule.is_due(TODAY))
        self.assertTrue(schedule.is_due(TODAY + datetime.timedelta(days=90)))

    def test_the_running_hours_clock_reads_the_time_bookings(self):
        """
        Derived, never counted into a field: a second running total
        would be wrong the first time a booking was voided.
        """
        schedule = self.schedule(hours="500", last=TODAY)
        self.assertEqual(schedule.hours_remaining(), Decimal("500"))
        self.book_hours("100", TODAY + datetime.timedelta(days=1))
        self.assertEqual(schedule.hours_remaining(), Decimal("400"))
        self.assertFalse(schedule.is_due())

    def test_running_hours_can_make_it_due_early(self):
        """
        A machine that has run flat out for six weeks needs its
        shuttle changed whatever the calendar says.
        """
        schedule = self.schedule(days=90, hours="50", last=TODAY)
        self.book_hours("60", TODAY + datetime.timedelta(days=1))
        self.assertFalse(schedule.due_on(TODAY) <= TODAY)
        self.assertTrue(schedule.is_due(TODAY + datetime.timedelta(days=2)))

    def test_standing_still_does_not_postpone_the_calendar_clock(self):
        """
        A machine that has not run for six months still needs its
        gearbox done.
        """
        schedule = self.schedule(days=90, hours="500", last=TODAY)
        self.assertTrue(
            schedule.is_due(TODAY + datetime.timedelta(days=100))
        )

    def test_a_voided_booking_gives_the_hours_back(self):
        schedule = self.schedule(hours="500", last=TODAY)
        booking = self.book_hours("100", TODAY + datetime.timedelta(days=1))
        self.assertEqual(schedule.hours_remaining(), Decimal("400"))
        booking.void()
        self.assertEqual(schedule.hours_remaining(), Decimal("500"))


class PuttingItOnTheBoardTests(MaintenanceTestCase):
    def test_a_job_is_raised_for_the_date_it_is_due(self):
        schedule = self.schedule(days=90, last=TODAY)
        job = schedule.raise_job(as_of=TODAY)
        self.assertEqual(job.due_on, TODAY + datetime.timedelta(days=90))
        self.assertEqual(job.planned_minutes, Decimal("480"))
        self.assertTrue(job.is_open())

    def test_two_open_jobs_for_one_schedule_are_refused(self):
        """
        They would take the machine out twice and a planner cannot
        tell which is real.
        """
        schedule = self.schedule(days=90)
        schedule.raise_job(as_of=TODAY)
        with self.assertRaisesMessage(ValidationError, "take the machine out twice"):
            schedule.raise_job(as_of=TODAY)

    def test_what_is_due_and_not_yet_on_the_board(self):
        schedule = self.schedule(days=90)
        self.assertEqual(due_now(as_of=TODAY), [schedule])
        schedule.raise_job(as_of=TODAY)
        self.assertEqual(due_now(as_of=TODAY), [])

    def test_a_schedule_not_yet_due_is_not_listed(self):
        self.schedule(days=90, last=TODAY)
        self.assertEqual(due_now(as_of=TODAY), [])


class DoingItTests(MaintenanceTestCase):
    def test_completing_it_records_the_stoppage_where_every_other_one_goes(self):
        """
        Planned downtime is still downtime: a machine being serviced
        is a machine not weaving, and it belongs in effectiveness
        beside a breakdown.
        """
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        downtime = job.complete(on_date=TODAY)
        self.assertEqual(downtime.work_centre, self.loom)
        self.assertEqual(downtime.minutes, Decimal("480"))
        self.assertTrue(downtime.reason.is_planned)
        self.assertFalse(job.is_open())

    def test_it_records_what_it_really_took(self):
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        job.complete(on_date=TODAY, minutes=Decimal("600"))
        self.assertEqual(job.actual_minutes, Decimal("600"))
        self.assertEqual(job.downtime.minutes, Decimal("600"))

    def test_the_clock_restarts_from_the_day_it_was_done(self):
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        job.complete(on_date=TODAY)
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_done_on, TODAY)
        self.assertFalse(schedule.is_due(TODAY))

    def test_doing_it_twice_is_refused(self):
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        job.complete(on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "already done"):
            job.complete(on_date=TODAY)

    def test_a_service_that_took_no_time_is_refused(self):
        schedule = self.schedule(days=90)
        job = schedule.raise_job(as_of=TODAY)
        with self.assertRaisesMessage(ValidationError, "nobody did"):
            job.complete(on_date=TODAY, minutes=Decimal("0"))

    def test_a_one_off_repair_needs_no_schedule(self):
        job = MaintenanceJob.objects.create(
            work_centre=self.loom, due_on=TODAY,
            planned_minutes=Decimal("120"), notes="Bearing failure",
        )
        job.complete(on_date=TODAY)
        self.assertIsNotNone(job.downtime)
