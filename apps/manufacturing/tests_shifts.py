"""
Who was on the machine, and what happened while they were.

The figures here were worked out by hand: a shift of 480 minutes in
which the loom ran 400 and stood still for 80 is 5/6 available; a
booking of 400 minutes that produced what 333.33 should have is 5/6 at
speed; and 950 good kilogrammes out of a thousand is 0.95. The three
multiply to 0.6597, which is the number that surprises people about a
line everybody calls "eighty-something per cent".
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee, EmploymentStatus

from .oee import availability, by_operator, by_shift, effectiveness, performance, quality
from .shifts import Downtime, DowntimeReason, Shift
from .tests_conversion import ConversionTestCase
from .tests_orders import TODAY

# The same day the rest of the manufacturing fixtures book on, so a
# report's window and the documents in it line up.
DAY = TODAY


def at(year, month, day, hour, minute=0):
    return timezone.make_aware(datetime.datetime(year, month, day, hour, minute))


class ShiftTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.day = Shift.objects.create(
            code="A", name="Day", starts_at=datetime.time(6, 0), hours=Decimal("8")
        )
        self.evening = Shift.objects.create(
            code="B", name="Evening", starts_at=datetime.time(14, 0),
            hours=Decimal("8"),
        )
        self.night = Shift.objects.create(
            code="C", name="Night", starts_at=datetime.time(22, 0),
            hours=Decimal("8"),
        )
        self.breakdown = DowntimeReason.objects.create(
            code="WARP", name="Warp break"
        )
        self.changeover = DowntimeReason.objects.create(
            code="CHG", name="Colour change", is_planned=True
        )

    def operator(self, number, name, hired="2020-01-01", left=None):
        party = Party.objects.create(code=number, name=name)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(
            party=party, employee_number=number,
            hire_date=datetime.date.fromisoformat(hired),
            termination_date=(
                datetime.date.fromisoformat(left) if left else None
            ),
            employment_status=(
                EmploymentStatus.TERMINATED if left else EmploymentStatus.ACTIVE
            ),
        )

    def stop(self, minutes, reason=None, shift=None, on=DAY, run=None):
        return Downtime.objects.create(
            work_centre=self.loom, shift_date=on, shift=shift or self.night,
            reason=reason or self.breakdown, minutes=Decimal(minutes),
            work_order=run,
        )


class TheNightShiftBelongsToTheDayBeforeTests(ShiftTestCase):
    def test_a_shift_that_ends_the_next_morning_says_so(self):
        self.assertTrue(self.night.crosses_midnight())
        self.assertFalse(self.day.crosses_midnight())
        self.assertEqual(self.night.ends_at(), datetime.time(6, 0))

    def test_two_in_the_morning_is_the_night_before(self):
        # The whole reason this is a method rather than a report's
        # `.date()` call: a crew's hours must not split across two days.
        self.assertEqual(
            self.night.shift_date_for(at(2026, 6, 2, 2)),
            datetime.date(2026, 6, 1),
        )

    def test_and_eleven_at_night_is_the_same_day(self):
        self.assertEqual(
            self.night.shift_date_for(at(2026, 6, 1, 23)),
            datetime.date(2026, 6, 1),
        )

    def test_a_day_shift_is_simply_the_day(self):
        self.assertEqual(
            self.day.shift_date_for(at(2026, 6, 1, 8)),
            datetime.date(2026, 6, 1),
        )

    def test_which_shift_a_moment_falls_in(self):
        self.assertEqual(Shift.covering(at(2026, 6, 1, 8)), self.day)
        self.assertEqual(Shift.covering(at(2026, 6, 1, 15)), self.evening)
        self.assertEqual(Shift.covering(at(2026, 6, 1, 23)), self.night)
        self.assertEqual(Shift.covering(at(2026, 6, 2, 2)), self.night)

    def test_a_shift_nobody_is_running_covers_nothing(self):
        self.night.is_active = False
        self.night.save()
        self.assertIsNone(Shift.covering(at(2026, 6, 2, 2)))


class ABookingKnowsWhoseShiftItWasTests(ShiftTestCase):
    def test_the_clock_decides_the_shift_and_the_day(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.started_at = at(2026, 6, 2, 1)
        booking.save()
        booking.post()
        self.assertEqual(booking.shift, self.night)
        self.assertEqual(booking.booking_date, datetime.date(2026, 6, 1))

    def test_a_shift_that_does_not_run_at_that_hour(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.started_at = at(2026, 6, 1, 9)
        booking.shift = self.night
        booking.save()
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("does not run at", str(caught.exception))

    def test_an_hour_no_shift_covers(self):
        self.night.is_active = False
        self.night.save()
        order = self.routed()
        booking = self.book(order, "300")
        booking.started_at = at(2026, 6, 2, 2)
        booking.save()
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("No active shift covers", str(caught.exception))

    def test_a_plant_that_does_not_run_shifts_loses_nothing(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.post()
        self.assertIsNone(booking.shift)
        self.assertEqual(booking.posted_value, Decimal("1800.00"))


class WhoWasOnItTests(ShiftTestCase):
    def test_a_crew_is_recorded(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.operators.set([self.operator("E1", "Ravi"), self.operator("E2", "Sunil")])
        booking.post()
        self.assertEqual(booking.operators.count(), 2)

    def test_somebody_who_had_not_started_yet(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.operators.set([self.operator("E3", "New", hired="2027-01-01")])
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("not employed here on", str(caught.exception))

    def test_somebody_who_had_already_left(self):
        order = self.routed()
        booking = self.book(order, "300")
        booking.operators.set([self.operator("E4", "Gone", left="2026-01-31")])
        with self.assertRaises(ValidationError):
            booking.post()

    def test_the_crew_does_not_change_what_the_hour_costs(self):
        # Attribution, not costing: the labour is in the work centre's
        # rate and charging these people on top would bill the run twice
        # for the same crew.
        order = self.routed()
        alone = self.book(order, "300")
        alone.post()
        crowded = self.book(order, "300")
        crowded.operators.set([
            self.operator("E5", "A"), self.operator("E6", "B"),
            self.operator("E7", "C"),
        ])
        crowded.post()
        self.assertEqual(alone.posted_value, crowded.posted_value)


class DowntimeTests(ShiftTestCase):
    def test_a_stoppage_is_recorded_against_the_machine(self):
        stop = self.stop("80")
        self.assertEqual(stop.minutes, Decimal("80"))
        self.assertAlmostEqual(stop.hours(), Decimal("1.3333"), places=3)

    def test_longer_than_the_shift_it_was_in(self):
        with self.assertRaises(ValidationError) as caught:
            self.stop("600")
        self.assertIn("cannot be stopped for longer", str(caught.exception))

    def test_a_stoppage_on_a_machine_the_run_never_goes_near(self):
        from .orders import WorkCentre

        other = WorkCentre.objects.create(code="CUT-9", name="Cutting")
        order = self.routed()
        with self.assertRaises(ValidationError) as caught:
            Downtime.objects.create(
                work_centre=other, shift_date=DAY, shift=self.night,
                reason=self.breakdown, minutes=Decimal("30"), work_order=order,
            )
        self.assertIn("never goes near", str(caught.exception))

    def test_a_changeover_belongs_to_no_run_at_all(self):
        stop = self.stop("45", reason=self.changeover)
        self.assertIsNone(stop.work_order)
        self.assertTrue(stop.reason.is_planned)


class TheThreeRatiosTests(ShiftTestCase):
    def a_shift(self, ran="400", stopped="80", completed="1000",
                produced="950", scrapped="50"):
        order = self.routed()
        booking = self.book(order, ran, completed=completed)
        booking.started_at = at(2026, 6, 1, 23)
        booking.save()
        booking.post()
        if stopped:
            self.stop(stopped)
        self.produce(order, produced, scrapped=scrapped).post()
        return order

    def test_availability_is_what_ran_over_what_it_had(self):
        self.a_shift()
        report = availability(self.loom, DAY, DAY)
        self.assertEqual(report["ran_minutes"], Decimal("400.00"))
        self.assertEqual(report["stopped_minutes"], Decimal("80.00"))
        # 400 of 480.
        self.assertAlmostEqual(report["ratio"], Decimal("0.8333"), places=4)

    def test_planned_and_unplanned_are_reported_apart(self):
        self.a_shift(stopped=None)
        self.stop("45", reason=self.changeover)
        self.stop("35", reason=self.breakdown)
        report = availability(self.loom, DAY, DAY)
        self.assertEqual(report["planned_stop_minutes"], Decimal("45.00"))
        self.assertEqual(report["unplanned_stop_minutes"], Decimal("35.00"))
        # Availability counts both: the loom was stopped either way.
        self.assertAlmostEqual(report["ratio"], Decimal("0.8333"), places=4)

    def test_performance_is_measured_in_minutes(self):
        self.a_shift()
        report = performance(self.loom, DAY, DAY)
        # A thousand kilos at 180 an hour should have taken 333.33
        # minutes; it took 400.
        self.assertAlmostEqual(report["ideal_minutes"], Decimal("333.33"), places=2)
        self.assertEqual(report["actual_minutes"], Decimal("400.00"))
        self.assertAlmostEqual(report["ratio"], Decimal("0.8333"), places=4)

    def test_a_shift_that_did_not_count_is_not_a_shift_that_made_nothing(self):
        # Putting it in the denominator would read its silence as zero
        # output and drag the line's performance down for a report
        # nobody filled in.
        self.a_shift()
        extra = self.book(self.routed(), "120")
        extra.started_at = at(2026, 6, 1, 23)
        extra.save()
        extra.post()
        report = performance(self.loom, DAY, DAY)
        self.assertEqual(report["counted_bookings"], 1)
        self.assertEqual(report["actual_minutes"], Decimal("400.00"))

    def test_quality_is_good_over_everything_that_came_off(self):
        self.a_shift()
        report = quality(self.loom, DAY, DAY)
        self.assertEqual(report["good"], Decimal("950.0000"))
        self.assertEqual(report["scrapped"], Decimal("50.0000"))
        self.assertAlmostEqual(report["ratio"], Decimal("0.95"), places=4)

    def test_and_the_three_multiply(self):
        self.a_shift()
        report = effectiveness(self.loom, DAY, DAY)
        # 0.8333 x 0.8333 x 0.95. A line everybody calls
        # "eighty-something per cent" is at sixty-six.
        self.assertAlmostEqual(report["oee"], Decimal("0.6597"), places=4)
        self.assertEqual(report["unmeasured"], [])

    def test_a_ratio_nobody_measured_is_missing_rather_than_one(self):
        # A plant that reads a missing ratio as one congratulates itself
        # on a line it never measured.
        order = self.routed()
        booking = self.book(order, "400")
        booking.post()
        report = effectiveness(self.loom, DAY, DAY)
        self.assertIsNone(report["oee"])
        self.assertIn("performance", report["unmeasured"])
        self.assertIn("quality", report["unmeasured"])


class QualityWillNotAddKilogrammesToPiecesTests(ShiftTestCase):
    def test_a_machine_that_made_two_kinds_of_thing(self):
        from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
        from apps.inventory.models import Item

        from .bom import BillOfMaterials, BomComponent

        pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        sack = Item.objects.create(sku="SACK", name="Sack", uom=pcs)
        bom = BillOfMaterials.objects.create(
            item=sack, quantity_produced=Decimal("1000"), uom=pcs
        )
        BomComponent.objects.create(
            bom=bom, item=self.virgin, quantity=Decimal("100"), uom=self.kg
        )
        sacks = self.order("1000")
        sacks.item = sack
        sacks.bom = bom
        sacks.uom = pcs
        sacks.save()
        sacks.release(DAY)
        self.produce(sacks, "900", scrapped="100", uom=pcs).post()

        tape = self.routed()
        self.produce(tape, "950", scrapped="50").post()

        report = quality(self.loom, DAY, DAY)
        self.assertIsNone(report["ratio"])
        self.assertIn("different units", report["note"])
        self.assertEqual(len(report["by_item"]), 2)
        ratios = {row["item"].sku: row["ratio"] for row in report["by_item"]}
        self.assertAlmostEqual(ratios["TAPE-1000"], Decimal("0.95"), places=4)
        self.assertAlmostEqual(ratios["SACK"], Decimal("0.90"), places=4)


class TheGroupingsAPlantManagerReadsTests(ShiftTestCase):
    def test_a_row_per_shift_including_the_silent_ones(self):
        order = self.routed()
        booking = self.book(order, "400", completed="1000")
        booking.started_at = at(2026, 6, 1, 23)
        booking.save()
        booking.post()
        rows = by_shift(self.loom, DAY, DAY)
        self.assertEqual(len(rows), 3)
        worked = {
            row["shift"].code: row["availability"]["ran_minutes"] for row in rows
        }
        self.assertEqual(worked["C"], Decimal("400.00"))
        # The most interesting row on the page.
        self.assertEqual(worked["A"], Decimal("0"))

    def test_what_each_persons_hours_produced(self):
        ravi = self.operator("E1", "Ravi")
        sunil = self.operator("E2", "Sunil")
        order = self.routed()
        first = self.book(order, "400", completed="1000")
        first.operators.set([ravi, sunil])
        first.post()
        second = self.book(order, "120", completed="200")
        second.operators.set([ravi])
        second.post()
        rows = {row["operator"].employee_number: row for row in by_operator(DAY, DAY)}
        # A booking with two people on it is two people's hours: the
        # machine ran once and both of them were there for all of it.
        self.assertEqual(rows["E1"]["minutes"], Decimal("520.00"))
        self.assertEqual(rows["E2"]["minutes"], Decimal("400.00"))
        self.assertEqual(rows["E1"]["work_centre"], self.loom)

    def test_a_booking_with_nobody_on_it_is_not_a_row(self):
        order = self.routed()
        self.book(order, "400", completed="1000").post()
        self.assertEqual(by_operator(DAY, DAY), [])


class TwoShiftsCannotClaimTheSameHourTests(ShiftTestCase):
    """
    Found by probing. Which crew a booking landed on depended on the
    order the rows came back in, which is not an answer.
    """

    def test_an_overlapping_shift_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            Shift.objects.create(
                code="D", name="Overlaps night",
                starts_at=datetime.time(21, 0), hours=Decimal("8"),
            )
        self.assertIn("claim the same hours", str(caught.exception))

    def test_nor_can_one_be_stretched_into_another(self):
        self.evening.hours = Decimal("12")
        with self.assertRaises(ValidationError):
            self.evening.save()

    def test_a_retired_shift_is_not_in_the_way(self):
        # A plant that re-cuts its night shift has two facts, and the
        # old one must not block the new one — which it would, being an
        # exact overlap of it.
        self.night.is_active = False
        self.night.save()
        replacement = Shift.objects.create(
            code="C2", name="Night, from March",
            starts_at=datetime.time(22, 0), hours=Decimal("8"),
        )
        self.assertEqual(Shift.covering(at(2026, 6, 1, 23)), replacement)

    def test_shifts_that_merely_touch_are_fine(self):
        # Six to two and two to ten share an instant and no minute: the
        # end of a shift is the start of the next, not part of it.
        self.assertEqual(Shift.covering(at(2026, 6, 1, 14)), self.evening)


class OneBreakdownEnteredTwiceTests(ShiftTestCase):
    def test_two_stoppages_that_together_outlast_the_shift(self):
        # Each passed on its own; between them they made an eight-hour
        # shift ten hours long and had the loom reading nought per cent
        # available.
        self.stop("300")
        with self.assertRaises(ValidationError) as caught:
            self.stop("300")
        self.assertIn("would be down for 600", str(caught.exception))

    def test_the_row_being_edited_does_not_count_against_itself(self):
        stop = self.stop("300")
        stop.minutes = Decimal("400")
        stop.save()
        self.assertEqual(stop.minutes, Decimal("400"))

    def test_a_different_machine_is_a_different_question(self):
        from .orders import WorkCentre

        other = WorkCentre.objects.create(code="LOOM-9", name="Loom 9")
        self.stop("400")
        Downtime.objects.create(
            work_centre=other, shift_date=DAY, shift=self.night,
            reason=self.breakdown, minutes=Decimal("400"),
        )

    def test_and_so_is_a_different_day(self):
        self.stop("400")
        self.stop("400", on=DAY - datetime.timedelta(days=1))


class ALineCannotRunFiveTimesItsRateTests(ShiftTestCase):
    def test_an_hour_that_made_a_days_worth(self):
        # 1,000 kg in an hour on a 180 kg/hr line read as 556%
        # performance and made the loom look magnificent.
        order = self.routed()
        booking = self.book(order, "60", completed="1000")
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("could not have made", str(caught.exception))

    def test_a_line_genuinely_running_above_its_rate_is_allowed(self):
        # Good polymer and an experienced crew: 250 kg in an hour on a
        # 180 kg/hr line is 39% over and perfectly real.
        order = self.routed()
        self.book(order, "60", completed="250").post()
        self.assertAlmostEqual(
            performance(self.loom, DAY, DAY)["ratio"], Decimal("1.3889"), places=4
        )

    def test_setup_does_not_trip_it(self):
        # The minutes the output should have needed can only be smaller
        # than the minutes booked, so setup only widens the margin.
        order = self.routed()
        self.book(order, "300", completed="200").post()

    def test_it_counts_what_is_already_booked(self):
        order = self.routed()
        self.book(order, "60", completed="250").post()
        with self.assertRaises(ValidationError):
            self.book(order, "10", completed="250").post()


class TheRowsAddUpToTheWindowTests(ShiftTestCase):
    def test_hours_nobody_attributed_get_a_row_of_their_own(self):
        order = self.routed()
        self.book(order, "400", completed="1000").post()   # no shift named
        whole = availability(self.loom, DAY, DAY)
        rows = by_shift(self.loom, DAY, DAY)
        self.assertEqual(
            sum(row["availability"]["ran_minutes"] for row in rows),
            whole["ran_minutes"],
        )
        loose = [row for row in rows if not hasattr(row["shift"], "code")]
        self.assertEqual(len(loose), 1)
        self.assertEqual(loose[0]["availability"]["ran_minutes"], Decimal("400.00"))

    def test_and_there_is_no_such_row_when_everything_was_attributed(self):
        order = self.routed()
        booking = self.book(order, "400", completed="1000")
        booking.started_at = at(2026, 6, 1, 23)
        booking.save()
        booking.post()
        rows = by_shift(self.loom, DAY, DAY)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(hasattr(row["shift"], "code") for row in rows))


class AWindowThatMeansSomethingTests(ShiftTestCase):
    def test_a_window_the_wrong_way_round(self):
        # It used to report nought hours and nought stoppages, which
        # reads exactly like a machine that stood idle all week.
        with self.assertRaises(ValidationError) as caught:
            effectiveness(self.loom, DAY, DAY - datetime.timedelta(days=7))
        self.assertIn("runs backwards", str(caught.exception))
