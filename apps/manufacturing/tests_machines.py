"""
Which loom, by name.

A work centre was both the bank and the machine, and its docstring
said "which loom is the identity of a run" over a schema that could
not hold a loom. These are the questions that were unanswerable and
now are: what can this bank really do on a Sunday, which loom ate the
polymer, which one is due a beam change, and which one is dragging the
shed's effectiveness down.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from . import oee
from .machines import Machine, machines_in
from .maintenance import MaintenanceSchedule
from .orders import ProductionEntry, TimeBooking, WorkCentre
from .routing import Routing, RoutingOperation, capacity_report
from .shifts import Downtime, DowntimeReason, Shift
from .tests_orders import TODAY, RunTestCase


class MachineTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.loom.available_hours_per_day = Decimal("24")
        self.loom.working_days = "1234567"
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()

    def machine(self, code, **kwargs):
        kwargs.setdefault("work_centre", self.loom)
        return Machine.objects.create(code=code, **kwargs)

    def routed(self, quantity="20000"):
        """A run on the loom, so its bookings name a work centre."""
        if not hasattr(self, "_routing"):
            self._routing = Routing.objects.create(code="R-EXT", name="Extrude")
            RoutingOperation.objects.create(
                routing=self._routing, sequence=10, name="Extrude",
                work_centre=self.loom, setup_minutes=Decimal("0"),
                units_per_hour=Decimal("180"), rate_uom=self.kg,
            )
            self.bom.routing = self._routing
            self.bom.save()
        order = self.order(quantity)
        order.release(TODAY)
        return order


class ABankIsWhatItsMachinesCanDoTests(MachineTestCase):
    def test_a_centre_with_no_machines_is_its_own_single_machine(self):
        """The fallback, which is most plants and every existing fixture."""
        week = datetime.timedelta(days=6)
        self.assertEqual(
            self.loom.minutes_available(TODAY, TODAY + week),
            Decimal("7") * 24 * 60,
        )

    def test_a_bank_of_three_has_three_machines_worth_of_minutes(self):
        for code in ("L-01", "L-02", "L-03"):
            self.machine(code)
        self.assertEqual(
            self.loom.minutes_on(TODAY), Decimal("3") * 24 * 60
        )

    def test_the_banks_own_hours_stop_being_read_once_it_has_machines(self):
        """
        Two answers to one question is the bug. A bank listing four
        looms at twelve hours each does twenty-four hours a day, not
        the twenty-four its own field still says.
        """
        for code in ("L-01", "L-02"):
            self.machine(code, available_hours_per_day=Decimal("12"))
        self.assertEqual(self.loom.available_hours_per_day, Decimal("24"))
        self.assertEqual(self.loom.minutes_on(TODAY), Decimal("24") * 60)

    def test_a_machine_on_its_own_pattern_is_not_the_banks_pattern(self):
        """
        Eleven continuous looms and one kept on days is not twelve
        continuous looms. TODAY is a Monday; the Sunday after it is
        the day the difference shows.
        """
        self.machine("L-01")
        self.machine("L-02", working_days="12345")
        sunday = TODAY + datetime.timedelta(days=6)
        self.assertEqual(sunday.isoweekday(), 7)
        self.assertEqual(self.loom.minutes_on(TODAY), Decimal("2") * 24 * 60)
        self.assertEqual(self.loom.minutes_on(sunday), Decimal("24") * 60)

    def test_an_inactive_machine_takes_its_hours_with_it(self):
        self.machine("L-01")
        gone = self.machine("L-02")
        self.assertEqual(self.loom.minutes_on(TODAY), Decimal("2") * 24 * 60)
        gone.is_active = False
        gone.save()
        self.assertEqual(self.loom.minutes_on(TODAY), Decimal("24") * 60)
        self.assertEqual([m.code for m in machines_in(self.loom)], ["L-01"])

    def test_a_sold_machine_answers_nothing_when_asked_directly(self):
        """
        The bank filters inactive machines out before it ever asks
        them, so the guard inside the machine is only reachable by
        asking one directly — which the API does. Tested where it is
        reachable rather than through the caller that never reaches
        it.
        """
        gone = self.machine("L-01", is_active=False)
        self.assertEqual(gone.minutes_on(TODAY), Decimal("0"))
        self.assertEqual(
            gone.minutes_available(TODAY, TODAY + datetime.timedelta(days=6)),
            Decimal("0"),
        )

    def test_the_capacity_report_counts_the_machines(self):
        for code in ("L-01", "L-02"):
            self.machine(code)
        report = capacity_report(self.loom, TODAY, TODAY)
        self.assertEqual(report["working_days"], 1)
        self.assertEqual(report["available_minutes"], Decimal("2") * 24 * 60)

    def test_a_window_that_runs_backwards_is_refused(self):
        self.machine("L-01")
        with self.assertRaises(ValidationError):
            self.loom.minutes_available(TODAY, TODAY - datetime.timedelta(1))
        with self.assertRaises(ValidationError):
            Machine.objects.get(code="L-01").minutes_available(
                TODAY, TODAY - datetime.timedelta(1)
            )


class WhatAMachineFallsBackToTests(MachineTestCase):
    def test_hours_and_pattern_come_from_the_bank_when_unstated(self):
        self.loom.available_hours_per_day = Decimal("16")
        self.loom.working_days = "123456"
        self.loom.save()
        machine = self.machine("L-01")
        self.assertEqual(machine.hours_per_day(), Decimal("16"))
        self.assertEqual(machine.days_pattern(), "123456")

    def test_a_machine_states_its_own_where_it_differs(self):
        machine = self.machine(
            "L-01", available_hours_per_day=Decimal("8"), working_days="12345"
        )
        self.assertEqual(machine.hours_per_day(), Decimal("8"))
        self.assertEqual(machine.days_pattern(), "12345")

    def test_the_rate_falls_back_to_the_bank(self):
        machine = self.machine("L-01")
        self.assertEqual(machine.rate_per_hour(), (Decimal("180"), self.kg))

    def test_a_slower_loom_says_so(self):
        machine = self.machine(
            "L-01", capacity_per_hour=Decimal("150"), capacity_uom=self.kg
        )
        self.assertEqual(machine.rate_per_hour(), (Decimal("150"), self.kg))

    def test_an_unrated_machine_in_an_unrated_bank_answers_nothing(self):
        """
        Rather than raising. Listing a bank must not fail because one
        of its machines has no speed on it; the caller that needs a
        rate refuses for itself and says what it was scheduling.
        """
        bare = WorkCentre.objects.create(code="BARE", name="Unrated")
        machine = Machine.objects.create(work_centre=bare, code="X-01")
        self.assertEqual(machine.rate_per_hour(), (None, None))

    def test_a_rate_must_say_what_it_counts(self):
        with self.assertRaises(Exception):
            Machine.objects.create(
                work_centre=self.loom, code="L-09",
                capacity_per_hour=Decimal("150"),
            )

    def test_an_unreadable_pattern_is_refused_where_it_is_typed(self):
        with self.assertRaises(ValidationError):
            self.machine("L-01", working_days="banana")

    def test_the_holiday_region_is_the_banks_and_not_overridable(self):
        """Not a field. A loom does not observe a different Diwali."""
        self.assertFalse(
            any(f.name == "holiday_region" for f in Machine._meta.get_fields())
        )


class AMachineBelongsToOneBankTests(MachineTestCase):
    def setUp(self):
        super().setUp()
        self.other = WorkCentre.objects.create(code="CUT-1", name="Cutting")
        self.stranger = Machine.objects.create(
            work_centre=self.other, code="C-01"
        )

    def test_an_operation_cannot_name_a_machine_in_another_bank(self):
        order = self.routed()
        operation = order.operations.get()
        operation.machine = self.stranger
        with self.assertRaisesMessage(ValidationError, "belongs to CUT-1"):
            operation.save()

    def test_the_rest_of_a_released_operation_is_still_frozen(self):
        """
        The other half of widening that guard. Letting the loom move
        must not let the rate move with it.
        """
        order = self.routed()
        operation = order.operations.get()
        operation.units_per_hour = Decimal("999")
        with self.assertRaisesMessage(ValidationError, "frozen"):
            operation.save()
        operation.refresh_from_db()
        operation.setup_minutes = Decimal("45")
        with self.assertRaisesMessage(ValidationError, "frozen"):
            operation.save()

    def test_a_closed_run_cannot_be_moved_to_another_loom(self):
        """History is not rescheduled."""
        order = self.routed()
        machine = Machine.objects.create(work_centre=self.loom, code="L-01")
        order.status = "closed"
        order.save(update_fields=["status"])
        operation = order.operations.get()
        operation.machine = machine
        with self.assertRaisesMessage(ValidationError, "frozen"):
            operation.save()

    def test_a_released_run_may_still_be_put_on_a_loom(self):
        order = self.routed()
        machine = Machine.objects.create(work_centre=self.loom, code="L-01")
        operation = order.operations.get()
        operation.machine = machine
        operation.save()
        self.assertEqual(order.operations.get().machine, machine)

    def test_a_booking_cannot_name_a_machine_in_another_bank(self):
        order = self.routed()
        with self.assertRaisesMessage(ValidationError, "belongs to CUT-1"):
            TimeBooking.objects.create(
                work_order=order, operation=order.operations.get(),
                booking_date=TODAY, minutes=Decimal("60"),
                machine=self.stranger,
            )

    def test_downtime_cannot_name_a_machine_in_another_bank(self):
        reason = DowntimeReason.objects.create(code="WARP", name="Warp break")
        with self.assertRaisesMessage(ValidationError, "belongs to CUT-1"):
            Downtime.objects.create(
                work_centre=self.loom, machine=self.stranger,
                shift_date=TODAY, reason=reason, minutes=Decimal("30"),
            )

    def test_a_schedule_cannot_name_a_machine_in_another_bank(self):
        with self.assertRaisesMessage(ValidationError, "belongs to CUT-1"):
            MaintenanceSchedule.objects.create(
                work_centre=self.loom, machine=self.stranger,
                name="Gearbox", every_days=90, duration_minutes=Decimal("60"),
            )

    def test_an_entry_cannot_name_a_machine_in_another_bank(self):
        order = self.routed()
        with self.assertRaisesMessage(ValidationError, "belongs to CUT-1"):
            ProductionEntry.objects.create(
                work_order=order, entry_date=TODAY, warehouse=self.plant,
                quantity_produced=Decimal("100"), uom=self.kg,
                work_centre=self.loom, machine=self.stranger,
            )

    def test_an_entry_with_no_bank_is_checked_against_the_routing(self):
        """
        The other half. An entry that names no work centre still
        cannot claim a machine the run never went near — otherwise the
        check is one `work_centre=None` away from doing nothing.
        """
        order = self.routed()
        with self.assertRaisesMessage(ValidationError, "never goes near"):
            ProductionEntry.objects.create(
                work_order=order, entry_date=TODAY, warehouse=self.plant,
                quantity_produced=Decimal("100"), uom=self.kg,
                machine=self.stranger,
            )


class ABookingRemembersWhichLoomTests(MachineTestCase):
    def test_a_booking_takes_the_machine_the_operation_was_put_on(self):
        order = self.routed()
        operation = order.operations.get()
        operation.machine = self.machine("L-01")
        operation.save()
        booking = TimeBooking.objects.create(
            work_order=order, operation=operation,
            booking_date=TODAY, minutes=Decimal("60"),
        )
        self.assertEqual(booking.machine, operation.machine)

    def test_a_run_moved_to_another_loom_leaves_the_first_shift_where_it_was(self):
        """
        The reason the machine is frozen onto the booking rather than
        read back through the operation. Reassigning a half-finished
        run is ordinary; rewriting where its first shift happened is
        not.
        """
        order = self.routed()
        operation = order.operations.get()
        first, second = self.machine("L-01"), self.machine("L-02")
        operation.machine = first
        operation.save()
        monday = TimeBooking.objects.create(
            work_order=order, operation=operation,
            booking_date=TODAY, minutes=Decimal("60"),
        )
        operation.machine = second
        operation.save()
        tuesday = TimeBooking.objects.create(
            work_order=order, operation=operation,
            booking_date=TODAY + datetime.timedelta(days=1),
            minutes=Decimal("60"),
        )
        monday.refresh_from_db()
        self.assertEqual(monday.machine, first)
        self.assertEqual(tuesday.machine, second)

    def test_a_booking_may_name_a_loom_the_operation_did_not(self):
        order = self.routed()
        machine = self.machine("L-01")
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("60"), machine=machine,
        )
        self.assertIsNone(order.operations.get().machine)
        self.assertEqual(booking.machine, machine)


class OneLoomCannotStopForLongerThanTheShiftTests(MachineTestCase):
    def setUp(self):
        super().setUp()
        self.shift = Shift.objects.create(
            code="A", name="Day", starts_at=datetime.time(6),
            hours=Decimal("8"),
        )
        self.reason = DowntimeReason.objects.create(
            code="WARP", name="Warp break"
        )

    def stop(self, minutes, machine=None):
        return Downtime.objects.create(
            work_centre=self.loom, machine=machine, shift_date=TODAY,
            shift=self.shift, reason=self.reason, minutes=Decimal(minutes),
        )

    def test_twelve_looms_may_each_be_down_a_whole_shift(self):
        """
        The regression the bank total would have caused. Totalling the
        centre refuses the second loom of the day, and a shed that
        cannot record its second breakdown stops recording breakdowns.
        """
        for code in ("L-01", "L-02", "L-03"):
            self.stop("480", self.machine(code))
        self.assertEqual(Downtime.objects.count(), 3)

    def test_one_loom_still_cannot_be_down_twice_over(self):
        machine = self.machine("L-01")
        self.stop("300", machine)
        with self.assertRaisesMessage(ValidationError, "L-01"):
            self.stop("300", machine)

    def test_a_bank_wide_stoppage_counts_against_every_loom(self):
        """
        A power cut stopped this loom as surely as its own warp break
        did. Four hundred of its own plus a hundred and twenty of the
        shed's is more shift than there is.

        Two looms with DIFFERENT stoppages on purpose. With one, the
        worst loom and the least-stopped loom are the same row and a
        guard that checked the wrong one would pass — which is the
        one-dimensional fixture this project keeps writing.
        """
        worst, mild = self.machine("L-01"), self.machine("L-02")
        self.stop("400", worst)
        self.stop("100", mild)
        with self.assertRaisesMessage(ValidationError, "L-01"):
            self.stop("120")

    def test_the_least_stopped_loom_still_has_room(self):
        """The other side of it: 100 + 120 fits in 480, and would be
        refused by a guard that tested the whole bank."""
        worst, mild = self.machine("L-01"), self.machine("L-02")
        self.stop("300", worst)
        self.stop("100", mild)
        self.stop("60")
        self.assertEqual(Downtime.objects.count(), 3)

    def test_a_centre_with_no_machines_totals_as_it_always_did(self):
        self.stop("300")
        with self.assertRaisesMessage(ValidationError, "EXT-1"):
            self.stop("300")


class WhichLoomIsDueTests(MachineTestCase):
    def book(self, hours, machine, order=None):
        order = order or self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal(hours) * 60, machine=machine,
        )
        booking.post()
        return booking

    def test_a_service_on_one_loom_counts_that_looms_hours(self):
        """
        The twelve-fold error. A shed runs twelve hours of bookings
        for every hour any one loom turns, so a beam change rated at
        five hundred hours would fall due every forty.
        """
        first, second = self.machine("L-01"), self.machine("L-02")
        order = self.routed()
        self.book("30", first, order)
        self.book("70", second, order)
        schedule = MaintenanceSchedule.objects.create(
            work_centre=self.loom, machine=first, name="Beam change",
            every_run_hours=Decimal("100"), duration_minutes=Decimal("120"),
        )
        self.assertEqual(schedule.hours_run_since(TODAY), Decimal("30"))
        self.assertFalse(schedule.is_due(TODAY))

    def test_a_bank_wide_service_still_counts_the_whole_bank(self):
        first, second = self.machine("L-01"), self.machine("L-02")
        order = self.routed()
        self.book("30", first, order)
        self.book("70", second, order)
        schedule = MaintenanceSchedule.objects.create(
            work_centre=self.loom, name="Compressor",
            every_run_hours=Decimal("100"), duration_minutes=Decimal("120"),
        )
        self.assertEqual(schedule.hours_run_since(TODAY), Decimal("100"))
        self.assertTrue(schedule.is_due(TODAY))

    def test_the_job_and_its_downtime_carry_the_loom_through(self):
        machine = self.machine("L-01")
        schedule = MaintenanceSchedule.objects.create(
            work_centre=self.loom, machine=machine, name="Beam change",
            every_days=30, duration_minutes=Decimal("120"),
        )
        job = schedule.raise_job(as_of=TODAY)
        self.assertEqual(job.machine, machine)
        stoppage = job.complete(on_date=TODAY)
        self.assertEqual(stoppage.machine, machine)
        self.assertIn("L-01", str(job))


class OneBadLoomInThirtyTests(MachineTestCase):
    """
    A bank at 78% is two dead looms and thirty good ones, or
    thirty-two mediocre ones. Those are different problems.
    """

    def test_effectiveness_can_be_asked_of_one_loom(self):
        first, second = self.machine("L-01"), self.machine("L-02")
        order = self.routed()
        for machine, minutes, made in (
            (first, "60", "180"), (second, "60", "90"),
        ):
            booking = TimeBooking.objects.create(
                work_order=order, operation=order.operations.get(),
                booking_date=TODAY, minutes=Decimal(minutes),
                quantity_completed=Decimal(made), machine=machine,
            )
            booking.post()
        good = oee.performance(self.loom, TODAY, TODAY, machine=first)
        bad = oee.performance(self.loom, TODAY, TODAY, machine=second)
        self.assertEqual(good["ratio"], Decimal("1"))
        self.assertEqual(bad["ratio"], Decimal("0.5"))
        # And the bank averages the two away.
        both = oee.performance(self.loom, TODAY, TODAY)
        self.assertEqual(both["ratio"], Decimal("0.75"))

    def test_a_bank_wide_stoppage_stops_every_loom_in_it(self):
        first = self.machine("L-01")
        self.machine("L-02")
        order = self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("60"), machine=first,
        )
        booking.post()
        reason = DowntimeReason.objects.create(code="PWR", name="Power cut")
        Downtime.objects.create(
            work_centre=self.loom, shift_date=TODAY, reason=reason,
            minutes=Decimal("60"),
        )
        up = oee.availability(self.loom, TODAY, TODAY, machine=first)
        self.assertEqual(up["stopped_minutes"], Decimal("60"))
        self.assertEqual(up["ratio"], Decimal("0.5"))

    def test_quality_can_be_asked_of_one_loom(self):
        """
        Why the entry carries a machine at all. Scrap is booked per
        entry, so without it a bank has a quality figure and its
        looms permanently do not — and the shed cannot tell one loom
        cutting badly from a bad batch of tape.
        """
        first, second = self.machine("L-01"), self.machine("L-02")
        order = self.routed()
        for machine, made, scrapped in (
            (first, "100", "0"), (second, "50", "50"),
        ):
            entry = ProductionEntry.objects.create(
                work_order=order, entry_date=TODAY, warehouse=self.plant,
                quantity_produced=Decimal(made),
                quantity_scrapped=Decimal(scrapped), uom=self.kg,
                work_centre=self.loom, machine=machine,
            )
            entry.post()
        good = oee.quality(self.loom, TODAY, TODAY, machine=first)
        bad = oee.quality(self.loom, TODAY, TODAY, machine=second)
        self.assertEqual(good["ratio"], Decimal("1"))
        self.assertEqual(bad["ratio"], Decimal("0.5"))
        # And the bank averages them: 150 good of 200 made.
        self.assertEqual(
            oee.quality(self.loom, TODAY, TODAY)["ratio"], Decimal("0.75")
        )

    def test_every_loom_appears_including_the_one_that_did_nothing(self):
        """The silent row is the interesting one."""
        first = self.machine("L-01")
        self.machine("L-02")
        order = self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("60"), machine=first,
        )
        booking.post()
        rows = oee.by_machine(self.loom, TODAY, TODAY)
        self.assertEqual([str(row["machine"]) for row in rows], ["L-01", "L-02"])
        self.assertEqual(
            rows[1]["availability"]["ran_minutes"], Decimal("0")
        )

    def test_hours_on_no_machine_get_their_own_row(self):
        self.machine("L-01")
        order = self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("90"),
        )
        booking.post()
        rows = oee.by_machine(self.loom, TODAY, TODAY)
        loose = rows[-1]
        self.assertEqual(str(loose["machine"]), "No machine named")
        self.assertEqual(loose["loose_minutes"], Decimal("90"))
        self.assertEqual(loose["loose_bookings"], 1)

    def test_a_bank_with_no_machines_has_no_rows_but_the_loose_one(self):
        order = self.routed()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("90"),
        )
        booking.post()
        rows = oee.by_machine(self.loom, TODAY, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["machine"]), "No machine named")
