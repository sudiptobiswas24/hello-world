"""
Planning around a step that leaves the building.

The fixture weaves a thousand kilos of fabric in 1,060 minutes of loom
(60 kg an hour plus an hour's setup). Here the fabric is then sent out
to be laminated for four days. None of that is our machine time, all
of it is time the order is not ready.
"""

from decimal import Decimal

from apps.manufacturing.routing import RoutingOperation

from .leadtime import make_run_days
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class OutsideStepTestCase(PlanningTestCase):
    def laminate_outside(self, days=4):
        return RoutingOperation.objects.create(
            routing=self.weaving, sequence=20, name="Laminate",
            is_outside=True, outside_lead_days=days,
            outside_cost_per_unit=Decimal("3"), rate_uom=self.kg,
        )


class TheScheduleWaitsForTheVendorTests(OutsideStepTestCase):
    def test_the_run_starts_four_days_earlier(self):
        """
        On an eight-hour loom the weaving takes 1,060 / 480 = 2.2 days:
        three days booked, a lead of two. The vendor's four days come
        on top, because the fabric has to leave that much earlier.
        """
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        plain = self.orders()["FAB-10X10"]
        self.assertEqual(plain.lead_days, 2)
        self.laminate_outside()
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 6)

    def test_a_same_day_weave_is_not_padded_by_the_floor(self):
        """
        What an earlier draft of the test above got wrong. On a
        continuous loom the weaving fits inside a single day, and the
        plain run's lead of one is the planner's floor — a run never
        starts and finishes on the day it is wanted — not weaving time.
        With the vendor the weave lands four days out, where the floor
        has nothing to add: four, not one plus four.
        """
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 1)
        self.laminate_outside()
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 4)

    def test_the_vendor_is_never_the_bottleneck(self):
        """
        The bottleneck is the machine worth adding capacity to. A
        vendor who takes too long is a buying decision, and naming it
        here would hide the loom behind it.
        """
        self.laminate_outside(days=20)
        self.sell(self.fabric, "1000", self.day(60))
        self.assertEqual(self.orders()["FAB-10X10"].bottleneck, self.loom)

    def test_it_takes_no_hours_off_the_loom(self):
        from .capacity import LoadBook, schedule_make

        self.laminate_outside()
        book = LoadBook(self.plant, TODAY, self.day(60))
        result = schedule_make(
            book, self.fabric_bom, Decimal("1000"), self.kg,
            self.day(30), TODAY,
        )
        spans = {span["operation"].name: span for span in result["spans"]}
        self.assertTrue(spans["Laminate"]["outside"])
        self.assertIsNone(spans["Laminate"]["work_centre"])
        self.assertEqual(spans["Laminate"]["minutes"], Decimal("0"))
        self.assertEqual(
            (spans["Laminate"]["finish"] - spans["Laminate"]["start"]).days, 4
        )
        # Weaving has to finish the day the fabric leaves.
        self.assertEqual(spans["Weave"]["finish"], spans["Laminate"]["start"])


class WithoutAScheduleTheLeadTimeStillCountsItTests(OutsideStepTestCase):
    def test_the_vendors_days_are_added(self):
        plain = make_run_days(self.fabric_bom, Decimal("1000"), self.kg)
        self.laminate_outside()
        self.assertEqual(
            make_run_days(self.fabric_bom, Decimal("1000"), self.kg),
            plain + 4,
        )


class TheLeadTimeCountsTheLoomsTests(PlanningTestCase):
    """
    Missed when machines arrived and caught on the way past: without a
    capacity book the lead time divided by the bank's own hours, so a
    bank of two eight-hour looms quoted a run as though it had the
    twenty-four the bank's own field still said.
    """

    def test_a_bank_of_looms_is_what_its_looms_can_do(self):
        from apps.manufacturing.machines import Machine

        self.loom.available_hours_per_day = Decimal("24")
        self.loom.save()
        # 1,060 minutes over one 24-hour bank.
        self.assertEqual(
            make_run_days(self.fabric_bom, Decimal("1000"), self.kg),
            Decimal("1060") / Decimal("1440"),
        )
        for code in ("L-01", "L-02"):
            Machine.objects.create(
                work_centre=self.loom, code=code,
                available_hours_per_day=Decimal("8"),
            )
        # Over two eight-hour looms: 1,060 / 960.
        self.assertEqual(
            make_run_days(self.fabric_bom, Decimal("1000"), self.kg),
            Decimal("1060") / Decimal("960"),
        )
