"""
Booked at the conversion station on the day shift of 1 June: C-1
stopped 25 minutes for a broken needle, 40 sacks spoiled at cut and
stitch for mis-cutting, and — on a run printed first on P-1 — 3,000
sacks counted off the printer.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from .machines import Machine
from .oee import _downtime
from .orders import WorkCentre, WorkOrder
from .routing import Routing, RoutingOperation
from .scrap import ScrapReason, flow
from .shifts import Downtime, DowntimeReason
from .station_floor import (
    book_scrap,
    book_stoppage,
    count_step,
    void_count,
    void_scrap,
    void_stoppage,
)
from .tests_bag_counts import BagStationApiTests, ConversionTestCase
from .tests_orders import TODAY
from .tests_station import at

NOON = at(TODAY, 12)


class FloorTestCase(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.needle = DowntimeReason.objects.create(code="NEEDLE", name="Needle broke")
        self.miscut = ScrapReason.objects.create(code="MISCUT", name="Cut off size")


class StoppageTests(FloorTestCase):
    def test_booked_against_the_machine_the_shift_and_the_run_on_it(self):
        row = book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "25", "Needle 3",
                            at=NOON)
        self.assertEqual((row.work_centre, row.machine, row.shift, row.shift_date, row.minutes,
                          row.work_order, row.station, row.booked_by),
                         (self.cutting, self.c1, self.day, TODAY, Decimal("25"),
                          self.bag_run, self.cv, self.operator))
        self.assertEqual(len(_downtime(self.cutting, TODAY, TODAY, machine=self.c1)), 1)

    def test_withdrawn_by_a_supervisor_it_stopped_nothing(self):
        row = book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "25", at=NOON)
        with self.assertRaisesMessage(ValidationError, "approved by somebody else"):
            void_stoppage(row, self.cv, self.operator, self.operator, "Wrong loom")
        void_stoppage(row, self.cv, self.supervisor, self.operator, "Wrong loom")
        self.assertEqual(list(_downtime(self.cutting, TODAY, TODAY, machine=self.c1)), [])
        with self.assertRaisesMessage(ValidationError, "already withdrawn"):
            row.void("Again")
        row.minutes = Decimal("5")
        with self.assertRaisesMessage(ValidationError, "was withdrawn"):
            row.save()
        # And it no longer fills the twelve-hour shift: 700 more fits in
        # 720, which the withdrawn 25 would have pushed over.
        book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "700", at=NOON)

    def test_what_a_stoppage_needs(self):
        with self.assertRaisesMessage(ValidationError, "PAINT is not a stoppage reason"):
            book_stoppage(self.cv, self.operator, self.c1, "PAINT", "5", at=NOON)
        with self.assertRaisesMessage(ValidationError, "Minutes stopped is more than nothing"):
            book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "0", at=NOON)
        with self.assertRaisesMessage(ValidationError, "Minutes stopped is a number"):
            book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "lots", at=NOON)
        with self.assertRaisesMessage(ValidationError, "Nobody is signed in"):
            book_stoppage(self.cv, None, self.c1, "NEEDLE", "5", at=NOON)
        stranger = Machine.objects.create(work_centre=self.cutting, code="C-9")
        with self.assertRaisesMessage(ValidationError, "does not serve C-9"):
            book_stoppage(self.cv, self.operator, stranger, "NEEDLE", "5", at=NOON)
        other = Downtime.objects.create(work_centre=self.cutting, machine=self.c1,
                                        shift_date=TODAY, reason=self.needle,
                                        minutes=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "was not booked at CV-1"):
            void_stoppage(other, self.cv, self.supervisor, self.operator, "x")

    def test_only_at_a_station_in_use_on_a_shift_by_somebody_employed(self):
        import datetime

        from apps.hr.models import Employee

        type(self.cv).objects.filter(pk=self.cv.pk).update(is_active=False)
        self.cv.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is not in use"):
            book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "5", at=NOON)
        type(self.cv).objects.filter(pk=self.cv.pk).update(is_active=True)
        self.cv.refresh_from_db()
        Employee.objects.filter(pk=self.operator.pk).update(
            termination_date=TODAY - datetime.timedelta(days=1))
        self.operator.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "does not work here on"):
            book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "5", at=NOON)
        self.night.delete()
        with self.assertRaisesMessage(ValidationError, "No shift runs at 22:00"):
            book_stoppage(self.cv, self.other, self.c1, "NEEDLE", "5", at=at(TODAY, 22))

    def test_a_machine_with_no_run_on_it_stops_against_no_run(self):
        WorkOrder.objects.filter(pk=self.bag_run.pk).update(status="closed")
        row = book_stoppage(self.cv, self.operator, self.c1, "NEEDLE", "10", at=NOON)
        self.assertIsNone(row.work_order)


class ScrapAtTheMachineTests(FloorTestCase):
    def test_booked_as_scrap_off_the_step_for_its_reason(self):
        entry = book_scrap(self.cv, self.operator, self.c1, "MISCUT", "40", at=NOON)
        self.assertEqual((entry.quantity_produced, entry.quantity_scrapped, entry.machine,
                          entry.entry_date, entry.posted),
                         (Decimal("0"), Decimal("40"), self.c1, TODAY, True))
        (row,) = flow(self.bag_run)
        self.assertEqual(row["scrap_by_reason"], {"MISCUT": Decimal("40")})
        void_scrap(entry, self.cv, self.supervisor, self.operator, "Counted twice")
        (row,) = flow(self.bag_run)
        self.assertEqual(row["scrap"], Decimal("0"))

    def test_what_scrap_needs(self):
        with self.assertRaisesMessage(ValidationError, "SMUDGE is not a scrap reason"):
            book_scrap(self.cv, self.operator, self.c1, "SMUDGE", "4", at=NOON)
        entry = book_scrap(self.cv, self.operator, self.c1, "MISCUT", "4", at=NOON)
        with self.assertRaisesMessage(ValidationError, "Say why"):
            void_scrap(entry, self.cv, self.supervisor, self.operator, " ")
        good = self.count().inspection
        from .orders import ProductionEntry

        output = ProductionEntry.objects.get(lot=good.lot)
        with self.assertRaisesMessage(ValidationError, "is not scrap booked at CV-1"):
            void_scrap(output, self.cv, self.supervisor, self.operator, "x")


class CountedAtTheMachineTests(FloorTestCase):
    def setUp(self):
        super().setUp()
        printing = WorkCentre.objects.create(code="PRINT", name="Printing")
        self.p1 = Machine.objects.create(work_centre=printing, code="P-1")
        self.cv.machines.add(self.p1)
        routing = Routing.objects.create(code="R-PRCONV", name="Print then convert")
        RoutingOperation.objects.create(routing=routing, sequence=10, name="Print",
                                        work_centre=printing, units_per_hour=Decimal("3000"),
                                        rate_uom=self.pcs)
        RoutingOperation.objects.create(routing=routing, sequence=20, name="Cut and stitch",
                                        work_centre=self.cutting,
                                        units_per_hour=Decimal("600"), rate_uom=self.pcs)
        WorkOrder.objects.filter(pk=self.bag_run.pk).update(status="closed")
        self.printed = WorkOrder.objects.create(item=self.bag, bom=self.bag_spec.bom,
                                                quantity_ordered=Decimal("5000"),
                                                uom=self.pcs, warehouse=self.plant,
                                                routing=routing)
        # The spec's routing is one step; this run is released on its own.
        from .woven import BagSpecification

        BagSpecification.objects.filter(pk=self.bag_spec.pk).update(routing=routing)
        self.bag_spec.bom.__class__.objects.filter(pk=self.bag_spec.bom.pk).update(
            routing=routing)
        self.printed.bom.refresh_from_db()
        self.printed.release(TODAY)

    def test_a_step_counted_where_it_is_done(self):
        counted = count_step(self.cv, self.operator, self.p1, "3000", at=NOON)
        self.assertEqual((counted.operation.name, counted.quantity_good, counted.machine,
                          counted.reported_on), ("Print", Decimal("3000"), self.p1, TODAY))
        with self.assertRaisesMessage(ValidationError, "is the last step"):
            count_step(self.cv, self.operator, self.c1, "10", at=NOON)
        void_count(counted, self.cv, self.supervisor, self.operator, "Miscounted")
        self.assertEqual(flow(self.printed)[0]["good"], Decimal("0"))

    def test_scrap_lands_on_the_step_it_happened_at(self):
        book_scrap(self.cv, self.operator, self.p1, "MISCUT", "40", at=NOON)
        rows = {row["operation"]: row for row in flow(self.printed)}
        self.assertEqual((rows["Print"]["scrap"], rows["Cut and stitch"]["scrap"]),
                         (Decimal("40"), Decimal("0")))

    def test_a_count_from_elsewhere_is_not_this_stations(self):
        from .scrap import report

        elsewhere = report(self.printed.operations.get(name="Print"), "5", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "was not counted at CV-1"):
            void_count(elsewhere, self.cv, self.supervisor, self.operator, "x")


class FloorApiTests(BagStationApiTests):
    def test_booked_and_withdrawn_over_the_api(self):
        DowntimeReason.objects.create(code="NEEDLE", name="Needle broke")
        ScrapReason.objects.create(code="MISCUT", name="Cut off size")
        self.post_cv("sign-in/", {"pin": self.pin})
        response = self.post_cv("stoppage/", {"machine": "C-1", "reason": "NEEDLE",
                                              "minutes": "25"})
        self.assertEqual(response.status_code, 201, response.content)
        stopped = response.json()
        self.assertEqual((stopped["minutes"], stopped["run"]), ("25", self.bag_run.number))
        response = self.post_cv(f"stoppage/{stopped['id']}/void/",
                                {"supervisor_pin": self.supervisor_pin, "reason": "Wrong"})
        self.assertEqual(response.status_code, 200, response.content)
        response = self.post_cv("scrap/", {"machine": "C-1", "reason": "MISCUT",
                                           "quantity": "40"})
        self.assertEqual(response.status_code, 201, response.content)
        spoiled = response.json()
        response = self.post_cv(f"scrap/{spoiled['id']}/void/",
                                {"supervisor_pin": self.supervisor_pin, "reason": "Twice"})
        self.assertEqual(response.status_code, 200, response.content)
        response = self.post_cv("count/", {"machine": "C-1", "quantity": "10"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("is the last step", response.content.decode())
        response = self.post_cv("stoppage/", {"machine": "Z-9", "reason": "NEEDLE",
                                              "minutes": "5"})
        self.assertEqual(response.status_code, 400)


class TwoStepsOnOneBankTests(FloorTestCase):
    def setUp(self):
        super().setUp()
        routing = Routing.objects.create(code="R-2X", name="Cut, then stitch")
        for sequence, name in ((10, "Cut"), (20, "Stitch")):
            RoutingOperation.objects.create(routing=routing, sequence=sequence, name=name,
                                            work_centre=self.cutting,
                                            units_per_hour=Decimal("600"), rate_uom=self.pcs)
        WorkOrder.objects.filter(pk=self.bag_run.pk).update(status="closed")
        self.bag_spec.bom.__class__.objects.filter(pk=self.bag_spec.bom.pk).update(
            routing=routing)
        self.twice = WorkOrder.objects.create(item=self.bag, bom=self.bag_spec.bom,
                                              quantity_ordered=Decimal("5000"), uom=self.pcs,
                                              warehouse=self.plant, routing=routing)
        self.twice.bom.refresh_from_db()
        self.twice.release(TODAY)

    def test_a_bank_passed_twice_is_refused_unless_the_machine_is_named(self):
        with self.assertRaisesMessage(ValidationError, "more than once"):
            count_step(self.cv, self.operator, self.c1, "10", at=NOON)
        self.twice.operations.filter(name="Cut").update(machine=self.c1)
        counted = count_step(self.cv, self.operator, self.c1, "10", at=NOON)
        self.assertEqual(counted.operation.name, "Cut")
