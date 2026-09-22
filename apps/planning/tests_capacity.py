"""
Scheduling against machines that are already busy.

The fixture's loom runs 60 kg an hour with an hour of setup, so 1,000
kg is 1,060 minutes — seventeen hours and forty minutes. On a
continuous line that fits inside a day; on an eight-hour shift it
fills two days and part of a third.
"""

import datetime
from decimal import Decimal

from apps.manufacturing.orders import WorkOrder

from .capacity import LoadBook, load_profile, overloaded_weeks
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class OneRunAtATimeTests(PlanningTestCase):
    def test_a_free_machine_gives_the_shortest_schedule(self):
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.lead_days, 1)
        self.assertFalse(fabric.is_overloaded)
        self.assertEqual(fabric.bottleneck, self.loom)

    def test_two_runs_on_one_loom_do_not_both_get_the_same_hours(self):
        """
        The failure this whole module exists to fix. Both orders need
        the same loom in the same week, and told separately they each
        came back with a date the other one made impossible.
        """
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        fabric = run.orders.get(item=self.fabric)
        # 2,000 kg is 2,120 minutes of loom — one order, because the
        # two call-offs share a date — over eight-hour days: four days
        # full and part of a fifth.
        self.assertEqual(fabric.quantity, Decimal("2000"))
        self.assertEqual(fabric.lead_days, 4)

    def test_a_machine_already_booked_pushes_the_run_earlier(self):
        """
        A loom with three days of committed work on it cannot also
        start a new run on those days.
        """
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        # Another fabric on the same loom, filling it for the three
        # days before the new order is wanted. A different item, so it
        # occupies the machine without covering the demand.
        from apps.manufacturing.bom import BillOfMaterials, BomComponent

        other = self.fabric.__class__.objects.create(
            sku="FAB-12X12", name="Heavier fabric", uom=self.kg
        )
        other_bom = BillOfMaterials.objects.create(
            item=other, name="Heavier", quantity_produced=Decimal("100"),
            uom=self.kg, routing=self.weaving,
        )
        BomComponent.objects.create(
            bom=other_bom, item=self.tape, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )
        self.stock(self.tape, "5000")
        busy = WorkOrder.objects.create(
            item=other, bom=other_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant,
            scheduled_start=self.day(27), scheduled_end=self.day(29),
        )
        busy.release(TODAY)

        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.plan().orders.get(item=self.fabric, quantity=Decimal("1000"))
        # Without the committed run the new one would start on day 28;
        # with it, the loom's free hours run out earlier.
        self.assertLess(fabric.release_on, self.day(28))

    def test_the_bottleneck_is_the_machine_that_held_it_longest(self):
        """
        The only one worth adding capacity to, and the one to name
        when the date slips.
        """
        self.sell(self.fabric, "1000", self.day(30))
        found = self.orders()
        self.assertEqual(found["FAB-10X10"].bottleneck, self.loom)
        self.assertEqual(found["TAPE-1000"].bottleneck, self.extruder)


class ARoutingIsAChainTests(PlanningTestCase):
    """
    Several operations on several machines, which is what a real
    routing is. With one operation the sequence cannot be got wrong
    and the bottleneck is whichever machine there is.
    """

    def setUp(self):
        super().setUp()
        from apps.manufacturing.orders import WorkCentre
        from apps.manufacturing.routing import RoutingOperation

        # A slow finishing machine after the loom: 20 kg an hour, so
        # 1,000 kg is fifty hours against the loom's seventeen.
        self.laminator = WorkCentre.objects.create(
            code="LAM-1", name="Lamination line",
            capacity_per_hour=Decimal("20"), capacity_uom=self.kg,
            available_hours_per_day=Decimal("24"),
        )
        RoutingOperation.objects.create(
            routing=self.weaving, sequence=20, name="Laminate",
            work_centre=self.laminator, setup_minutes=Decimal("0"),
        )

    def test_each_operation_finishes_when_the_next_one_starts(self):
        """
        The routing runs in sequence, so it is placed in reverse: the
        last operation ends on the day the run is wanted and every
        earlier one has to be done before the next begins.
        """
        from .capacity import LoadBook, schedule_make

        book = LoadBook(self.plant, TODAY, self.day(90))
        placed = schedule_make(
            book, self.fabric_bom, Decimal("1000"), self.kg,
            self.day(30), TODAY,
        )
        spans = placed["spans"]
        self.assertEqual([s["operation"].sequence for s in spans], [10, 20])
        weave, laminate = spans
        # The lamination ends on the day the run is due; the weaving
        # has to be finished by the moment lamination starts.
        self.assertEqual(laminate["finish"], self.day(30))
        self.assertEqual(weave["finish"], laminate["start"])
        # 3,000 minutes of lamination over 24-hour days fills two and
        # spills 120 minutes into a third, so it starts two days before
        # it is due — and the weaving has to be done by then.
        self.assertEqual(laminate["start"], datetime.date(2026, 6, 29))
        self.assertEqual(placed["start"], datetime.date(2026, 6, 29))

    def test_the_bottleneck_is_the_machine_that_held_it_longest(self):
        """
        The only one worth adding capacity to. The loom holds 1,060
        minutes and the laminator 3,000, so the laminator is the
        constraint even though the loom is the obvious machine.
        """
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.bottleneck, self.laminator)

    def test_two_machines_share_a_day_rather_than_taking_one_each(self):
        """
        Days are not added up per machine. The lamination's last 120
        minutes and the whole of the weaving both fall on the same
        day, because they are different machines and that day has
        room on each of them.
        """
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.lead_days, 2)
        self.assertEqual(fabric.release_on, datetime.date(2026, 6, 29))


class WhenTheMachinesCannotFitItTests(PlanningTestCase):
    def test_a_run_that_cannot_fit_is_marked_overloaded(self):
        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        # 600,000 kg at 60 an hour on a one-hour day is ten thousand
        # days of loom, which no amount of starting early fixes.
        self.sell(self.fabric, "600000", self.day(5))
        fabric = self.orders()["FAB-10X10"]
        self.assertTrue(fabric.is_overloaded)
        self.assertTrue(fabric.is_late())

    def test_it_says_a_full_machine_rather_than_a_long_lead_time(self):
        """
        The two have different answers. A lead time is shortened by
        ringing a vendor and a full loom is not, and a planner handed
        one number cannot tell which conversation to have.
        """
        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        self.sell(self.fabric, "600000", self.day(5))
        fabric = self.orders()["FAB-10X10"]
        self.assertIn("no room before", fabric.why_late())
        self.assertIn(self.loom.code, fabric.why_late())

    def test_a_late_purchase_says_lead_time_instead(self):
        self.sell(self.fabric, "1000", self.day(3))
        virgin = self.orders()["PP-RAFFIA"]
        self.assertTrue(virgin.is_late())
        self.assertIn("days to buy", virgin.why_late())

    def test_nothing_on_time_explains_itself(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.assertIsNone(self.orders()["FAB-10X10"].why_late())


class NoRoutingNoMachineTests(PlanningTestCase):
    def test_a_bom_with_no_routing_falls_back_to_the_stated_default(self):
        """
        Without operations there is no machine to be busy, so the
        plant's stated default is the honest answer.
        """
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.lead_days, 2)
        self.assertIsNone(fabric.bottleneck)
        self.assertFalse(fabric.is_overloaded)


class WhatIsAlreadyOnTheMachinesTests(PlanningTestCase):
    def book(self):
        return LoadBook(self.plant, TODAY, self.day(60))

    def test_a_committed_run_is_spread_over_the_days_it_says_it_runs(self):
        """
        A run scheduled from the 4th to the 9th does not say which of
        those days the loom is turning, so its minutes are spread.
        That is an approximation and the only honest one: the
        alternative is to invent a day.
        """
        self.stock(self.tape, "5000")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom,
            quantity_ordered=Decimal("1000"), uom=self.kg,
            warehouse=self.plant, scheduled_start=self.day(10),
            scheduled_end=self.day(13),
        )
        order.release(TODAY)
        book = self.book()
        # 1,060 minutes over four days is 265 a day.
        self.assertEqual(book.booked(self.loom, self.day(10)), Decimal("265"))
        self.assertEqual(book.booked(self.loom, self.day(13)), Decimal("265"))
        self.assertEqual(book.booked(self.loom, self.day(14)), Decimal("0"))

    def test_a_released_run_with_no_dates_is_reported_not_booked(self):
        """
        Booking it somewhere would invent the fact the run is missing;
        ignoring it would have a planner promise a machine that is not
        free.
        """
        self.stock(self.tape, "5000")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom,
            quantity_ordered=Decimal("1000"), uom=self.kg,
            warehouse=self.plant,
        )
        order.release(TODAY)
        book = self.book()
        self.assertEqual(book.booked(self.loom, self.day(10)), Decimal("0"))
        self.assertEqual(book.unscheduled[self.loom.pk], Decimal("1060"))

    def test_a_machine_is_shut_on_the_days_it_does_not_work(self):
        self.loom.working_days = "12345"
        self.loom.save()
        book = self.book()
        # 2026-06-06 is a Saturday.
        self.assertEqual(
            book.capacity_minutes(self.loom, datetime.date(2026, 6, 6)),
            Decimal("0"),
        )
        self.assertEqual(
            book.capacity_minutes(self.loom, datetime.date(2026, 6, 5)),
            Decimal("1440"),
        )

    def test_a_run_steps_over_a_weekend(self):
        self.loom.working_days = "12345"
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        # Wanted Monday the 8th; 1,060 minutes over eight-hour days
        # needs the Monday, the Friday and the Thursday before it.
        self.sell(self.fabric, "1000", self.day(7))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.needed_by, datetime.date(2026, 6, 8))
        self.assertEqual(fabric.release_on, datetime.date(2026, 6, 4))


class MaintenanceTakesTheMachineOutTests(PlanningTestCase):
    """
    Leaving planned maintenance out of the load had the plan run a
    loom straight through a service the plant fully intended to do,
    and then blame the lateness on the loom.
    """

    def service(self, when, minutes="1440"):
        from apps.manufacturing.maintenance import MaintenanceJob

        return MaintenanceJob.objects.create(
            work_centre=self.loom, due_on=when,
            planned_minutes=Decimal(minutes),
        )

    def test_a_booked_service_is_load_on_the_machine(self):
        from .capacity import LoadBook

        self.service(self.day(10))
        book = LoadBook(self.plant, TODAY, self.day(60))
        self.assertEqual(book.booked(self.loom, self.day(10)), Decimal("1440"))
        self.assertEqual(book.maintenance[self.loom.pk], Decimal("1440"))

    def test_a_service_already_done_is_history(self):
        from .capacity import LoadBook

        job = self.service(self.day(10))
        job.complete(on_date=self.day(10))
        book = LoadBook(self.plant, TODAY, self.day(60))
        self.assertEqual(book.booked(self.loom, self.day(10)), Decimal("0"))

    def test_a_run_is_not_scheduled_through_a_service(self):
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        free = self.orders()["FAB-10X10"].release_on
        # A full day of service on the day the run would otherwise
        # have used pushes it earlier.
        self.service(free + datetime.timedelta(days=1), minutes="480")
        self.assertLess(self.orders()["FAB-10X10"].release_on, free)

    def test_the_load_report_names_the_maintenance_separately(self):
        from .capacity import LoadBook, load_profile

        self.service(self.day(3))
        book = LoadBook(self.plant, TODAY, self.day(30))
        rows = load_profile(book, [self.loom], TODAY, self.day(7))
        self.assertEqual(rows[0]["maintenance_minutes"], Decimal("1440"))


class TheLoadPictureTests(PlanningTestCase):
    def test_a_week_reports_what_is_asked_against_what_there_is(self):
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        book = LoadBook(self.plant, TODAY, self.day(60))
        rows = load_profile(book, [self.loom], TODAY, self.day(7))
        self.assertTrue(rows)
        first = rows[0]
        self.assertGreater(first["available_minutes"], Decimal("0"))
        self.assertIsNotNone(first["utilisation_percent"])

    def test_the_command_names_the_weeks_a_machine_is_over(self):
        from io import StringIO

        from django.core.management import call_command

        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        self.stock(self.tape, "50000")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom,
            quantity_ordered=Decimal("5000"), uom=self.kg,
            warehouse=self.plant, scheduled_start=self.day(1),
            scheduled_end=self.day(3),
        )
        order.release(TODAY)
        out = StringIO()
        call_command("run_mrp", "P", "--on", str(TODAY), stdout=out)
        report = out.getvalue()
        self.assertIn("over its hours", report)
        self.assertIn(self.loom.code, report)

    def test_an_overloaded_week_is_listed_worst_first(self):
        self.loom.available_hours_per_day = Decimal("1")
        self.loom.save()
        self.stock(self.tape, "50000")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom,
            quantity_ordered=Decimal("5000"), uom=self.kg,
            warehouse=self.plant, scheduled_start=self.day(1),
            scheduled_end=self.day(3),
        )
        order.release(TODAY)
        book = LoadBook(self.plant, TODAY, self.day(30))
        rows = overloaded_weeks(book, [self.loom], TODAY, self.day(14))
        self.assertTrue(rows)
        self.assertLess(rows[0]["spare_minutes"], Decimal("0"))
