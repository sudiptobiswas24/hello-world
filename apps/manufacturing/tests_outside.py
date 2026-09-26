"""
A step a vendor does, in the middle of a run.

The fixture's tape run goes out to be coated and comes back: extrude
here, coat outside, and nothing in between is an item. Hand-checked
throughout: 1,000 kg of tape at a standard coating charge of 2.00 a
kilo is 2,000 of vendor work planned, and it is away for five days.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

from .orders import ManufacturingSettings, TimeBooking, WorkOrderStatus
from .outside import OutsideMovement
from .routing import Routing, RoutingOperation
from .tests_orders import TODAY, RunTestCase


class OutsideTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.grni = Account.objects.create(
            code="2150", name="GRNI", account_type=AccountType.LIABILITY
        )
        self.absorbed = Account.objects.create(
            code="5300", name="Conversion absorbed",
            account_type=AccountType.EXPENSE,
        )
        self.conversion_variance = Account.objects.create(
            code="5110", name="Conversion variance",
            account_type=AccountType.EXPENSE,
        )
        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = self.absorbed
        settings.conversion_variance_account = self.conversion_variance
        settings.save()
        self.loom.capacity_per_hour = Decimal("180")
        self.loom.capacity_uom = self.kg
        self.loom.save()
        self.routing = Routing.objects.create(code="R-COAT", name="Coated tape")
        self.extrude = RoutingOperation.objects.create(
            routing=self.routing, sequence=10, name="Extrude",
            work_centre=self.loom, units_per_hour=Decimal("180"),
            rate_uom=self.kg,
        )
        self.coat = RoutingOperation.objects.create(
            routing=self.routing, sequence=20, name="Coat", is_outside=True,
            outside_lead_days=5, outside_cost_per_unit=Decimal("2"),
            rate_uom=self.kg,
        )
        self.bom.routing = self.routing
        self.bom.save()

    def released(self, quantity="1000"):
        order = self.order(quantity)
        order.release(TODAY)
        return order

    def back(self, order, quantity, value, is_return=False):
        movement = OutsideMovement.objects.create(
            operation=order.operations.get(is_outside=True),
            movement_date=TODAY, is_return=is_return,
            quantity=Decimal(quantity), value=Decimal(value),
            credit_account=self.grni,
        )
        movement.post()
        return movement

    def balance(self, account):
        total = Decimal("0")
        for row in JournalLine.objects.filter(
            account=account, entry__posted=True
        ):
            total += row.debit - row.credit
        return total


class AStepIsOursOrAVendorsTests(OutsideTestCase):
    def refused(self, **kwargs):
        kwargs.setdefault("routing", self.routing)
        kwargs.setdefault("sequence", 30)
        kwargs.setdefault("name", "Bad")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoutingOperation.objects.create(**kwargs)

    def test_an_inside_step_must_name_a_machine(self):
        self.refused(units_per_hour=Decimal("10"), rate_uom=self.kg)

    def test_an_outside_step_names_none(self):
        self.refused(is_outside=True, work_centre=self.loom)

    def test_an_outside_step_has_no_rate_on_our_machines(self):
        self.refused(is_outside=True, units_per_hour=Decimal("10"),
                     rate_uom=self.kg)

    def test_an_outside_step_has_no_setup_of_ours(self):
        self.refused(is_outside=True, setup_minutes=Decimal("30"))

    def test_an_inside_step_has_no_vendor_terms(self):
        self.refused(work_centre=self.loom, outside_lead_days=3)
        self.refused(work_centre=self.loom, outside_cost_per_unit=Decimal("1"),
                     rate_uom=self.kg)

    def test_a_charge_says_what_it_counts(self):
        self.refused(is_outside=True, outside_cost_per_unit=Decimal("1"))


class WhatAnOutsideStepCostsAndTakesTests(OutsideTestCase):
    def test_it_takes_none_of_our_minutes(self):
        self.assertEqual(
            self.coat.minutes_for(Decimal("1000"), self.kg), Decimal("0")
        )

    def test_asking_it_for_a_rate_is_refused_rather_than_answered(self):
        with self.assertRaisesMessage(ValidationError, "done by a vendor"):
            self.coat.rate(self.kg)

    def test_its_charge_is_per_unit_it_counts(self):
        self.assertEqual(
            self.coat.outside_charge_for(Decimal("1000"), self.kg),
            Decimal("2000"),
        )

    def test_the_charge_converts_between_units_of_one_kind(self):
        tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        # 1.5 t is 1,500 kg at 2.00.
        self.assertEqual(
            self.coat.outside_charge_for(Decimal("1.5"), tonne),
            Decimal("3000"),
        )

    def test_a_charge_in_another_kind_of_unit_is_refused(self):
        pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        with self.assertRaisesMessage(ValidationError, "not the same kind"):
            self.coat.outside_charge_for(Decimal("10"), pcs)

    def test_an_unpriced_vendor_plans_at_nothing_rather_than_refusing(self):
        self.coat.outside_cost_per_unit = None
        self.coat.save()
        self.assertEqual(
            self.coat.outside_charge_for(Decimal("1000"), self.kg),
            Decimal("0"),
        )

    def test_an_inside_step_charges_nothing(self):
        self.assertEqual(
            self.extrude.outside_charge_for(Decimal("1000"), self.kg),
            Decimal("0"),
        )


class ReleaseFreezesTheVendorsTermsTests(OutsideTestCase):
    def test_the_outside_step_is_frozen_with_no_machine_and_no_minutes(self):
        order = self.released()
        step = order.operations.get(is_outside=True)
        self.assertIsNone(step.work_centre)
        self.assertIsNone(step.units_per_hour)
        self.assertEqual(step.planned_minutes, Decimal("0"))
        self.assertEqual(step.outside_lead_days, 5)
        self.assertEqual(step.planned_outside_cost, Decimal("2000"))

    def test_the_charge_is_for_what_is_started(self):
        """
        The vendor coats the tape that will be rejected too. At twenty
        per cent reject a thousand delivered is 1,250 coated.
        """
        self.bom.expected_reject_percent = Decimal("20")
        self.bom.save()
        order = self.released()
        self.assertEqual(
            order.operations.get(is_outside=True).planned_outside_cost,
            Decimal("2500"),
        )

    def test_a_revised_vendor_price_does_not_move_a_live_run(self):
        order = self.released()
        self.coat.outside_cost_per_unit = Decimal("9")
        self.coat.save()
        self.assertEqual(order.planned_outside_cost(), Decimal("2000"))

    def test_the_plan_carries_it_apart_from_our_machine_time(self):
        from .bom import planned_cost

        plan = planned_cost(self.bom, Decimal("1000"), self.plant, self.kg)
        self.assertEqual(plan.outside, Decimal("2000"))
        self.assertEqual(
            plan.net, plan.materials + plan.conversion + plan.outside
            - plan.byproducts,
        )

    def test_its_terms_are_frozen_like_everything_else(self):
        order = self.released()
        step = order.operations.get(is_outside=True)
        step.outside_lead_days = 1
        with self.assertRaisesMessage(ValidationError, "frozen"):
            step.save()


class NobodyBooksOurTimeAgainstAVendorTests(OutsideTestCase):
    def test_a_time_booking_is_refused_when_it_is_saved(self):
        order = self.released()
        with self.assertRaisesMessage(ValidationError, "done by a vendor"):
            TimeBooking.objects.create(
                work_order=order, operation=order.operations.get(is_outside=True),
                booking_date=TODAY, minutes=Decimal("60"),
            )

    def test_nor_is_a_loom_assigned_to_it(self):
        from .machines import Machine

        order = self.released()
        step = order.operations.get(is_outside=True)
        step.machine = Machine.objects.create(work_centre=self.loom, code="L-1")
        with self.assertRaises(Exception):
            step.save()


class TheVendorsWorkGoesIntoTheRunTests(OutsideTestCase):
    def test_work_coming_back_is_debited_to_work_in_progress(self):
        order = self.released()
        self.back(order, "1000", "2000")
        self.assertEqual(self.balance(self.wip), Decimal("2000"))
        self.assertEqual(self.balance(self.grni), Decimal("-2000"))
        self.assertEqual(order.outside_cost(), Decimal("2000"))
        self.assertEqual(order.unaccounted(), Decimal("2000"))

    def test_work_sent_back_takes_it_out_again(self):
        order = self.released()
        self.back(order, "1000", "2000")
        self.back(order, "400", "800", is_return=True)
        self.assertEqual(self.balance(self.wip), Decimal("1200"))
        self.assertEqual(self.balance(self.grni), Decimal("-1200"))
        step = order.operations.get(is_outside=True)
        self.assertEqual(step.quantity_back(), Decimal("600"))

    def test_more_cannot_go_back_than_came(self):
        order = self.released()
        self.back(order, "300", "600")
        with self.assertRaisesMessage(ValidationError, "never came"):
            self.back(order, "301", "602", is_return=True)

    def test_the_vendor_cannot_have_coated_more_than_the_run_held(self):
        order = self.released()
        # 1,000 ordered with the fixture's ten per cent allowance.
        with self.assertRaisesMessage(ValidationError, "at most"):
            self.back(order, "1101", "2202")

    def test_a_step_of_ours_takes_no_vendor_charge(self):
        order = self.released()
        with self.assertRaisesMessage(ValidationError, "our own machines"):
            OutsideMovement.objects.create(
                operation=order.operations.get(is_outside=False),
                movement_date=TODAY, quantity=Decimal("1"),
                value=Decimal("1"), credit_account=self.grni,
            )

    def test_nothing_moves_on_a_closed_run(self):
        order = self.released()
        order.close(TODAY)
        with self.assertRaisesMessage(ValidationError, "closed"):
            self.back(order, "100", "200")


class AMistakenMovementIsVoidedTests(OutsideTestCase):
    def test_voiding_takes_the_value_back_out(self):
        order = self.released()
        movement = self.back(order, "1000", "2000")
        movement.void(TODAY)
        self.assertEqual(self.balance(self.wip), Decimal("0"))
        self.assertEqual(order.outside_cost(), Decimal("0"))
        self.assertEqual(
            order.operations.get(is_outside=True).quantity_back(), Decimal("0")
        )

    def test_a_receipt_partly_sent_back_cannot_be_voided_first(self):
        order = self.released()
        movement = self.back(order, "1000", "2000")
        self.back(order, "400", "800", is_return=True)
        with self.assertRaisesMessage(ValidationError, "Void that return first"):
            movement.void(TODAY)

    def test_two_receipts_and_a_return_leave_room_to_void_one(self):
        """
        Two of something, deliberately: 500 and 500 back, 400 returned,
        600 net. Voiding one 500 leaves 100 — allowed.
        """
        order = self.released()
        first = self.back(order, "500", "1000")
        self.back(order, "500", "1000")
        self.back(order, "400", "800", is_return=True)
        first.void(TODAY)
        self.assertEqual(
            order.operations.get(is_outside=True).quantity_back(), Decimal("100")
        )

    def test_a_posted_movement_is_not_edited(self):
        order = self.released()
        movement = self.back(order, "1000", "2000")
        movement.value = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "once it is posted"):
            movement.save()


class TheNextStepWaitsForTheVendorTests(OutsideTestCase):
    """
    A step after the vendor's can only have made what came back. The
    fixture's routing gets a third step, ours, after the coating.
    """

    def setUp(self):
        super().setUp()
        RoutingOperation.objects.create(
            routing=self.routing, sequence=30, name="Wind", work_centre=self.loom,
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )

    def test_winding_cannot_outrun_what_the_vendor_returned(self):
        order = self.released()
        self.back(order, "300", "600")
        wind = order.operations.get(sequence=30)
        with self.assertRaisesMessage(ValidationError, "has fed it"):
            TimeBooking.objects.create(
                work_order=order, operation=wind, booking_date=TODAY,
                minutes=Decimal("110"), quantity_completed=Decimal("301"),
            ).post()

    def test_it_may_wind_what_came_back(self):
        order = self.released()
        self.back(order, "300", "600")
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(sequence=30),
            booking_date=TODAY, minutes=Decimal("100"),
            quantity_completed=Decimal("300"),
        )
        booking.post()
        self.assertTrue(booking.posted)


class AtCloseTheVendorsOverrunIsItsOwnTests(OutsideTestCase):
    """
    Material issued and output booked exactly to plan, so the only
    thing that can differ is the vendor. The coating went up from 2.00
    to 2.30 a kilo on this run: 300 over, and it must land in
    conversion variance, not in the material remainder where somebody
    looking for missing polymer would find a laminator's price rise.
    """

    def run_with_vendor_at(self, value):
        from .orders import ProductionEntry

        order = self.released()
        for component in order.components.all():
            self.issue(order, [(component.item, component.quantity_required)])
        for document in order.issues.all():
            document.post()
        self.back(order, "1000", value)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("1000"), uom=self.kg,
        )
        entry.post()
        return order

    def test_a_vendor_at_the_standard_leaves_no_vendor_variance(self):
        order = self.run_with_vendor_at("2000")
        self.assertEqual(order.outside_earned(), Decimal("2000"))
        self.assertEqual(order.outside_variance(), Decimal("0"))

    def test_half_a_run_has_earned_half_the_vendors_work(self):
        """
        The case a run-to-plan fixture cannot see: there, output equals
        what was started and "earned per unit made" equals "planned".
        Five hundred of a thousand made is 1,000 of the 2,000 earned,
        whatever the vendor has invoiced so far.
        """
        from .orders import ProductionEntry

        order = self.released()
        self.back(order, "1000", "2000")
        ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("500"), uom=self.kg,
        ).post()
        self.assertEqual(order.outside_earned(), Decimal("1000"))
        self.assertEqual(order.outside_variance(), Decimal("1000"))

    def test_a_dearer_vendor_lands_in_conversion_variance(self):
        order = self.run_with_vendor_at("2300")
        self.assertEqual(order.outside_variance(), Decimal("300"))
        order.close(TODAY)
        self.assertEqual(
            self.balance(self.conversion_variance), Decimal("300.00")
        )
        self.assertEqual(order.wip_balance(), Decimal("0"))


class ACancelledRunLeavesNothingBehindTests(OutsideTestCase):
    def test_vendor_work_stops_a_cancel(self):
        order = self.released()
        self.back(order, "100", "200")
        with self.assertRaisesMessage(ValidationError, "vendors' work"):
            order.cancel()

    def test_machine_time_stops_a_cancel_too(self):
        """
        The hole this closed on the way past. A run with nothing but
        hours booked could be cancelled, and its hours sat in work in
        progress against an order nobody would open again.
        """
        order = self.released()
        booking = TimeBooking.objects.create(
            work_order=order, operation=order.operations.get(sequence=10),
            booking_date=TODAY, minutes=Decimal("60"),
        )
        booking.post()
        with self.assertRaisesMessage(ValidationError, "machine time"):
            order.cancel()
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.RELEASED)

    def test_a_voided_movement_does_not_stop_it(self):
        order = self.released()
        self.back(order, "100", "200").void(TODAY)
        order.cancel()
        self.assertEqual(order.status, WorkOrderStatus.CANCELLED)


class TheStandardIncludesTheVendorTests(OutsideTestCase):
    def test_the_rolled_conversion_carries_the_vendors_charge(self):
        from .costing import CostVersion

        for item in (self.virgin, self.filler, self.colour):
            item.standard_cost = Decimal("100")
            item.save()
        version = CostVersion.objects.create(
            code="STD-1", name="Standard",
            effective_from=datetime.date(2026, 1, 1),
        )
        version.roll_up([self.tape])
        row = version.costs.get(item=self.tape)
        # The batch is 100 kg: 2.00 a kilo of coating is 200 a batch,
        # 2.00 a kilo. The loom's hourly rates are nought, so the
        # vendor is all of the conversion.
        self.assertEqual(row.conversion, Decimal("2.000000"))
