"""
What the machines cost the run, and whether they earned it.

Until this, a work order's planned cost was materials and nothing else,
so a sack on the shelf was worth about three quarters of what it cost
to make and the power, wages and depreciation fell to the period with
nothing connecting them to the runs that used them.

The arithmetic is deliberately round: EXT-1 costs ₹360 an hour, which
is ₹6 a minute, so 1,423.33 planned minutes is ₹8,540 of planned
machine time and every figure below can be checked in your head.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType

from .orders import (
    ManufacturingSettings,
    TimeBooking,
    WorkOrderOperation,
    WorkOrderStatus,
)
from .routing import Routing, RoutingOperation
from .tests_orders import TODAY, RunTestCase


class ConversionTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.absorbed = acc("5300", "Conversion absorbed", AccountType.EXPENSE)
        self.time_variance = acc("5400", "Conversion variance", AccountType.EXPENSE)
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = self.absorbed
        settings.conversion_variance_account = self.time_variance
        settings.save()

        # ₹360 an hour, which is ₹6 a minute.
        self.loom.machine_rate_per_hour = Decimal("180")
        self.loom.labour_rate_per_hour = Decimal("120")
        self.loom.overhead_rate_per_hour = Decimal("60")
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()

        # The base fixture stocks a 1,000 kg run; these are 4,000 kg
        # ones. Same prices, so the averages do not move.
        self.stock(self.virgin, "8000", "100")
        self.stock(self.regrind, "2000", "60")
        self.stock(self.filler, "1200", "30")
        self.stock(self.colour, "400", "200")

        self.plan = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=self.plan, sequence=10, name="Extrude",
            work_centre=self.loom, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        self.bom.routing = self.plan
        self.bom.save()

    def routed(self, quantity="4000"):
        order = self.order(quantity)
        order.release(TODAY)
        return order

    def book(self, order, minutes, completed=None, operation=None):
        return TimeBooking.objects.create(
            work_order=order,
            operation=operation or order.operations.get(),
            booking_date=TODAY, minutes=Decimal(minutes),
            quantity_completed=(
                Decimal(completed) if completed is not None else None
            ),
        )


class MachineTimeIsPartOfThePlanTests(ConversionTestCase):
    def test_the_planned_time_is_costed_at_the_machines_rate(self):
        order = self.routed()
        # 90 minutes of setup then 4,000 kg at 180 an hour = 1,423.33
        # minutes, at ₹6 a minute.
        self.assertAlmostEqual(
            order.planned_minutes(), Decimal("1423.33"), places=2
        )
        self.assertAlmostEqual(
            order.planned_conversion_cost, Decimal("8540.00"), places=2
        )

    def test_it_lands_in_what_a_unit_is_worth(self):
        # The point of the whole exercise: a kilo of tape carries its
        # share of the loom, not only its share of the polymer.
        with_routing = self.routed()
        self.bom.routing = None
        self.bom.save()
        without = self.order("4000")
        without.release(TODAY)
        self.assertGreater(
            with_routing.planned_unit_cost, without.planned_unit_cost
        )
        # 8,540 over 4,000 kg is ₹2.135 a kilo.
        self.assertAlmostEqual(
            with_routing.planned_unit_cost - without.planned_unit_cost,
            Decimal("2.135"), places=3,
        )

    def test_a_plant_that_does_not_absorb_conversion_gets_nothing(self):
        # A real choice, not an oversight: it carries power and labour
        # as period cost and its goods are worth their materials.
        self.loom.machine_rate_per_hour = Decimal("0")
        self.loom.labour_rate_per_hour = Decimal("0")
        self.loom.overhead_rate_per_hour = Decimal("0")
        self.loom.save()
        order = self.routed()
        self.assertEqual(order.planned_conversion_cost, Decimal("0"))

    def test_a_run_with_no_routing_has_no_machine_time(self):
        self.bom.routing = None
        self.bom.save()
        order = self.order("4000")
        order.release(TODAY)
        self.assertEqual(order.planned_conversion_cost, Decimal("0"))


class BookingTimeChargesTheRunTests(ConversionTestCase):
    def test_it_debits_work_in_progress_and_credits_absorbed(self):
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        # Ten hours at ₹360.
        self.assertEqual(booking.posted_value, Decimal("3600.00"))
        self.assertEqual(self.balance(self.wip), Decimal("3600.00"))
        self.assertEqual(self.balance(self.absorbed), Decimal("-3600.00"))
        self.assertEqual(order.conversion_cost(), Decimal("3600.00"))

    def test_the_rate_is_frozen_when_it_posts(self):
        # A work centre re-rated in April must not re-price a shift that
        # ran in March.
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        self.loom.machine_rate_per_hour = Decimal("900")
        self.loom.save()
        booking.refresh_from_db()
        self.assertEqual(booking.hourly_rate, Decimal("360"))
        self.assertEqual(booking.posted_value, Decimal("3600.00"))
        self.assertEqual(order.conversion_cost(), Decimal("3600.00"))

    def test_the_hours_go_into_the_run(self):
        order = self.routed()
        self.book(order, "600").post()
        self.book(order, "300").post()
        self.assertEqual(order.minutes_booked(), Decimal("900.00"))
        self.assertEqual(order.conversion_cost(), Decimal("5400.00"))

    def test_a_booking_against_another_runs_operation(self):
        order = self.routed()
        other = self.routed("1000")
        booking = TimeBooking.objects.create(
            work_order=order, operation=other.operations.get(),
            booking_date=TODAY, minutes=Decimal("60"),
        )
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("belongs to", str(caught.exception))

    def test_time_cannot_be_booked_against_a_closed_run(self):
        order = self.routed()
        order.close(TODAY)
        booking = self.book(order, "60")
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("released order", str(caught.exception))

    def test_a_plant_with_no_rates_books_the_hours_and_posts_nothing(self):
        self.loom.machine_rate_per_hour = Decimal("0")
        self.loom.labour_rate_per_hour = Decimal("0")
        self.loom.overhead_rate_per_hour = Decimal("0")
        self.loom.save()
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        self.assertEqual(booking.posted_value, Decimal("0"))
        self.assertIsNone(booking.journal_entry)
        # The hours are still worth having: they are what the time
        # variance is measured from.
        self.assertEqual(order.minutes_booked(), Decimal("600.00"))

    def test_a_posted_booking_cannot_be_edited(self):
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        booking.minutes = Decimal("60")
        with self.assertRaises(ValidationError):
            booking.save()

    def test_no_absorbed_account_is_a_sentence(self):
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = None
        settings.save()
        order = self.routed()
        with self.assertRaises(ValidationError) as caught:
            self.book(order, "600").post()
        self.assertIn("conversion absorbed account", str(caught.exception))


class AndItCanBeTakenBackOffTests(ConversionTestCase):
    def test_voiding_a_booking(self):
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        booking.void(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.absorbed), Decimal("0"))
        self.assertEqual(order.conversion_cost(), Decimal("0"))
        self.assertEqual(order.minutes_booked(), Decimal("0"))

    def test_not_after_the_run_is_closed(self):
        order = self.routed()
        booking = self.book(order, "600")
        booking.post()
        order.close(TODAY)
        with self.assertRaises(ValidationError) as caught:
            booking.void(TODAY)
        self.assertIn("Reopen the order first", str(caught.exception))
        self.assertEqual(self.balance(self.wip), Decimal("0"))


class ClosingTellsTheTwoOverrunsApartTests(ConversionTestCase):
    """
    A blend that ran heavy and a loom that ran slow are different
    problems with different owners, and one number for both names
    neither. The two always come to exactly what was left in work in
    progress: material takes the remainder, so nothing can fall between
    them.
    """

    def run_it(self, minutes, produced="4000", scrapped="0"):
        order = self.routed()
        self.full_issue(order).post()
        self.book(order, minutes).post()
        self.produce(
            order, produced, scrapped=scrapped,
            byproducts=[(self.regrind, "98.9691")],
        ).post()
        return order

    def test_a_loom_that_ran_over(self):
        # 1,600 minutes booked against 1,423.33 earned, at ₹6 a minute.
        order = self.run_it("1600")
        self.assertAlmostEqual(
            order.conversion_earned(), Decimal("8540.00"), places=2
        )
        self.assertAlmostEqual(
            order.conversion_variance(), Decimal("1060.00"), places=2
        )
        self.assertAlmostEqual(
            order.time_variance_minutes(), Decimal("176.67"), places=2
        )
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertAlmostEqual(
            self.balance(self.time_variance), Decimal("1060.00"), places=2
        )

    def test_a_loom_that_ran_to_time(self):
        order = self.run_it("1423.33")
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertLess(abs(self.balance(self.time_variance)), Decimal("0.05"))

    def test_the_two_always_come_to_what_was_left(self):
        order = self.run_it("1600")
        left = order.unaccounted()
        order.close(TODAY)
        total = self.balance(self.variance) + self.balance(self.time_variance)
        self.assertAlmostEqual(total, left, places=2)
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_a_run_that_made_half_earns_half_the_machine_time(self):
        # The loom ran the full setup and half the metres; it has earned
        # half the hours and the rest is an overrun, whether it was slow
        # or simply stopped.
        order = self.run_it("1600", produced="2000")
        self.assertAlmostEqual(
            order.conversion_earned(), Decimal("4270.00"), places=2
        )
        self.assertAlmostEqual(
            order.conversion_variance(), Decimal("5330.00"), places=2
        )

    def test_scrap_counts_as_time_earned(self):
        # The machine ran to make it.
        full = self.run_it("1600", produced="4000")
        half = self.run_it("1600", produced="2000", scrapped="2000")
        self.assertAlmostEqual(
            half.conversion_earned(), full.conversion_earned(), places=2
        )

    def test_a_run_with_no_machine_time_at_all_still_closes(self):
        self.bom.routing = None
        self.bom.save()
        order = self.order("4000")
        order.release(TODAY)
        self.full_issue(order).post()
        order.close(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(self.balance(self.time_variance), Decimal("0"))
        self.assertGreater(self.balance(self.variance), Decimal("0"))


class HowFarThroughTheRoutingTests(ConversionTestCase):
    def test_an_operation_knows_what_has_come_off_it(self):
        order = self.routed()
        self.book(order, "600", completed="1500").post()
        self.book(order, "300", completed="800").post()
        operation = order.operations.get()
        self.assertEqual(operation.quantity_completed(), Decimal("2300"))
        self.assertEqual(operation.minutes_booked(), Decimal("900.00"))

    def test_a_shift_that_did_not_count_is_not_a_count_of_zero(self):
        order = self.routed()
        self.book(order, "600").post()
        operation = order.operations.get()
        self.assertEqual(operation.quantity_completed(), Decimal("0"))
        self.assertEqual(operation.counted_bookings().count(), 0)
        self.assertEqual(operation.posted_bookings().count(), 1)

    def test_a_voided_booking_is_not_progress(self):
        order = self.routed()
        booking = self.book(order, "600", completed="1500")
        booking.post()
        booking.void(TODAY)
        self.assertEqual(order.operations.get().quantity_completed(), Decimal("0"))


class ABookingHasToBePhysicallyPossibleTests(ConversionTestCase):
    """
    Three things a shift can get wrong by typing, all found by probing.
    The third is the one no general-purpose system asks.
    """

    def test_a_typed_zero_on_the_minutes(self):
        # 1,000 hours on a 23.7 hour run had put ₹360,000 into work in
        # progress without a murmur.
        order = self.routed()
        booking = self.book(order, "60000")
        with self.assertRaises(ValidationError) as caught:
            booking.post()
        self.assertIn("really ran that long", str(caught.exception))
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_a_machine_that_genuinely_ran_over_is_not_refused(self):
        # Fifty per cent is wide on purpose: a breakdown costs real hours.
        order = self.routed()
        self.book(order, "2000").post()
        self.assertEqual(order.minutes_booked(), Decimal("2000.00"))

    def test_the_allowance_counts_what_is_already_booked(self):
        order = self.routed()
        self.book(order, "2000").post()
        with self.assertRaises(ValidationError):
            self.book(order, "500").post()

    def test_a_plant_whose_machines_really_do_run_over_says_so(self):
        order = self.routed()
        order.time_allowance_percent = Decimal("400")
        order.save()
        self.book(order, "6000").post()
        self.assertEqual(order.minutes_booked(), Decimal("6000.00"))

    def test_more_completed_than_the_run_is_for(self):
        # Booked over enough time that only the quantity is impossible.
        order = self.routed()
        with self.assertRaises(ValidationError) as caught:
            self.book(order, "2000", completed="999999").post()
        self.assertIn("which allows up to", str(caught.exception))

    def test_a_sack_cannot_be_stitched_before_it_is_cut(self):
        # The invariant no general-purpose system checks, and the one
        # that catches a shift booked against the wrong line.
        RoutingOperation.objects.create(
            routing=self.plan, sequence=20, name="Wind",
            work_centre=self.loom, setup_minutes=Decimal("10"),
            units_per_hour=Decimal("4000"), rate_uom=self.kg,
        )
        order = self.routed()
        first, last = list(order.operations.order_by("sequence"))
        self.book(order, "200", completed="500", operation=first).post()
        with self.assertRaises(ValidationError) as caught:
            self.book(order, "60", completed="3000", operation=last).post()
        self.assertIn("never went into it", str(caught.exception))

    def test_but_it_can_be_stitched_once_it_has_been(self):
        RoutingOperation.objects.create(
            routing=self.plan, sequence=20, name="Wind",
            work_centre=self.loom, setup_minutes=Decimal("10"),
            units_per_hour=Decimal("4000"), rate_uom=self.kg,
        )
        order = self.routed()
        first, last = list(order.operations.order_by("sequence"))
        self.book(order, "1100", completed="3000", operation=first).post()
        self.book(order, "60", completed="3000", operation=last).post()
        self.assertEqual(last.quantity_completed(), Decimal("3000"))

    def test_an_operation_nobody_counted_does_not_block_the_next(self):
        # Nobody counting is not nothing being made, and treating it as
        # zero would refuse every booking after it.
        RoutingOperation.objects.create(
            routing=self.plan, sequence=20, name="Wind",
            work_centre=self.loom, setup_minutes=Decimal("10"),
            units_per_hour=Decimal("4000"), rate_uom=self.kg,
        )
        order = self.routed()
        first, last = list(order.operations.order_by("sequence"))
        self.book(order, "60", operation=first).post()
        self.book(order, "60", completed="3000", operation=last).post()
        self.assertEqual(last.quantity_completed(), Decimal("3000"))

    def test_the_first_operation_has_nothing_upstream_of_it(self):
        order = self.routed()
        self.book(order, "1100", completed="3000").post()
        self.assertIsNone(order.operations.get().feeds_from())


class ClosingNeedsSomewhereToPutItTests(ConversionTestCase):
    def test_a_time_overrun_with_no_account_for_it(self):
        # Refused rather than quietly merged into the material variance,
        # which would name neither problem.
        settings = ManufacturingSettings.get()
        settings.conversion_variance_account = None
        settings.save()
        order = self.routed()
        self.full_issue(order).post()
        self.book(order, "1600").post()
        with self.assertRaises(ValidationError) as caught:
            order.close(TODAY)
        self.assertIn("conversion variance account", str(caught.exception))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.RELEASED)
