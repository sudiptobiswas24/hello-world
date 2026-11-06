"""
Planning books the changeover a run will actually pay.

The fixture's loom weaves 60 kg an hour with a flat hour of setup, so
1,000 kg is 1,060 minutes. Natural fabric behind black needs a
ten-hour wash-down; natural behind natural needs none. A planned run
joins the end of the loom's queue, so what it pays depends on what is
there.
"""

from decimal import Decimal

from apps.manufacturing.bom import BillOfMaterials, BomComponent
from apps.manufacturing.changeover import ChangeoverRule, SetupFamily
from apps.manufacturing.orders import TimeBooking, WorkOrder

from .capacity import LoadBook
from .leadtime import make_run_days
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class ChangeoverPlanningTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.black = self.fabric.__class__.objects.create(
            sku="FAB-BLACK", name="Black fabric", uom=self.kg
        )
        self.black_bom = BillOfMaterials.objects.create(
            item=self.black, name="Black", quantity_produced=Decimal("100"),
            uom=self.kg, routing=self.weaving,
        )
        BomComponent.objects.create(
            bom=self.black_bom, item=self.tape, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )
        self.stock(self.tape, "5000")
        SetupFamily.objects.create(item=self.fabric, work_centre=self.loom, family="NATURAL")
        SetupFamily.objects.create(item=self.black, work_centre=self.loom, family="BLACK")
        ChangeoverRule.objects.create(
            work_centre=self.loom, from_family="BLACK", to_family="NATURAL",
            minutes=Decimal("600"),
        )
        self.weave = self.weaving.operations.get()

    def black_waiting(self):
        order = WorkOrder.objects.create(
            item=self.black, bom=self.black_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant,
        )
        order.release(TODAY)
        return order

    def book(self):
        return LoadBook(self.plant, TODAY, self.day(90))


class TheLoadBookTests(ChangeoverPlanningTestCase):
    def test_nothing_known_is_the_flat_setup(self):
        self.assertEqual(
            self.book().run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg),
            Decimal("1060"),
        )

    def test_behind_a_black_run_it_pays_the_wash_down(self):
        self.black_waiting()
        self.assertEqual(
            self.book().run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg),
            Decimal("1600"),
        )

    def test_the_next_natural_run_follows_the_first_and_pays_nothing(self):
        self.black_waiting()
        book = self.book()
        book.run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg)
        self.assertEqual(
            book.run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg),
            Decimal("1000"),
        )

    def test_it_follows_the_last_of_the_queue_not_the_first(self):
        black = self.black_waiting()
        WorkOrder.objects.filter(pk=black.pk).update(
            scheduled_start=self.day(2), scheduled_end=self.day(3))
        natural = WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom, quantity_ordered=Decimal("100"),
            uom=self.kg, warehouse=self.plant,
            scheduled_start=self.day(4), scheduled_end=self.day(5),
        )
        natural.release(TODAY)
        self.assertEqual(
            self.book().run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg),
            Decimal("1000"),
        )

    def test_with_nothing_waiting_it_follows_what_the_loom_last_ran(self):
        order = self.black_waiting()
        TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("30"),
        ).post()
        self.assertEqual(
            self.book().run_minutes(self.weave, self.fabric, Decimal("1000"), self.kg),
            Decimal("1600"),
        )


class TheLeadTimeEstimateAgreesTests(ChangeoverPlanningTestCase):
    def test_it_reads_the_same_changeover(self):
        self.assertEqual(make_run_days(self.fabric_bom, Decimal("1000"), self.kg),
                         Decimal("1060") / Decimal("1440"))
        self.black_waiting()
        self.assertEqual(make_run_days(self.fabric_bom, Decimal("1000"), self.kg),
                         Decimal("1600") / Decimal("1440"))


class ThePlanTests(ChangeoverPlanningTestCase):
    def test_a_run_planned_behind_black_is_released_earlier(self):
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        clean = self.plan().orders.get(item=self.fabric)

        self.black_waiting()
        behind = self.plan().orders.get(item=self.fabric)
        # 1,060 minutes is three eight-hour days; 1,600 is four.
        self.assertLess(behind.release_on, clean.release_on)
