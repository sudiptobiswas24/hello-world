"""
The door to manufacturing.

Everything here was built, tested and reachable only from a Python
shell: the specifications, the explosion, the work order and every
posting on it. These tests are about reachability, and about a refusal
arriving as an answer rather than as a crash — the domain behaviour has
its own tests.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .tests_orders import TODAY, RunTestCase
from .tests_woven import WovenTestCase


class ManufacturingApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_every_collection_answers(self):
        for path in (
            "tape-specifications", "fabric-specifications", "bag-specifications",
            "boms", "bom-components", "bom-byproducts", "work-centres",
            "work-orders", "material-issues", "material-issue-lines",
            "production-entries", "production-byproducts",
        ):
            response = self.client.get(f"/api/manufacturing/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_a_bom_carries_its_components_and_their_gross(self):
        response = self.client.get(f"/api/manufacturing/boms/{self.bom.pk}/")
        self.assertEqual(response.status_code, 200)
        virgin = [
            row for row in response.data["components"]
            if row["item"] == self.virgin.pk
        ][0]
        # 75 net, 3% lost on the input: 77.319588 gross.
        self.assertAlmostEqual(
            Decimal(str(virgin["gross_quantity"])), Decimal("77.319588"), places=5
        )

    def test_the_explosion_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/explosion/?quantity=1000"
        )
        self.assertEqual(response.status_code, 200)
        skus = {row["item"] for row in response.data}
        self.assertIn("PP-RAFFIA", skus)
        self.assertTrue(any(row["is_byproduct"] for row in response.data))

    def test_the_requirements_say_what_the_yard_is_short_of(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/requirements/"
            "?quantity=10000&warehouse=P"
        )
        self.assertEqual(response.status_code, 200)
        virgin = [r for r in response.data if r["item"] == "PP-RAFFIA"][0]
        # 7,731.96 kg wanted against 2,000 on the shelf.
        self.assertGreater(Decimal(str(virgin["short_by"])), Decimal("5000"))

    def test_the_material_balance_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/material-balance/?quantity=1000"
        )
        self.assertEqual(response.status_code, 200)
        regrind = [r for r in response.data if r["item"] == "REGRIND"][0]
        self.assertGreater(
            Decimal(str(regrind["net"])), Decimal("100"),
        )

    def test_an_unparseable_quantity_is_a_sentence_and_not_a_crash(self):
        response = self.client.get(
            f"/api/manufacturing/boms/{self.bom.pk}/explosion/?quantity=lots"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be a number", str(response.data))

    def test_a_run_can_be_driven_from_end_to_end(self):
        order = self.order()
        base = f"/api/manufacturing/work-orders/{order.pk}/"
        self.assertEqual(self.client.post(f"{base}release/").status_code, 200)
        order.refresh_from_db()
        self.assertIsNotNone(order.planned_unit_cost)

        issue = self.full_issue(order)
        self.assertEqual(
            self.client.post(
                f"/api/manufacturing/material-issues/{issue.pk}/post/"
            ).status_code, 200,
        )
        entry = self.produce(order, "1000", byproducts=[(self.regrind, "24.7423")])
        self.assertEqual(
            self.client.post(
                f"/api/manufacturing/production-entries/{entry.pk}/post/"
            ).status_code, 200,
        )
        self.assertEqual(self.client.post(f"{base}close/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")
        self.assertEqual(self.balance(self.wip), Decimal("0"))

    def test_the_variance_is_reachable_in_kilogrammes(self):
        order = self.order()
        order.release(TODAY)
        rows = [
            (component.item, component.quantity_required)
            for component in order.components.all()
        ]
        rows[0] = (self.virgin, rows[0][1] + Decimal("50"))
        self.issue(order, rows).post()
        response = self.client.get(
            f"/api/manufacturing/work-orders/{order.pk}/material-variance/"
        )
        self.assertEqual(response.status_code, 200)
        virgin = [r for r in response.data if r["item"] == "PP-RAFFIA"][0]
        self.assertAlmostEqual(
            Decimal(str(virgin["difference"])), Decimal("50"), places=2
        )

    def test_a_refusal_arrives_as_a_sentence(self):
        order = self.order()
        order.release(TODAY)
        order.close(TODAY)
        response = self.client.post(
            f"/api/manufacturing/work-orders/{order.pk}/release/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("only a draft order can be released", str(response.data))


class SpecificationApiTests(WovenTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_a_fabric_specification_carries_its_derived_figures(self):
        fabric = self.fabric()
        response = self.client.get(
            f"/api/manufacturing/fabric-specifications/{fabric.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(
            Decimal(str(response.data["gsm"])), Decimal("87.489"), places=3
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["metres_per_kg"])), Decimal("9.525"), places=3
        )

    def test_a_bag_specification_carries_what_a_sack_weighs(self):
        bag = self.bag()
        response = self.client.get(
            f"/api/manufacturing/bag-specifications/{bag.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["cut_length_cm"])), Decimal("105"))
        self.assertAlmostEqual(
            Decimal(str(response.data["bag_grams"])), Decimal("111.4363"), places=3
        )

    def test_a_mesh_that_cannot_reach_the_quoted_gsm_is_refused_as_a_sentence(self):
        response = self.client.post("/api/manufacturing/fabric-specifications/", {
            "code": "F-BAD", "fabric_item": self.fabric_item.pk,
            "warp_tape": self.tape().pk, "ends_per_inch": "8",
            "picks_per_inch": "8", "lay_flat_width_cm": "60",
            "weave": "tubular", "target_gsm": "87.5",
            "gsm_tolerance_percent": "5",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("70.0 GSM", str(response.data))

    def test_a_computed_bom_reports_that_it_is_computed(self):
        tape = self.tape()
        response = self.client.get(f"/api/manufacturing/boms/{tape.bom.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_computed"])


class TheAdminDoesNotOfferWhatTheModelWillRefuseTests(RunTestCase):
    """
    The models refuse to edit a closed run or a posted document. An
    admin page that lets somebody retype a closed run's quantity and
    then refuses the save is a worse answer than one that does not
    offer the box.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.admin.sites import site

        self.site = site
        self.request = None

    def readonly(self, obj):
        admin = self.site._registry[type(obj)]
        return set(admin.get_readonly_fields(self.request, obj))

    def test_a_closed_run_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        self.assertNotIn("quantity_ordered", self.readonly(order))
        order.close(TODAY)
        self.assertIn("quantity_ordered", self.readonly(order))
        self.assertIn("bom", self.readonly(order))

    def test_a_cancelled_run_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        order.cancel()
        self.assertIn("quantity_ordered", self.readonly(order))

    def test_a_posted_issue_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        document = self.issue(order, [(self.virgin, "10")])
        self.assertNotIn("warehouse", self.readonly(document))
        document.post()
        self.assertIn("warehouse", self.readonly(document))

    def test_a_posted_production_entry_is_frozen(self):
        order = self.order()
        order.release(TODAY)
        self.full_issue(order).post()
        entry = self.produce(order, "1000")
        self.assertNotIn("quantity_produced", self.readonly(entry))
        entry.post()
        self.assertIn("quantity_produced", self.readonly(entry))

    def test_a_typed_bom_stays_editable(self):
        # The guard must let the ordinary case through, or it is only a
        # refusal. This BOM was typed, not computed.
        self.assertFalse(self.bom.is_computed)
        self.assertNotIn("quantity_produced", self.readonly(self.bom))


class RoutingApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        from .routing import Routing, RoutingOperation

        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()
        self.plan = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=self.plan, sequence=10, name="Extrude",
            work_centre=self.loom, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        self.bom.routing = self.plan
        self.bom.save()

    def test_the_collections_answer(self):
        for path in ("routings", "routing-operations", "work-centres"):
            response = self.client.get(f"/api/manufacturing/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_a_routing_carries_its_operations(self):
        response = self.client.get(f"/api/manufacturing/routings/{self.plan.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["operations"]), 1)
        self.assertEqual(response.data["operations"][0]["name"], "Extrude")

    def test_a_released_run_carries_its_frozen_times(self):
        order = self.order("4000")
        order.release(TODAY)
        response = self.client.get(
            f"/api/manufacturing/work-orders/{order.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        # 90 minutes of setup then 4,000 kg at 180 an hour.
        self.assertAlmostEqual(
            Decimal(str(response.data["planned_minutes"])),
            Decimal("1423.33"), places=2,
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["operations"][0]["planned_hours"])),
            Decimal("23.72"), places=2,
        )

    def test_capacity_is_reachable(self):
        order = self.order("4000")
        order.scheduled_start = datetime.date(2026, 6, 1)
        order.scheduled_end = datetime.date(2026, 6, 7)
        order.save()
        order.release(TODAY)
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/capacity/"
            "?start=2026-06-01&end=2026-06-07"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Decimal(str(response.data["available_minutes"])), Decimal("10080.00")
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["utilisation_percent"])),
            Decimal("14.12"), places=2,
        )
        self.assertEqual(response.data["runs"], [order.number])

    def test_a_window_with_no_dates_is_a_sentence(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/capacity/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("start and end are required", str(response.data))

    def test_a_backwards_window_is_a_sentence_too(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/capacity/"
            "?start=2026-06-07&end=2026-06-01"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("runs backwards", str(response.data))


class TimeBookingApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from .orders import ManufacturingSettings, TimeBooking
        from .routing import Routing, RoutingOperation

        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = Account.objects.create(
            code="5300", name="Conversion absorbed", account_type=AccountType.EXPENSE
        )
        settings.conversion_variance_account = Account.objects.create(
            code="5400", name="Conversion variance", account_type=AccountType.EXPENSE
        )
        settings.save()
        self.loom.machine_rate_per_hour = Decimal("360")
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()
        plan = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=plan, sequence=10, name="Extrude",
            work_centre=self.loom, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        self.bom.routing = plan
        self.bom.save()
        self.booking_model = TimeBooking

    def test_the_collection_answers(self):
        response = self.client.get("/api/manufacturing/time-bookings/")
        self.assertEqual(response.status_code, 200)

    def test_time_can_be_booked_and_taken_back(self):
        order = self.order("1000")
        order.release(TODAY)
        booking = self.booking_model.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("300"),
        )
        base = f"/api/manufacturing/time-bookings/{booking.pk}/"
        self.assertEqual(self.client.post(f"{base}post/").status_code, 200)
        booking.refresh_from_db()
        # Five hours at ₹360.
        self.assertEqual(booking.posted_value, Decimal("1800.00"))
        self.assertEqual(self.client.post(f"{base}void/").status_code, 200)
        self.assertEqual(order.conversion_cost(), Decimal("0"))

    def test_a_run_carries_its_machine_time(self):
        order = self.order("1000")
        order.release(TODAY)
        response = self.client.get(f"/api/manufacturing/work-orders/{order.pk}/")
        self.assertEqual(response.status_code, 200)
        # 90 minutes of setup then 1,000 kg at 180 an hour = 423.33
        # minutes, at ₹6 a minute.
        self.assertAlmostEqual(
            Decimal(str(response.data["planned_conversion_cost"])),
            Decimal("2540.00"), places=2,
        )

    def test_an_impossible_booking_is_a_sentence(self):
        order = self.order("1000")
        order.release(TODAY)
        booking = self.booking_model.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, minutes=Decimal("60000"),
        )
        response = self.client.post(
            f"/api/manufacturing/time-bookings/{booking.pk}/post/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("really ran that long", str(response.data))


class DemandApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        from apps.core.models import Party
        from apps.sales.models import SalesOrder, SalesOrderLine

        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        customer = Party.objects.create(code="CEM", name="Deccan Cement")
        sale = SalesOrder.objects.create(
            customer=customer, order_date=datetime.date(2026, 9, 1),
            currency=self.usd,
        )
        self.line = SalesOrderLine.objects.create(
            order=sale, item=self.tape, uom=self.kg,
            quantity=Decimal("4000"), unit_price=Decimal("120"),
        )

    def test_the_planners_morning_list_is_reachable(self):
        response = self.client.get("/api/manufacturing/work-orders/uncovered/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["item"], "TAPE-1000")
        self.assertEqual(Decimal(str(response.data[0]["uncovered"])), Decimal("4000"))

    def test_a_run_against_a_line_covers_it(self):
        order = self.order("4000")
        order.sales_order_line = self.line
        order.save()
        response = self.client.get(
            f"/api/manufacturing/work-orders/{order.pk}/coverage/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["uncovered"])), Decimal("0"))
        self.assertEqual(
            self.client.get("/api/manufacturing/work-orders/uncovered/").data, []
        )

    def test_a_run_for_nobody_says_so(self):
        order = self.order("1000")
        response = self.client.get(
            f"/api/manufacturing/work-orders/{order.pk}/coverage/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not against a customer line", str(response.data))


class ShiftAndOeeApiTests(RunTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Party, PartyRole, PartyRoleAssignment
        from apps.hr.models import Employee
        from .orders import ManufacturingSettings, TimeBooking
        from .routing import Routing, RoutingOperation
        from .shifts import Downtime, DowntimeReason, Shift

        user = get_user_model().objects.create_superuser(
            username="planner", email="planner@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = Account.objects.create(
            code="5300", name="Conversion absorbed", account_type=AccountType.EXPENSE
        )
        settings.save()
        self.loom.machine_rate_per_hour = Decimal("360")
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()
        plan = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=plan, sequence=10, name="Extrude",
            work_centre=self.loom, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )
        self.bom.routing = plan
        self.bom.save()
        self.night = Shift.objects.create(
            code="C", name="Night", starts_at=datetime.time(22, 0),
            hours=Decimal("8"),
        )
        self.reason = DowntimeReason.objects.create(code="WARP", name="Warp break")

        party = Party.objects.create(code="E1", name="Ravi")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        self.ravi = Employee.objects.create(
            party=party, employee_number="E1", hire_date=datetime.date(2020, 1, 1)
        )

        order = self.order("1000")
        order.release(TODAY)
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(),
            booking_date=TODAY, shift=self.night, minutes=Decimal("400"),
            quantity_completed=Decimal("1000"),
        )
        booking.operators.set([self.ravi])
        booking.post()
        Downtime.objects.create(
            work_centre=self.loom, shift_date=TODAY, shift=self.night,
            reason=self.reason, minutes=Decimal("80"),
        )
        self.produce(order, "950", scrapped="50").post()
        self.window = f"?start={TODAY}&end={TODAY}"

    def test_the_collections_answer(self):
        for path in ("shifts", "downtime-reasons", "downtime"):
            response = self.client.get(f"/api/manufacturing/{path}/")
            self.assertEqual(response.status_code, 200, path)

    def test_a_shift_says_when_it_ends_and_whether_that_is_tomorrow(self):
        response = self.client.get(f"/api/manufacturing/shifts/{self.night.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["crosses_midnight"])
        self.assertEqual(str(response.data["ends_at"]), "06:00:00")

    def test_the_three_ratios_are_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/effectiveness/"
            f"{self.window}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(
            Decimal(str(response.data["availability"])), Decimal("0.8333"), places=4
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["performance"])), Decimal("0.8333"), places=4
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["quality"])), Decimal("0.95"), places=4
        )
        self.assertAlmostEqual(
            Decimal(str(response.data["oee"])), Decimal("0.6597"), places=4
        )

    def test_by_shift_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/by-shift/{self.window}"
        )
        self.assertEqual(response.status_code, 200)
        night = [row for row in response.data if row["shift"] == "C"][0]
        self.assertEqual(Decimal(str(night["ran_minutes"])), Decimal("400.00"))

    def test_operator_yield_is_reachable(self):
        response = self.client.get(
            f"/api/manufacturing/operator-yield/{self.window}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["operator"], "E1")
        self.assertEqual(response.data[0]["work_centre"], "EXT-1")
        self.assertEqual(Decimal(str(response.data[0]["minutes"])), Decimal("400.00"))

    def test_a_window_nobody_gave_is_a_sentence(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/effectiveness/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("start and end are required", str(response.data))

    def test_a_backwards_window_is_a_sentence_too(self):
        response = self.client.get(
            f"/api/manufacturing/work-centres/{self.loom.pk}/effectiveness/"
            f"?start={TODAY}&end=2026-01-01"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("runs backwards", str(response.data))
