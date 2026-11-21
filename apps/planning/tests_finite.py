"""
Finite machines: nothing is booked on a day that has gone.

The loom makes 60 kg an hour with an hour's setup: 1,000 kg of fabric
is 1,060 minutes. Cut to eight hours (480 minutes) a day, every day:

  1,000 kg wanted on 2 June (day 1). Today and tomorrow give 960
  minutes; the last 100 go on 3 June, a day late. It should have
  started on 31 May.
  A second fabric wanted the same day finds both days full: 380 on
  3 June, 480 on the 4th, 200 on the 5th, three days late. Booked in
  May instead, as it was, it read as on time and left June looking
  free.

With the extruder cut to an hour a day, 1,020.4 kg of tape (1,000 kg of
fabric at 2% loom waste) is 400 minutes: seven days, ready 7 June. The
fabric, wanted 4 June and a day's loom, cannot start till the tape is
there and is ready 8 June, four days late, held up by the tape.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.manufacturing.bom import BillOfMaterials, BomComponent

from .tests_mrp import PlanningTestCase


class FiniteTestCase(PlanningTestCase):
    def shift(self, centre, hours):
        centre.available_hours_per_day = Decimal(hours)
        centre.save()

    def other_fabric(self):
        other = self.fabric.__class__.objects.create(sku="FAB-12X12", name="Heavier",
                                                     uom=self.kg)
        bom = BillOfMaterials.objects.create(item=other, name="Fabric 12x12",
                                             quantity_produced=Decimal("100"), uom=self.kg,
                                             routing=self.weaving)
        BomComponent.objects.create(bom=bom, item=self.tape, quantity=Decimal("100"),
                                    uom=self.kg, waste_percent=Decimal("2"), line_number=1)
        return other


class NoTimeInThePastTests(FiniteTestCase):
    def test_a_run_that_cannot_fit_is_booked_from_today_and_says_when(self):
        self.shift(self.loom, "8")
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(1))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual((fabric.can_start_on, fabric.expected_on, fabric.release_on),
                         (self.day(0), self.day(2), self.day(-1)))
        self.assertEqual((fabric.days_behind(), fabric.is_overloaded, fabric.is_late()),
                         (1, True, True))

    def test_the_next_run_does_not_get_the_same_hours(self):
        self.shift(self.loom, "8")
        self.stock(self.tape, "5000")
        other = self.other_fabric()
        self.sell(self.fabric, "1000", self.day(1))
        self.sell(other, "1000", self.day(1))
        found = self.orders()
        self.assertEqual(found["FAB-10X10"].expected_on, self.day(2))
        self.assertEqual((found["FAB-12X12"].can_start_on, found["FAB-12X12"].expected_on,
                          found["FAB-12X12"].days_behind()),
                         (self.day(2), self.day(4), 3))

    def test_on_time_is_expected_when_wanted(self):
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(10))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual((fabric.expected_on, fabric.can_start_on, fabric.days_behind(),
                          fabric.why_late()), (self.day(10), None, 0, None))


class HeldUpByWhatFeedsItTests(FiniteTestCase):
    def setUp(self):
        super().setUp()
        self.shift(self.extruder, "1")
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        self.sell(self.fabric, "1000", self.day(3))

    def test_a_late_component_makes_the_run_above_it_late(self):
        found = self.orders()
        tape, fabric = found["TAPE-1000"], found["FAB-10X10"]
        self.assertEqual((tape.expected_on, tape.can_start_on), (self.day(6), self.day(0)))
        self.assertEqual((fabric.expected_on, fabric.days_behind(), fabric.held_up_by),
                         (self.day(7), 4, tape))
        self.assertEqual(fabric.why_late(), "TAPE-1000 is not ready until 2026-06-07")

    def test_the_late_list_names_who_is_waiting(self):
        from .mrp import late_orders

        run = self.plan()
        rows = [(row["order"].item.sku, row["days_behind"], len(row["waiting"]))
                for row in late_orders(run)]
        # Tape wanted on day 2, when the fabric should start; ready day 6.
        self.assertEqual(rows, [("FAB-10X10", 4, 1), ("TAPE-1000", 4, 0)])
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        body = client.get(f"/api/planning/runs/{run.pk}/late/").json()
        self.assertEqual([(row["item"], row["expected_on"]) for row in body],
                         [("FAB-10X10", "2026-06-08"), ("TAPE-1000", "2026-06-07")])
        self.assertEqual(body[0]["waiting"][0]["wanted_on"], "2026-06-04")


class TheEdgesTests(FiniteTestCase):
    def test_a_trial_that_did_not_fit_leaves_the_changeover_where_it_was(self):
        # 1,400 kg is 1,400 minutes and an hour's setup: 1,460, one day
        # past three 480-minute days. Without the setup it would fit in
        # three. The trial that failed must not leave the loom thinking
        # it has just run this fabric.
        # In a family, so a leaked "just ran this" would make the setup nil.
        from apps.manufacturing.changeover import SetupFamily

        SetupFamily.objects.create(item=self.fabric, work_centre=self.loom, family="LIGHT")
        self.shift(self.loom, "8")
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1400", self.day(1))
        self.assertEqual(self.orders()["FAB-10X10"].expected_on, self.day(3))

    def test_the_queue_allowance_is_on_top(self):
        self.settings.queue_days = 1
        self.settings.save()
        self.shift(self.loom, "8")
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(1))
        self.assertEqual(self.orders()["FAB-10X10"].expected_on, self.day(3))

    def test_forward_it_keeps_the_machines_days(self):
        # Monday to Friday. 2,500 kg is 2,560 minutes: five days of 480
        # and 160 on the Monday after the weekend.
        self.shift(self.loom, "8")
        self.loom.working_days = "12345"
        self.loom.save()
        self.stock(self.tape, "9000")
        self.sell(self.fabric, "2500", self.day(1))
        self.assertEqual(self.orders()["FAB-10X10"].expected_on, self.day(7))

    def test_more_than_a_year_of_work_says_so(self):
        self.shift(self.loom, "1")
        self.stock(self.tape, "700000")
        self.sell(self.fabric, "600000", self.day(5))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.expected_on, self.day(366))
        self.assertIn("more than a year of work", fabric.why_late())

    def test_an_outside_step_is_counted_forward_too(self):
        from apps.manufacturing.routing import RoutingOperation

        RoutingOperation.objects.create(routing=self.weaving, sequence=20, name="Laminate",
                                        is_outside=True, outside_lead_days=5,
                                        outside_cost_per_unit=Decimal("2"), rate_uom=self.kg)
        self.shift(self.loom, "8")
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(1))
        self.assertEqual(self.orders()["FAB-10X10"].expected_on, self.day(7))

    def test_a_late_run_wants_its_components_when_it_can_start(self):
        self.shift(self.loom, "8")
        other = self.other_fabric()
        self.sell(self.fabric, "1000", self.day(1))
        self.sell(other, "1000", self.day(1))
        run = self.plan()
        second = run.orders.get(item=other)
        from .models import PlannedDemand

        wanted = PlannedDemand.objects.get(parent=second)
        self.assertEqual(wanted.needed_by, self.day(2))

    def test_a_late_run_with_no_routing_and_a_late_buy_say_when(self):
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(1))
        # Two days to make, wanted tomorrow: started today, ready day 2.
        self.assertEqual(self.orders()["FAB-10X10"].expected_on, self.day(2))

    def test_a_late_buy_is_ready_a_lead_time_from_today(self):
        self.stock(self.regrind, "5000")
        self.sell(self.virgin, "100", self.day(3))
        virgin = self.orders()["PP-RAFFIA"]
        self.assertEqual((virgin.expected_on, virgin.days_behind()), (self.day(7), 4))

    def test_on_time_orders_are_not_on_the_late_list(self):
        from .mrp import late_orders

        self.stock(self.tape, "5000")
        self.sell(self.fabric, "1000", self.day(10))
        self.assertEqual(late_orders(self.plan()), [])


class KnockOnTests(FiniteTestCase):
    def test_a_run_both_full_and_held_up_keeps_its_own_start(self):
        # Loom full: fabric can start today, two days' work, ready day 2.
        # Tape on an hour a day: seven days, ready day 6. The fabric
        # starts when the tape is there and still takes two days: day 8.
        self.shift(self.loom, "8")
        self.shift(self.extruder, "1")
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        self.sell(self.fabric, "1000", self.day(1))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual((fabric.expected_on, fabric.held_up_by.item.sku),
                         (self.day(8), "TAPE-1000"))
        self.assertIn("no room before", fabric.why_late())

    def test_down_three_levels_deepest_first(self):
        # Polymer: seven days to buy, wanted day 1, ready day 7. Tape
        # (a day's work) then ready day 8; fabric (a day) day 9.
        self.stock(self.regrind, "5000")
        self.sell(self.fabric, "1000", self.day(3))
        found = self.orders()
        self.assertEqual((found["PP-RAFFIA"].expected_on, found["TAPE-1000"].expected_on,
                          found["FAB-10X10"].expected_on),
                         (self.day(7), self.day(8), self.day(9)))

    def test_what_comes_off_a_late_run_comes_off_when_it_does(self):
        from types import SimpleNamespace

        from .mrp import _byproducts

        late = SimpleNamespace(quantity=Decimal("1000"), item=self.tape,
                               needed_by=self.day(2), expected_on=self.day(6))
        ((item, rows),) = _byproducts(self.tape_bom, late)
        self.assertEqual((item, {row.date for row in rows}), (self.regrind, {self.day(6)}))


class FirmedRunsHoldTheirHoursTests(FiniteTestCase):
    def test_a_firmed_run_is_on_the_loom_for_the_next_plan(self):
        # Firmed for days 3 to 5: 1,060 minutes, 353.33 a day. The next
        # fabric finds 126.67 free on each of those days, all of day 2
        # and 200 of day 1: it starts on day 1, not on day 3 on hours
        # already promised.
        self.shift(self.loom, "8")
        self.stock(self.tape, "9000")
        other = self.other_fabric()
        self.sell(self.fabric, "1000", self.day(5))
        first = self.orders()["FAB-10X10"]
        self.assertEqual(first.release_on, self.day(3))
        first.firm()
        self.sell(other, "1000", self.day(5))
        self.assertEqual(self.orders()["FAB-12X12"].release_on, self.day(1))

    def test_a_draft_with_no_dates_is_counted_beside_the_plan(self):
        from apps.manufacturing.orders import WorkOrder

        from .capacity import LoadBook

        WorkOrder.objects.create(item=self.fabric, bom=self.fabric_bom,
                                 quantity_ordered=Decimal("1000"), uom=self.kg,
                                 warehouse=self.plant)
        book = LoadBook(self.plant, self.day(0), self.day(30))
        # The 2% loom waste grosses up the tape it eats, not the fabric it
        # must make: 1,000 minutes of loom and an hour's setup.
        self.assertEqual(round(book.unscheduled[self.loom.pk], 2), Decimal("1060.00"))

    def test_a_drafts_outside_step_holds_no_machine_of_ours(self):
        from apps.manufacturing.orders import WorkOrder
        from apps.manufacturing.routing import RoutingOperation

        from .capacity import LoadBook

        RoutingOperation.objects.create(routing=self.weaving, sequence=20, name="Laminate",
                                        is_outside=True, outside_lead_days=5,
                                        outside_cost_per_unit=Decimal("2"), rate_uom=self.kg)
        WorkOrder.objects.create(item=self.fabric, bom=self.fabric_bom,
                                 quantity_ordered=Decimal("1000"), uom=self.kg,
                                 warehouse=self.plant, scheduled_start=self.day(1),
                                 scheduled_end=self.day(2))
        book = LoadBook(self.plant, self.day(0), self.day(30))
        self.assertEqual((book.booked(self.loom, self.day(1)), book.booked(self.loom, self.day(2))),
                         (Decimal("530"), Decimal("530")))
