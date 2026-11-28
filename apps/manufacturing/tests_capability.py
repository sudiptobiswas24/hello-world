"""
Web speeds, and what each machine can take.

**Web speed.** The laminator runs the tube at 80 m/min; the lined sack
is cut 105 cm (100 + 3 + 2), so 80 x 60 / 1.05 = 4,571.4286 sacks an
hour. The cut-and-seal machine at 40 m/min makes 2,285.7143 liners an
hour of 105 cm. The blown-film haul-off at 25 m/min of 53.36 g a metre
is 80.04 kg an hour, held to the extruder's 70.

**Capability.** C-1 is the BCS that inserts liners; C-2 and C-3 do not.
The lined sack goes only on C-1: refused on C-2 when pinned, when
booked at the station, and on the dispatch board; and planning books
it on C-1's minutes while leaving C-2's for everything else. A 600-
minute lined job on C-1's 480-minute day takes 480 and 120 the next.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .machines import Machine, requirements
from .orders import SpeedBasis, WorkCentre, WorkOrderOperation
from .tests_liners import LinerTestCase
from .tests_orders import TODAY
from .woven import BagSpecification


class WebSpeedTests(LinerTestCase):
    def web(self, centre, speed, **extra):
        WorkCentre.objects.filter(pk=centre.pk).update(
            speed_basis=SpeedBasis.WEB, line_speed_m_per_min=Decimal(speed), **extra)
        centre.refresh_from_db()
        return centre

    def test_a_sack_or_a_liner_is_one_cut_length_of_the_web(self):
        self.assertEqual(self.lam_spec.cut_length_cm(), Decimal("105.00"))
        rate, unit = self.web(self.coater, "80").speed_for(self.lam_spec.bom)
        self.assertEqual((rate.quantize(Decimal("0.0001")), unit),
                         (Decimal("4571.4286"), self.pcs))
        rate, unit = self.web(self.sealer, "40").speed_for(self.liner_spec.bom)
        self.assertEqual((rate.quantize(Decimal("0.0001")), unit),
                         (Decimal("2285.7143"), self.pcs))

    def test_blown_film_is_its_weight_a_metre_held_to_the_extruder(self):
        rate, unit = self.web(self.blown, "25").speed_for(self.film.bom)
        self.assertEqual((rate, unit), (Decimal("80.04"), self.kg))
        self.web(self.blown, "25", capacity_per_hour=Decimal("70"), capacity_uom=self.kg)
        self.assertEqual(self.blown.speed_for(self.film.bom), (Decimal("70.0000"), self.kg))
        # An extruder rated in pieces cannot hold back kilogrammes.
        self.web(self.blown, "25", capacity_uom=self.pcs)
        with self.assertRaises(ValidationError):
            self.blown.speed_for(self.film.bom)

    def test_a_web_speed_is_given(self):
        centre = WorkCentre(code="LAM-2", name="Laminator", speed_basis=SpeedBasis.WEB)
        with self.assertRaisesMessage(ValidationError, "needs a web speed"):
            centre.save()

    def test_anything_else_at_its_stated_rate(self):
        from .bom import BillOfMaterials

        self.web(self.coater, "80", capacity_per_hour=Decimal("500"), capacity_uom=self.kg)
        typed = BillOfMaterials.objects.create(item=self.ldpe, name="Other", version=5,
                                               quantity_produced=Decimal("1"), uom=self.kg,
                                               is_default=False)
        self.assertEqual(self.coater.speed_for(typed), (Decimal("500.0000"), self.kg))


class CapabilityTestCase(LinerTestCase):
    def setUp(self):
        super().setUp()
        Machine.objects.filter(pk=self.c1.pk).update(inserts_liner=True)
        Machine.objects.filter(pk__in=[self.c2.pk, self.c3.pk]).update(inserts_liner=False)
        for machine in (self.c1, self.c2, self.c3):
            machine.refresh_from_db()
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.liner_item = self.liner
        spec.save()
        self.lined_spec = spec
        # Only the lined run left on the BCS bank, so the station knows
        # which run a bundle is for.
        for run in (self.bag_run, self.lam_run):
            run.cancel()
        self.lined_run = self.released(self.lam_bag, spec.bom, "2000", self.pcs)
        self.cutting_step = self.lined_run.operations.get(work_centre=self.cutting)


class WhatASackNeedsTests(CapabilityTestCase):
    def test_from_its_specification(self):
        self.assertEqual(requirements(self.lined_spec.bom),
                         {"width_cm": Decimal("60.00"), "length_cm": Decimal("105.00"),
                          "colours": 0, "liner": True})
        self.assertEqual(requirements(self.liner_spec.bom),
                         {"width_cm": Decimal("58.00"), "length_cm": Decimal("105.00")})
        self.assertEqual(requirements(self.film.bom), {"width_cm": Decimal("58.00")})
        self.assertEqual(requirements(self.spec.bom)["width_cm"],
                         self.spec.lay_flat_width_cm)
        self.assertEqual(requirements(None), {})

    def test_each_limit_refuses_on_its_own(self):
        press = Machine(code="P-9", work_centre=self.coater, max_colours=4)
        self.assertEqual(press.refuses({"colours": 6}), "P-9 prints 4 colours, not 6")
        self.assertEqual(press.refuses({"colours": 4, "liner": True}), "")
        loom = Machine(code="L-9", work_centre=self.coater, min_width_cm=Decimal("40"),
                       max_width_cm=Decimal("55"))
        self.assertEqual(loom.refuses({"width_cm": Decimal("60")}),
                         "L-9 takes 40 to 55 cm wide, not 60")
        self.assertEqual(loom.refuses({"width_cm": Decimal("35")}),
                         "L-9 takes 40 to 55 cm wide, not 35")
        self.assertEqual(loom.refuses({"width_cm": Decimal("55")}), "")
        bcs = Machine(code="B-9", work_centre=self.coater, max_length_cm=Decimal("100"))
        self.assertEqual(bcs.refuses({"length_cm": Decimal("105")}),
                         "B-9 takes any to 100 cm long, not 105")
        self.assertEqual(bcs.refuses({"liner": True}), "")


class OnlyWhereItCanBeMadeTests(CapabilityTestCase):
    def test_pinned_only_on_a_bcs_that_inserts_liners(self):
        step = WorkOrderOperation.objects.get(pk=self.cutting_step.pk)
        step.machine = self.c2
        with self.assertRaisesMessage(ValidationError, "C-2 inserts no liner"):
            step.save()
        step.machine = self.c1
        step.save()

    def test_booked_only_there_at_the_station(self):
        with self.assertRaisesMessage(ValidationError, "cannot be made here: C-2 inserts no"):
            self.count(machine=self.c2)

    def test_a_bcs_is_not_re_rated_under_a_run_on_it(self):
        WorkOrderOperation.objects.filter(pk=self.cutting_step.pk).update(machine=self.c1)
        self.c1.inserts_liner = False
        with self.assertRaisesMessage(ValidationError, "is on C-1, and C-1 inserts no liner"):
            self.c1.save()
        self.lined_run.cancel()
        self.c1.save()

    def test_the_board_puts_it_on_the_one_that_can(self):
        from .dispatch import build

        rows = [row for row in build() if row["operation"].pk == self.cutting_step.pk]
        self.assertEqual([row["machine"] for row in rows], [self.c1])
        Machine.objects.filter(pk=self.c1.pk).update(inserts_liner=False)
        (row,) = [row for row in build() if row["operation"].pk == self.cutting_step.pk]
        self.assertIn("No machine at CONV can take it", row["held"])
        self.assertIn("C-1 inserts no liner", row["held"])


class PlannedOnItsOwnMachinesTests(CapabilityTestCase):
    def test_a_lined_job_fills_the_liner_bcs_and_leaves_the_others(self):
        from apps.planning.capacity import LoadBook

        day = TODAY + datetime.timedelta(days=60)
        while not self.c1.calendar().is_working(day) or not self.cutting.calendar(
        ).is_working(day + datetime.timedelta(days=1)):
            day += datetime.timedelta(days=1)
        book = LoadBook(self.plant, TODAY, day + datetime.timedelta(days=30))
        pool = book.pool_for(self.cutting, self.lined_spec.bom)
        self.assertEqual(pool, (self.c1,))
        self.assertIsNone(book.pool_for(self.cutting, self.bag_spec.bom))
        one_day = self.c1.minutes_on(day)
        bank = book.free(self.cutting, day)
        first, last, short = book.take_forwards(self.cutting, one_day + 120, day,
                                                day + datetime.timedelta(days=30), pool)
        self.assertEqual((first, short), (day, False))
        self.assertGreater(last, day)
        self.assertEqual(book.free(self.cutting, day, pool), Decimal("0"))
        # The other machines' minutes are still there for work they can do.
        self.assertEqual(book.free(self.cutting, day), bank - one_day)
        mark = book.checkpoint()
        book.take_forwards(self.cutting, 60, last, last, pool)
        book.rollback(mark)
        self.assertEqual(book.free(self.cutting, last, pool),
                         self.c1.minutes_on(last) - 120)

    def test_what_no_machine_can_take_cannot_be_planned(self):
        from apps.planning.capacity import LoadBook

        Machine.objects.filter(pk=self.c1.pk).update(inserts_liner=False)
        book = LoadBook(self.plant, TODAY, TODAY + datetime.timedelta(days=5))
        pool = book.pool_for(self.cutting, self.lined_spec.bom)
        self.assertEqual(pool, ())
        _first, _last, short = book.take_forwards(self.cutting, 10, TODAY,
                                                  TODAY + datetime.timedelta(days=5), pool)
        self.assertTrue(short)

    def test_a_planned_lined_run_is_timed_on_it(self):
        from apps.planning.capacity import LoadBook, schedule_make

        book = LoadBook(self.plant, TODAY, TODAY + datetime.timedelta(days=90))
        placed = schedule_make(book, self.lined_spec.bom, Decimal("100"), self.pcs,
                               TODAY + datetime.timedelta(days=60), TODAY)
        cutting = [span for span in placed["spans"] if span["work_centre"] == self.cutting]
        self.assertEqual(len(cutting), 1)
        key = frozenset([self.c1.pk])
        self.assertEqual(sum(minutes for (pooled, _day), minutes in book._pooled.items()
                             if pooled == key), cutting[0]["minutes"])


def pooled(book, *machines):
    key = frozenset(machine.pk for machine in machines)
    return sum((minutes for (pool, _day), minutes in book._pooled.items() if pool == key),
               Decimal("0"))


class WhatIsAlreadyOnItTests(CapabilityTestCase):
    """Found by mutation: each commitment once loaded onto the whole bank."""

    def book(self):
        from apps.planning.capacity import LoadBook

        return LoadBook(self.plant, TODAY, TODAY + datetime.timedelta(days=90))

    def test_a_released_lined_run_holds_the_liner_bcs(self):
        from .orders import WorkOrder

        WorkOrder.objects.filter(pk=self.lined_run.pk).update(
            scheduled_start=TODAY, scheduled_end=TODAY + datetime.timedelta(days=6))
        self.cutting_step.refresh_from_db()
        self.assertEqual(pooled(self.book(), self.c1).quantize(Decimal("0.01")),
                         self.cutting_step.planned_minutes.quantize(Decimal("0.01")))

    def test_a_run_put_on_one_machine_holds_that_one(self):
        from .orders import WorkOrder

        plain = self.released(self.bag, self.bag_spec.bom, "600", self.pcs)
        WorkOrder.objects.filter(pk=plain.pk).update(
            scheduled_start=TODAY, scheduled_end=TODAY + datetime.timedelta(days=6))
        step = plain.operations.get(work_centre=self.cutting)
        step.machine = self.c2
        step.save()
        self.assertEqual(pooled(self.book(), self.c2).quantize(Decimal("0.01")),
                         step.planned_minutes.quantize(Decimal("0.01")))

    def test_a_drafted_one_too(self):
        from .orders import WorkOrder

        WorkOrder.objects.create(item=self.lam_bag, bom=self.lined_spec.bom,
                                 quantity_ordered=Decimal("600"), uom=self.pcs,
                                 warehouse=self.plant, scheduled_start=TODAY,
                                 scheduled_end=TODAY + datetime.timedelta(days=6))
        self.assertGreater(pooled(self.book(), self.c1), 0)

    def test_a_service_holds_its_own_machine(self):
        from .maintenance import MaintenanceJob

        MaintenanceJob.objects.create(work_centre=self.cutting, machine=self.c2,
                                      due_on=TODAY + datetime.timedelta(days=3),
                                      planned_minutes=Decimal("120"), notes="Knife")
        self.assertEqual(pooled(self.book(), self.c2), Decimal("120"))

    def test_a_run_that_cannot_be_on_time_is_placed_forward_on_it(self):
        from apps.planning.capacity import schedule_make

        book = self.book()
        placed = schedule_make(book, self.lined_spec.bom, Decimal("20000"), self.pcs, TODAY,
                               TODAY)
        self.assertTrue(placed["overloaded"])
        (span,) = [span for span in placed["spans"] if span["work_centre"] == self.cutting]
        self.assertEqual(pooled(book, self.c1), span["minutes"])


class TheEdgesTests(CapabilityTestCase):
    def test_the_limit_itself_is_in(self):
        loom = Machine(code="L-9", work_centre=self.coater, min_width_cm=Decimal("40"))
        self.assertEqual(loom.refuses({"width_cm": Decimal("40")}), "")

    def test_colours_front_and_back_together(self):
        BagSpecification.objects.filter(pk=self.lined_spec.pk).update(
            print_colours=2, print_colours_back=2)
        self.assertEqual(requirements(self.lined_spec.bom)["colours"], 4)
