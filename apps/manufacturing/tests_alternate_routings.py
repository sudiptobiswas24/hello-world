"""
The laminated sack is coated on K-1 and cut and stitched on the BCS
bank: its own routing. Its alternates, in order: hand stitching (150
sacks an hour on an eight-hour bench), then the contractor (five days
away).

With the BCS bank in service the plan keeps the BCS. With the bank out
of service, 3,000 sacks wanted in ten days go to hand stitching, which
can be on time. Wanted in two days, nothing can: the contractor's five
days are ready sooner than hand stitching's, so the contractor it is.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .machines import Machine
from .orders import WorkCentre, WorkOrder
from .routing import AlternateRouting, Routing, RoutingOperation, routings_for
from .tests_orders import TODAY
from .tests_station_coat import CoatingTestCase


def day(n):
    return TODAY + datetime.timedelta(days=n)


class AlternatesTestCase(CoatingTestCase):
    def setUp(self):
        super().setUp()
        self.own = self.lam_spec.bom.routing
        self.hand_bench = WorkCentre.objects.create(code="HAND", name="Hand stitching",
                                                    available_hours_per_day=Decimal("8"))
        self.hand = Routing.objects.create(code="R-LAM-HAND", name="Coat, stitch by hand")
        RoutingOperation.objects.create(routing=self.hand, sequence=10, name="Coat",
                                        work_centre=self.coater, setup_minutes=Decimal("0"),
                                        units_per_hour=Decimal("3000"), rate_uom=self.pcs)
        RoutingOperation.objects.create(routing=self.hand, sequence=20, name="Hand stitch",
                                        work_centre=self.hand_bench,
                                        setup_minutes=Decimal("0"),
                                        units_per_hour=Decimal("150"), rate_uom=self.pcs)
        self.outside = Routing.objects.create(code="R-LAM-OUT", name="Coat, contractor stitches")
        RoutingOperation.objects.create(routing=self.outside, sequence=10, name="Coat",
                                        work_centre=self.coater, setup_minutes=Decimal("0"),
                                        units_per_hour=Decimal("3000"), rate_uom=self.pcs)
        RoutingOperation.objects.create(routing=self.outside, sequence=20,
                                        name="Contract stitching", is_outside=True,
                                        outside_lead_days=5,
                                        outside_cost_per_unit=Decimal("0.80"),
                                        rate_uom=self.pcs)
        AlternateRouting.objects.create(bom=self.lam_spec.bom, routing=self.hand, priority=1)
        AlternateRouting.objects.create(bom=self.lam_spec.bom, routing=self.outside,
                                        priority=2)

    def draft(self, quantity="3000"):
        return WorkOrder.objects.create(item=self.lam_bag, bom=self.lam_spec.bom,
                                        quantity_ordered=Decimal(quantity), uom=self.pcs,
                                        warehouse=self.plant)

    def bcs_out_of_service(self):
        Machine.objects.filter(work_centre=self.cutting).update(is_active=False)


class TheWaysItIsMadeTests(AlternatesTestCase):
    def test_its_own_first_then_by_priority(self):
        self.assertEqual(routings_for(self.lam_spec.bom), [self.own, self.hand, self.outside])

    def test_a_retired_alternate_is_not_a_way(self):
        Routing.objects.filter(pk=self.hand.pk).update(is_active=False)
        self.assertEqual(routings_for(self.lam_spec.bom), [self.own, self.outside])

    def test_its_own_routing_is_not_an_alternate(self):
        with self.assertRaisesMessage(ValidationError, "own routing, not an alternate"):
            AlternateRouting.objects.create(bom=self.lam_spec.bom, routing=self.own,
                                            priority=3)


class ChosenOnTheDraftTests(AlternatesTestCase):
    def test_released_the_way_it_was_chosen(self):
        run = self.draft()
        run.choose_routing(self.hand)
        run.release(TODAY)
        self.assertEqual(run.routing, self.hand)
        self.assertEqual([op.name for op in run.operations.order_by("sequence")],
                         ["Coat", "Hand stitch"])

    def test_and_its_own_way_when_nobody_chose(self):
        run = self.draft()
        run.release(TODAY)
        self.assertEqual((run.routing, run.operations.order_by("sequence").last().name),
                         (self.own, "Cut and stitch"))

    def test_only_a_way_the_bill_is_made(self):
        stray = Routing.objects.create(code="R-OTHER", name="Other")
        run = self.draft()
        with self.assertRaisesMessage(ValidationError, "is not a way"):
            run.choose_routing(stray)
        # Nor slipped onto the draft some other way and released.
        WorkOrder.objects.filter(pk=run.pk).update(routing=stray)
        run.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "is not a way"):
            run.release(TODAY)

    def test_frozen_at_release(self):
        run = self.draft()
        run.release(TODAY)
        with self.assertRaisesMessage(ValidationError, "frozen at release"):
            run.choose_routing(self.hand)

    def test_not_a_retired_one(self):
        run = self.draft()
        Routing.objects.filter(pk=self.hand.pk).update(is_active=False)
        self.hand.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "has been retired"):
            run.choose_routing(self.hand)


class PlannedTheWayThatIsOnTimeTests(AlternatesTestCase):
    def plan_for(self, due):
        from apps.planning.models import PlanningSettings
        from apps.planning.mrp import plan
        from apps.sales.models import SalesOrder, SalesOrderLine
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        PlanningSettings.objects.create(horizon_days=60, default_buy_lead_days=7,
                                        default_make_lead_days=2)
        self.lam_run.cancel()
        customer = Party.objects.create(code="C1", name="C")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        sale = SalesOrder.objects.create(customer=customer,
                                         order_date=TODAY, currency=self.usd,
                                         status="confirmed")
        SalesOrderLine.objects.create(order=sale, item=self.lam_bag, uom=self.pcs,
                                      quantity=Decimal("3000"), unit_price=Decimal("20"),
                                      delivery_date=due)
        return plan(self.plant, planned_on=TODAY).orders.get(item=self.lam_bag)

    def test_its_own_way_while_it_can_be_on_time(self):
        self.assertEqual(self.plan_for(day(10)).routing, self.own)

    def test_the_first_alternate_that_can_when_it_cannot(self):
        self.bcs_out_of_service()
        order = self.plan_for(day(10))
        self.assertEqual((order.routing, order.is_overloaded), (self.hand, False))
        run = order.firm()
        self.assertEqual(run.routing, self.hand)

    def test_the_soonest_when_none_can(self):
        # 3,000 by hand is 20 hours, three benches' days; the contractor
        # is five days away. Wanted in two, both are late; the contractor
        # is ready first.
        self.bcs_out_of_service()
        WorkCentre.objects.filter(pk=self.hand_bench.pk).update(
            available_hours_per_day=Decimal("1"))
        order = self.plan_for(day(2))
        self.assertEqual(order.routing, self.outside)


class AlternatesApiTests(AlternatesTestCase):
    def test_listed_and_chosen(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("planner"))
        rows = client.get(f"/api/manufacturing/alternate-routings/?bom={self.lam_spec.bom_id}"
                          ).json()
        self.assertEqual(sorted(row["priority"] for row in rows), [1, 2])
        self.assertEqual(client.get(f"/api/manufacturing/alternate-routings/"
                                    f"?bom={self.bag_spec.bom_id}").json(), [])
        run = self.draft()
        response = client.post(f"/api/manufacturing/work-orders/{run.pk}/choose-routing/",
                               {"routing": self.outside.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        self.assertEqual(run.routing, self.outside)
        stray = Routing.objects.create(code="R-X", name="X")
        response = client.post(f"/api/manufacturing/work-orders/{run.pk}/choose-routing/",
                               {"routing": stray.pk}, format="json")
        self.assertEqual(response.status_code, 400)


class AStoppedBankTests(AlternatesTestCase):
    """
    Found building this: a bank whose machines were all out of service
    fell back to the bank's own hours, as one that lists none does, and
    planned work onto a stopped line.
    """

    def test_it_has_no_time(self):
        self.bcs_out_of_service()
        self.assertEqual(self.cutting.minutes_on(day(1)), Decimal("0"))
        self.assertEqual(self.cutting.minutes_available(day(1), day(7)), Decimal("0"))
        # A bank that never listed machines is still its own one machine.
        self.assertEqual(self.hand_bench.minutes_on(day(1)), Decimal("480.00"))

    def test_nor_in_the_planners_book(self):
        from apps.planning.capacity import LoadBook

        self.bcs_out_of_service()
        book = LoadBook(self.plant, TODAY, day(30))
        self.assertEqual(book.capacity_minutes(self.cutting, day(1)), Decimal("0"))
        self.assertEqual(book.capacity_minutes(self.hand_bench, day(1)), Decimal("480"))

    def test_the_board_holds_the_step_and_says_why(self):
        from django.utils import timezone

        from .dispatch import build

        self.bcs_out_of_service()
        start = timezone.make_aware(datetime.datetime.combine(TODAY, datetime.time(8)))
        held = [row["held"] for row in build(start_at=start)
                if row["order"] == self.lam_run and row["operation"].work_centre == self.cutting]
        self.assertEqual(held, ["Every machine at CONV is out of service."])

    def test_a_rough_lead_time_refuses_rather_than_divides_by_nothing(self):
        from apps.planning.leadtime import make_lead_days

        self.bcs_out_of_service()
        with self.assertRaisesMessage(ValidationError, "Every machine at CONV is out of"):
            make_lead_days(self.lam_bag, self.lam_spec.bom, Decimal("100"), self.pcs)
