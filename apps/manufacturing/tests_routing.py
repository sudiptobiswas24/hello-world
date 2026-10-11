"""
How long a run holds a machine, and whether the machine has the hours.

The figures here were worked out by hand from the rates and the setup
times; the working is in the comment beside each. The one that matters
most is the last class: a machine's load has to count released runs
only, and count them whole.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

from .orders import WorkCentre, WorkOrder, WorkOrderOperation, WorkOrderStatus
from .routing import Routing, RoutingOperation, capacity_report
from .tests_orders import TODAY, RunTestCase


class RoutingTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.extruder = self.loom  # EXT-1, from the base fixture
        self.extruder.capacity_per_hour = Decimal("180")
        self.extruder.capacity_uom = self.kg
        self.extruder.save()

    def routing(self, code="R-TAPE", rows=None):
        routing = Routing.objects.create(code=code, name="Tape")
        for sequence, (name, centre, setup, rate) in enumerate(
            rows or [("Extrude", self.extruder, "90", "180")], start=10
        ):
            RoutingOperation.objects.create(
                routing=routing, sequence=sequence, name=name,
                work_centre=centre, setup_minutes=Decimal(setup),
                units_per_hour=Decimal(rate) if rate else None,
                rate_uom=self.kg if rate else None,
            )
        return routing

    def routed_order(self, quantity="4000", routing=None):
        self.bom.routing = routing or self.routing()
        self.bom.save()
        return self.order(quantity)


class HowLongItTakesTests(RoutingTestCase):
    def test_setup_plus_run_time(self):
        operation = self.routing().operations.get()
        # 90 minutes to thread the line, then 4,000 kg at 180 an hour:
        # 4000/180 = 22.2222 hours = 1,333.3333 minutes.
        self.assertAlmostEqual(
            operation.minutes_for(Decimal("4000"), self.kg), Decimal("1423.3333"), places=3
        )

    def test_setup_is_paid_once_however_short_the_run(self):
        # The reason a plant hates short runs, and invisible in a model
        # that costs only by the unit.
        operation = self.routing().operations.get()
        short = operation.minutes_for(Decimal("100"), self.kg)
        self.assertAlmostEqual(short, Decimal("123.3333"), places=3)
        # Forty times the quantity is nowhere near forty times the time.
        self.assertLess(operation.minutes_for(Decimal("4000"), self.kg), short * 40)

    def test_the_rate_falls_back_to_the_machine(self):
        routing = self.routing(rows=[("Extrude", self.extruder, "90", None)])
        operation = routing.operations.get()
        self.assertEqual(operation.rate(self.kg), Decimal("180"))

    def test_a_rate_nobody_has_stated_is_refused_rather_than_guessed(self):
        self.extruder.capacity_per_hour = None
        self.extruder.save()
        routing = self.routing(rows=[("Extrude", self.extruder, "90", None)])
        with self.assertRaises(ValidationError) as caught:
            routing.operations.get().rate(self.kg)
        self.assertIn("Give the operation a rate", str(caught.exception))

    def test_the_whole_routing_adds_up(self):
        cutting = WorkCentre.objects.create(code="CUT-1", name="Cut and stitch")
        routing = self.routing(code="R-BAG", rows=[
            ("Laminate", self.extruder, "45", "8000"),
            ("Print", self.extruder, "60", "6000"),
            ("Cut and stitch", cutting, "20", "4200"),
        ])
        # 45 + 375, 60 + 500, 20 + 714.2857
        self.assertAlmostEqual(
            routing.total_minutes(Decimal("50000"), self.kg), Decimal("1714.2857"), places=3
        )

    def test_the_bottleneck_is_the_one_worth_fixing(self):
        cutting = WorkCentre.objects.create(code="CUT-1", name="Cut and stitch")
        routing = self.routing(code="R-BAG", rows=[
            ("Laminate", self.extruder, "45", "8000"),
            ("Print", self.extruder, "60", "6000"),
            ("Cut and stitch", cutting, "20", "4200"),
        ])
        self.assertEqual(
            routing.bottleneck(Decimal("50000"), self.kg).name, "Cut and stitch"
        )

    def test_and_it_can_change_with_the_quantity(self):
        # On a short run setup dominates, and the machine that takes an
        # hour to set up is the one everybody waits for.
        cutting = WorkCentre.objects.create(code="CUT-1", name="Cut and stitch")
        routing = self.routing(code="R-BAG", rows=[
            ("Print", self.extruder, "60", "6000"),
            ("Cut and stitch", cutting, "20", "4200"),
        ])
        self.assertEqual(routing.bottleneck(Decimal("1000"), self.kg).name, "Print")
        self.assertEqual(
            routing.bottleneck(Decimal("50000"), self.kg).name, "Cut and stitch"
        )


class ReleaseFreezesTheRoutingTests(RoutingTestCase):
    def test_the_operations_are_copied_onto_the_run(self):
        order = self.routed_order()
        order.release(TODAY)
        operation = order.operations.get()
        self.assertEqual(operation.name, "Extrude")
        self.assertEqual(operation.units_per_hour, Decimal("180"))
        self.assertAlmostEqual(
            operation.planned_minutes, Decimal("1423.33"), places=2
        )
        self.assertEqual(order.routing, self.bom.routing)

    def test_re_rating_the_machine_does_not_re_time_a_run(self):
        order = self.routed_order()
        order.release(TODAY)
        frozen = order.operations.get().planned_minutes
        frozen_total = order.planned_minutes()
        line = self.bom.routing.operations.get()
        line.units_per_hour = Decimal("90")
        line.save()
        self.extruder.capacity_per_hour = Decimal("90")
        self.extruder.save()
        self.assertEqual(order.operations.get().planned_minutes, frozen)
        # And the figure the run reports for itself, which has to come
        # from what was frozen rather than from the routing as it is now:
        # halving the rate would otherwise double a run that has already
        # happened.
        self.assertEqual(order.planned_minutes(), frozen_total)
        self.assertEqual(
            order.bottleneck().planned_minutes, frozen
        )

    def test_an_operation_added_to_the_routing_does_not_join_a_running_order(self):
        order = self.routed_order()
        order.release(TODAY)
        RoutingOperation.objects.create(
            routing=self.bom.routing, sequence=20, name="Wind",
            work_centre=self.extruder, setup_minutes=Decimal("10"),
            units_per_hour=Decimal("4000"), rate_uom=self.kg,
        )
        self.assertEqual(order.operations.count(), 1)
        self.assertAlmostEqual(
            order.planned_minutes(), Decimal("1423.33"), places=2
        )

    def test_a_released_run_refuses_a_new_operation(self):
        order = self.routed_order()
        order.release(TODAY)
        with self.assertRaises(ValidationError) as caught:
            WorkOrderOperation.objects.create(
                work_order=order, sequence=99, name="Sneaked in",
                work_centre=self.extruder, units_per_hour=Decimal("100"),
                planned_minutes=Decimal("10"),
            )
        self.assertIn("frozen when it was released", str(caught.exception))

    def test_nor_will_it_give_one_up(self):
        order = self.routed_order()
        order.release(TODAY)
        with self.assertRaises(ValidationError):
            order.operations.get().delete()

    def test_a_rate_nobody_stated_is_refused_at_release(self):
        # Asked here, where it can still be answered. By the first shift
        # the loom has been running for a day.
        self.extruder.capacity_per_hour = None
        self.extruder.save()
        order = self.routed_order(
            routing=self.routing(rows=[("Extrude", self.extruder, "90", None)])
        )
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("how long this operation takes", str(caught.exception))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT)

    def test_a_run_with_no_routing_still_runs(self):
        # Most of what this plant makes has one operation and nobody has
        # bothered to write it down. That must not stop a run.
        order = self.order("1000")
        order.release(TODAY)
        self.assertEqual(order.operations.count(), 0)
        self.assertEqual(order.planned_minutes(), Decimal("0"))
        self.assertIsNone(order.bottleneck())

    def test_the_run_knows_what_it_waits_on(self):
        cutting = WorkCentre.objects.create(code="CUT-1", name="Cut and stitch")
        order = self.routed_order(routing=self.routing(code="R-TWO", rows=[
            ("Extrude", self.extruder, "90", "180"),
            ("Wind", cutting, "10", "4000"),
        ]))
        order.release(TODAY)
        self.assertEqual(order.bottleneck().name, "Extrude")
        self.assertAlmostEqual(
            order.planned_minutes(), Decimal("1493.33"), places=2
        )


class WhetherTheMachineHasTheHoursTests(RoutingTestCase):
    def setUp(self):
        super().setUp()
        self.start = datetime.date(2026, 6, 1)
        self.end = datetime.date(2026, 6, 7)

    def released(self, quantity="4000", start=None, end=None):
        order = self.routed_order(quantity)
        order.scheduled_start = start or self.start
        order.scheduled_end = end or self.end
        order.save()
        order.release(TODAY)
        return order

    def test_a_week_of_a_continuous_line(self):
        self.released()
        report = capacity_report(self.extruder, self.start, self.end)
        # Seven days at 24 hours = 10,080 minutes.
        self.assertEqual(report["available_minutes"], Decimal("10080.00"))
        self.assertAlmostEqual(report["load_minutes"], Decimal("1423.33"), places=2)
        self.assertAlmostEqual(
            report["utilisation_percent"], Decimal("14.12"), places=2
        )

    def test_a_line_that_does_not_run_on_sundays(self):
        self.extruder.working_days = "123456"
        self.extruder.save()
        report = capacity_report(self.extruder, self.start, self.end)
        # Monday to Sunday less the Sunday: six days at 24 hours.
        self.assertEqual(report["available_minutes"], Decimal("8640.00"))
        self.assertEqual(report["working_days"], 6)

    def test_a_window_that_is_only_the_weekend_has_no_hours(self):
        """
        The case a fraction of a nominal week cannot answer.

        Six sevenths of a two-day weekend is a day and a half of
        capacity on a line that does not work weekends at all, and a
        planner told that takes an order nothing can make.
        """
        self.extruder.working_days = "12345"
        self.extruder.save()
        report = capacity_report(
            self.extruder, datetime.date(2026, 6, 6), datetime.date(2026, 6, 7)
        )
        self.assertEqual(report["available_minutes"], Decimal("0"))
        self.assertIsNone(report["utilisation_percent"])

    def test_a_public_holiday_takes_the_day_out(self):
        from apps.hr.calendars import PublicHoliday

        PublicHoliday.objects.create(
            name="Bakrid", date=datetime.date(2026, 6, 3)
        )
        report = capacity_report(self.extruder, self.start, self.end)
        # Seven days less the one nobody works: six at 24 hours.
        self.assertEqual(report["available_minutes"], Decimal("8640.00"))

    def test_a_holiday_for_another_region_does_not_shut_this_plant(self):
        from apps.hr.calendars import PublicHoliday

        PublicHoliday.objects.create(
            name="Pongal", date=datetime.date(2026, 6, 3), region="TN"
        )
        self.extruder.holiday_region = "TS"
        self.extruder.save()
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["available_minutes"], Decimal("10080.00"))

    def test_a_pattern_nobody_can_read_is_refused(self):
        self.extruder.working_days = "Mon-Sat"
        with self.assertRaises(ValidationError):
            self.extruder.full_clean()

    def test_two_shifts_rather_than_three(self):
        self.extruder.available_hours_per_day = Decimal("16")
        self.extruder.save()
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["available_minutes"], Decimal("6720.00"))

    def test_a_draft_run_is_not_load(self):
        # It may never happen. Counting it would have a planner turn work
        # away for a machine that is standing idle.
        order = self.routed_order()
        order.scheduled_start = self.start
        order.scheduled_end = self.end
        order.save()
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["load_minutes"], Decimal("0"))

    def test_nor_is_a_closed_one(self):
        order = self.released()
        order.close(TODAY)
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["load_minutes"], Decimal("0"))

    def test_a_run_scheduled_elsewhere_in_the_year(self):
        self.released(
            start=datetime.date(2026, 9, 1), end=datetime.date(2026, 9, 7)
        )
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["load_minutes"], Decimal("0"))

    def test_a_run_that_only_overlaps_the_window_counts_whole(self):
        # A run does not half-occupy a loom, and a planner asking "can I
        # take this order" is better served by a number that rounds
        # against them.
        self.released(
            start=datetime.date(2026, 5, 28), end=datetime.date(2026, 6, 2)
        )
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertAlmostEqual(report["load_minutes"], Decimal("1423.33"), places=2)

    def test_the_machine_answers_for_itself(self):
        self.released()
        self.assertEqual(
            self.extruder.capacity(self.start, self.end)["load_minutes"],
            capacity_report(self.extruder, self.start, self.end)["load_minutes"],
        )

    def test_it_names_the_runs_so_a_planner_can_move_one(self):
        order = self.released()
        report = capacity_report(self.extruder, self.start, self.end)
        self.assertEqual(report["runs"], [order])


class ARateWithNoUnitOnItTests(RoutingTestCase):
    """
    One routing serves several bills of materials, a kilo is not a sack,
    and a rate read against the wrong unit gives a plausible number and
    a date nobody can keep. Found by probing: a coating line rated at
    180 kg an hour, asked for fifty thousand pieces, answered eleven
    days and meant nothing by it.
    """

    def setUp(self):
        super().setUp()
        self.pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )

    def test_a_rate_in_kilos_read_against_pieces(self):
        routing = self.routing(rows=[("Cut", self.extruder, "20", "4200")])
        with self.assertRaises(ValidationError) as caught:
            routing.total_minutes(Decimal("50000"), self.pcs)
        self.assertIn("not the same kind of thing", str(caught.exception))

    def test_the_machines_nominal_rate_too(self):
        routing = self.routing(rows=[("Cut", self.extruder, "20", None)])
        with self.assertRaises(ValidationError) as caught:
            routing.operations.get().rate(self.pcs)
        self.assertIn("not the same kind of thing", str(caught.exception))

    def test_a_rate_that_does_not_say_what_it_counts(self):
        self.extruder.capacity_uom = None
        self.extruder.save()
        routing = self.routing(rows=[("Extrude", self.extruder, "90", None)])
        with self.assertRaises(ValidationError) as caught:
            routing.operations.get().rate(self.kg)
        self.assertIn("does not say what it counts", str(caught.exception))

    def test_a_rate_in_a_comparable_unit_is_converted_rather_than_refused(self):
        # Tonnes and kilogrammes are the same kind of thing, so a line
        # quoted in tonnes an hour has a perfectly good answer in kilos.
        tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        routing = Routing.objects.create(code="R-T", name="In tonnes")
        operation = RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Extrude",
            work_centre=self.extruder, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("0.18"), rate_uom=tonne,
        )
        self.assertEqual(operation.rate(self.kg), Decimal("180.00"))

    def test_a_rate_must_say_what_it_counts(self):
        from django.db import IntegrityError, transaction

        routing = Routing.objects.create(code="R-BARE", name="Bare")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RoutingOperation.objects.create(
                    routing=routing, sequence=10, name="Extrude",
                    work_centre=self.extruder, units_per_hour=Decimal("180"),
                )

    def test_and_a_run_is_refused_at_release(self):
        # Asked where it can still be answered, not on the shop floor.
        # The bill of materials counts kilogrammes of tape; this routing
        # is rated in pieces an hour.
        routing = self.routing(code="R-PCS", rows=[("Cut", self.extruder, "20", "4200")])
        routing.operations.update(rate_uom=self.pcs)
        order = self.routed_order(routing=routing)
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("not the same kind of thing", str(caught.exception))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT)


class AMachineThatIsNotThereTests(RoutingTestCase):
    def test_a_retired_routing(self):
        routing = self.routing(code="R-OLD")
        routing.is_active = False
        routing.save()
        order = self.routed_order(routing=routing)
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("has been retired", str(caught.exception))

    def test_a_decommissioned_work_centre(self):
        self.extruder.is_active = False
        self.extruder.save()
        order = self.routed_order()
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("not in service", str(caught.exception))
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT)


class AWindowThatMeansSomethingTests(RoutingTestCase):
    START = datetime.date(2026, 6, 1)
    END = datetime.date(2026, 6, 7)

    def test_a_window_the_wrong_way_round(self):
        # It used to report negative hours available, and a negative
        # utilisation to go with them.
        with self.assertRaises(ValidationError) as caught:
            capacity_report(self.extruder, self.END, self.START)
        self.assertIn("runs backwards", str(caught.exception))

    def test_a_released_run_with_no_dates_is_not_in_every_window(self):
        # Counting it everywhere had a planner turning work away for a
        # machine that was standing idle.
        order = self.routed_order()
        order.release(TODAY)
        self.assertIsNone(order.scheduled_start)
        report = capacity_report(self.extruder, self.START, self.END)
        self.assertEqual(report["load_minutes"], Decimal("0"))

    def test_but_it_is_not_hidden_either(self):
        order = self.routed_order()
        order.release(TODAY)
        report = capacity_report(self.extruder, self.START, self.END)
        self.assertAlmostEqual(
            report["unscheduled_minutes"], Decimal("1423.33"), places=2
        )

    def test_a_dated_run_is_load_and_not_unscheduled(self):
        order = self.routed_order()
        order.scheduled_start = self.START
        order.scheduled_end = self.END
        order.save()
        order.release(TODAY)
        report = capacity_report(self.extruder, self.START, self.END)
        self.assertAlmostEqual(report["load_minutes"], Decimal("1423.33"), places=2)
        self.assertEqual(report["unscheduled_minutes"], Decimal("0"))

    def test_a_run_with_only_one_date_counts_as_unscheduled(self):
        # Half a schedule is not a schedule: there is no window it
        # belongs in.
        order = self.routed_order()
        order.scheduled_start = self.START
        order.save()
        order.release(TODAY)
        report = capacity_report(self.extruder, self.START, self.END)
        self.assertEqual(report["load_minutes"], Decimal("0"))
        self.assertGreater(report["unscheduled_minutes"], Decimal("0"))
