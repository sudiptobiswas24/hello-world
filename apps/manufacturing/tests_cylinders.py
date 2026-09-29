"""
The laminated sack printed 2 + 2 with Ultratech's artwork, D-ULT: four
cylinders. Two are at the press; two are on order, due on day 10 and day
14. The set is there on day 14, and a run of the sack cannot start
before it however free the machines are. Cancel the day-14 cylinder and
the set waits on engraving: 21 days from today. With no lead time known,
or the artwork not approved, nobody can say when it can be printed.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import Party
from apps.inventory.models import Item

from .tests_orders import TODAY
from .tests_station_coat import CoatingTestCase
from .tooling import PrintDesign, Tool, ToolKind, ToolStatus, tooling_ready
from .woven import BagSpecification


def day(n):
    return TODAY + datetime.timedelta(days=n)


class CylinderTestCase(CoatingTestCase):
    def setUp(self):
        super().setUp()
        self.customer = Party.objects.create(code="ULT", name="Ultratech")
        self.design = PrintDesign.objects.create(
            code="D-ULT", name="Ultratech 50 kg", customer=self.customer, colours=4,
            approved_on=TODAY, engraving_lead_days=21)
        for n in (1, 2):
            Tool.objects.create(code=f"CYL-{n}", name="Cylinder", kind=ToolKind.CYLINDER,
                                design=self.design)
        self.third = Tool.objects.create(code="CYL-3", name="Cylinder", kind=ToolKind.CYLINDER,
                                         design=self.design, status=ToolStatus.ORDERED,
                                         expected_on=day(10))
        self.fourth = Tool.objects.create(code="CYL-4", name="Cylinder",
                                          kind=ToolKind.CYLINDER, design=self.design,
                                          status=ToolStatus.ORDERED, expected_on=day(14))
        # Printed 2 + 2 with the design, straight in: the ink and its
        # arithmetic are the specification's other tests' business.
        BagSpecification.objects.filter(pk=self.lam_spec.pk).update(
            print_colours=2, print_colours_back=2, print_design=self.design)


class WhenTheSetIsThereTests(CylinderTestCase):
    def test_the_last_cylinder_it_needs(self):
        self.assertEqual(self.design.readiness(TODAY),
                         (day(14), "D-ULT has 2 of 4 cylinders; the set is at the press on "
                                   f"{day(14)}"))
        self.assertEqual(tooling_ready(self.lam_spec.bom, TODAY)[0], day(14))

    def test_or_engraving_for_one_nobody_has_ordered(self):
        self.fourth.delete()
        self.assertEqual(self.design.readiness(TODAY)[0], day(21))
        PrintDesign.objects.filter(pk=self.design.pk).update(engraving_lead_days=None)
        self.design.refresh_from_db()
        ready, why = self.design.readiness(TODAY)
        self.assertIsNone(ready)
        self.assertIn("1 cylinder(s) short with none on order", why)

    def test_a_full_set_is_there_today(self):
        Tool.objects.filter(pk__in=[self.third.pk, self.fourth.pk]).update(
            status=ToolStatus.AVAILABLE)
        self.assertEqual(self.design.readiness(TODAY), (TODAY, ""))

    def test_the_orders_it_needs_not_every_order(self):
        # A fifth on order for day 30 changes nothing: two are short.
        Tool.objects.create(code="CYL-5", name="Spare", kind=ToolKind.CYLINDER,
                            design=self.design, status=ToolStatus.ORDERED,
                            expected_on=day(30))
        self.assertEqual(self.design.readiness(TODAY)[0], day(14))

    def test_an_overdue_cylinder_is_not_ready_in_the_past(self):
        Tool.objects.filter(pk=self.third.pk).update(expected_on=day(-5))
        Tool.objects.filter(pk=self.fourth.pk).update(expected_on=day(-3))
        self.assertEqual(self.design.readiness(TODAY)[0], TODAY)

    def test_artwork_nobody_approved(self):
        PrintDesign.objects.filter(pk=self.design.pk).update(approved_on=None)
        self.design.refresh_from_db()
        self.assertEqual(self.design.readiness(TODAY),
                         (None, "D-ULT's artwork is not approved"))

    def test_an_unprinted_sack_waits_for_nothing(self):
        self.assertEqual(tooling_ready(self.bag_spec.bom, TODAY), (TODAY, ""))

    def test_on_order_says_when(self):
        with self.assertRaises(Exception):
            Tool.objects.create(code="CYL-9", name="Cylinder", kind=ToolKind.CYLINDER,
                                design=self.design, status=ToolStatus.ORDERED)


class TheSackAndItsSetAgreeTests(CylinderTestCase):
    def test_one_cylinder_a_colour(self):
        ink = Item.objects.create(sku="INK", name="Ink", uom=self.kg)
        spec = BagSpecification.objects.get(pk=self.lam_spec.pk)
        spec.ink_item = ink
        spec.print_colours_back = 1
        with self.assertRaisesMessage(ValidationError, "printed in 3 colour(s) with D-ULT, "
                                                       "which is 4"):
            spec.save()
        spec.print_colours_back = 2
        spec.save()


class PlannedAfterTheSetTests(CylinderTestCase):
    def test_a_planned_run_cannot_start_before_its_cylinders(self):
        from apps.planning.models import PlanningSettings
        from apps.planning.mrp import plan
        from apps.sales.models import SalesOrder, SalesOrderLine

        PlanningSettings.objects.create(horizon_days=60, default_buy_lead_days=7,
                                        default_make_lead_days=2)
        self.lam_run.cancel()
        sale = SalesOrder.objects.create(customer=self.customer, order_date=TODAY,
                                         currency=self.usd, status="confirmed")
        SalesOrderLine.objects.create(order=sale, item=self.lam_bag, uom=self.pcs,
                                      quantity=Decimal("3000"), unit_price=Decimal("20"),
                                      delivery_date=day(10))
        run = plan(self.plant, planned_on=TODAY)
        order = run.orders.get(item=self.lam_bag)
        self.assertLess(order.release_on, day(14))
        self.assertEqual(order.can_start_on, day(14))
        self.assertIn("D-ULT has 2 of 4 cylinders", order.waits_for)
        # Ready on day 14, then the run's own days.
        self.assertEqual(order.expected_on, day(14) + datetime.timedelta(days=order.lead_days))
        self.assertIn("D-ULT has 2 of 4", order.why_late())

    def test_one_nobody_can_date_says_so(self):
        from apps.planning.models import PlanningSettings
        from apps.planning.mrp import plan
        from apps.sales.models import SalesOrder, SalesOrderLine

        PlanningSettings.objects.create(horizon_days=60, default_buy_lead_days=7,
                                        default_make_lead_days=2)
        PrintDesign.objects.filter(pk=self.design.pk).update(approved_on=None)
        self.lam_run.cancel()
        sale = SalesOrder.objects.create(customer=self.customer, order_date=TODAY,
                                         currency=self.usd, status="confirmed")
        SalesOrderLine.objects.create(order=sale, item=self.lam_bag, uom=self.pcs,
                                      quantity=Decimal("3000"), unit_price=Decimal("20"),
                                      delivery_date=day(10))
        order = plan(self.plant, planned_on=TODAY).orders.get(item=self.lam_bag)
        self.assertEqual(order.waits_for,
                         "D-ULT's artwork is not approved; it cannot be printed")


class OnTheBoardTests(CylinderTestCase):
    def at(self, date):
        return timezone.make_aware(datetime.datetime.combine(date, datetime.time(8)))

    def test_a_run_not_begun_starts_when_its_set_is_there(self):
        from .dispatch import build

        rows = [row for row in build(start_at=self.at(TODAY)) if row["order"] == self.lam_run]
        self.assertTrue(rows)
        self.assertEqual(timezone.localtime(rows[0]["start"]).date(), day(14))

    def test_one_that_cannot_be_printed_is_held(self):
        from .dispatch import build

        PrintDesign.objects.filter(pk=self.design.pk).update(approved_on=None)
        rows = [row for row in build(start_at=self.at(TODAY)) if row["order"] == self.lam_run]
        self.assertEqual({row["held"] for row in rows},
                         {"D-ULT's artwork is not approved; it cannot be printed."})

    def test_a_run_under_way_is_not_held_back(self):
        from .dispatch import build
        from .orders import TimeBooking

        step = self.lam_run.operations.order_by("sequence").first()
        TimeBooking.objects.create(work_order=self.lam_run, operation=step, booking_date=TODAY,
                                   minutes=Decimal("30"), machine=self.k1).post()
        rows = [row for row in build(start_at=self.at(TODAY)) if row["order"] == self.lam_run]
        self.assertEqual(timezone.localtime(rows[0]["start"]).date(), TODAY)
