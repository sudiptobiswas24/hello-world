"""
Starting more than you deliver.

A component's `waste_percent` is material fed in and lost on the way.
This is a different loss: finished units made and then rejected. The
bill knew the first and not the second, so a run for a thousand sacks
issued material for a thousand, booked a thousand, delivered nine
hundred and seventy, and the thirty were somebody else's problem.

The arithmetic throughout: at twenty per cent reject, delivering a
hundred means starting a hundred and twenty-five, because the
percentage is of what comes off the machine. Not a hundred and twenty.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item

from .bom import (
    BillOfMaterials,
    BomComponent,
    explode,
    net_requirements,
    planned_cost,
)
from .orders import ProductionEntry, WorkOrder, WorkOrderStatus
from .tests_orders import TODAY, RunTestCase


class YieldTestCase(RunTestCase):
    """A deliberately plain bill, so the arithmetic is checkable by eye."""

    def setUp(self):
        super().setUp()
        self.widget = Item.objects.create(
            sku="WIDGET", name="Widget", uom=self.kg
        )
        self.widget_bom = BillOfMaterials.objects.create(
            item=self.widget, name="Widget", quantity_produced=Decimal("100"),
            uom=self.kg,
        )
        BomComponent.objects.create(
            bom=self.widget_bom, item=self.virgin, quantity=Decimal("10"),
            uom=self.kg, waste_percent=Decimal("0"), line_number=1,
        )

    def reject(self, percent):
        self.widget_bom.expected_reject_percent = Decimal(percent)
        self.widget_bom.save()
        return self.widget_bom

    def run_for(self, quantity="100"):
        return WorkOrder.objects.create(
            item=self.widget, bom=self.widget_bom,
            quantity_ordered=Decimal(quantity), uom=self.kg,
            warehouse=self.plant,
        )


class TheArithmeticIsOfTheGrossTests(YieldTestCase):
    def test_no_reject_starts_what_it_delivers(self):
        self.assertEqual(
            self.widget_bom.start_for(Decimal("100")), Decimal("100")
        )

    def test_twenty_per_cent_reject_starts_a_hundred_and_twenty_five(self):
        """
        And not a hundred and twenty. The percentage is of what comes
        off the machine, so it divides — the same reading a
        component's waste already takes on the input.
        """
        self.reject("20")
        self.assertEqual(
            self.widget_bom.start_for(Decimal("100")), Decimal("125")
        )

    def test_the_two_readings_diverge_at_small_percentages_too(self):
        """
        Three per cent: 103.0928 started, not 103. A tenth of a per
        cent is a rounding error on one order and a shortfall on every
        order.
        """
        self.reject("3")
        started = self.widget_bom.start_for(Decimal("1000"))
        self.assertEqual(started.quantize(Decimal("0.0001")),
                         Decimal("1030.9278"))
        self.assertNotEqual(started.quantize(Decimal("1")), Decimal("1030"))

    def test_everything_rejected_is_refused_by_the_database(self):
        """A hundred per cent is a division by zero wearing a hat."""
        with self.assertRaises(Exception):
            self.reject("100")


class TheExplosionIsOfWhatMustBeStartedTests(YieldTestCase):
    def test_material_covers_the_units_that_will_be_rejected(self):
        self.reject("20")
        rows = net_requirements(self.widget_bom, Decimal("100"), self.kg)
        item, quantity, _uom = rows[0]
        self.assertEqual(item, self.virgin)
        # 125 started is 1.25 batches, and a batch takes 10 kg.
        self.assertEqual(quantity, Decimal("12.5"))

    def test_without_a_reject_rate_nothing_moves(self):
        rows = net_requirements(self.widget_bom, Decimal("100"), self.kg)
        self.assertEqual(rows[0][1], Decimal("10"))

    def test_the_explosion_says_the_same(self):
        self.reject("20")
        rows = explode(self.widget_bom, Decimal("100"), self.kg)
        self.assertEqual(rows[0].quantity, Decimal("12.5"))

    def test_the_plan_costs_the_material_that_will_be_spoiled(self):
        """The polymer wasted on a rejected sack was still bought."""
        plain = planned_cost(
            self.widget_bom, Decimal("100"), self.plant, self.kg
        )
        self.reject("20")
        with_reject = planned_cost(
            self.widget_bom, Decimal("100"), self.plant, self.kg
        )
        self.assertEqual(plain.materials, Decimal("1000"))
        self.assertEqual(with_reject.materials, Decimal("1250"))


class ReleaseFreezesWhatWillBeStartedTests(YieldTestCase):
    def test_the_started_quantity_is_frozen_on_the_order(self):
        self.reject("20")
        order = self.run_for("100")
        self.assertIsNone(order.quantity_to_start)
        self.assertEqual(order.started_quantity(), Decimal("100"))
        order.release(TODAY)
        self.assertEqual(order.quantity_to_start, Decimal("125.0000"))
        self.assertEqual(order.started_quantity(), Decimal("125.0000"))

    def test_the_requirements_are_for_the_started_quantity(self):
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        self.assertEqual(
            order.components.get().quantity_required, Decimal("12.5")
        )

    def test_a_rate_revised_after_release_does_not_move_a_live_run(self):
        """
        The trap this project keeps falling into: a number recomputed
        from a source that has since changed. The run holds material
        for a hundred and twenty-five and must go on reading against
        it however the bill is revised behind it.
        """
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        self.reject("50")
        order.refresh_from_db()
        self.assertEqual(order.started_quantity(), Decimal("125.0000"))
        self.assertEqual(
            order.components.get().quantity_required, Decimal("12.5")
        )

    def test_an_order_released_before_any_of_this_reads_as_it_did(self):
        """
        `quantity_to_start` is null on every run released before the
        column existed, and those runs must not suddenly read as
        having planned to make nothing.
        """
        order = self.run_for("100")
        order.release(TODAY)
        WorkOrder.objects.filter(pk=order.pk).update(quantity_to_start=None)
        order.refresh_from_db()
        self.assertEqual(order.started_quantity(), Decimal("100"))


class TheRunMayBookWhatItWasPlannedToMakeTests(YieldTestCase):
    def test_the_ceiling_is_the_started_quantity(self):
        """
        Not the ordered one, and the over-production allowance still
        sits on top of it. A run for a hundred at twenty per cent
        reject makes a hundred and twenty-five — good and spoiled
        together — and the fixture's ten per cent allowance takes it
        to 137.5, against 110 before.
        """
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        self.assertEqual(order.over_production_percent, Decimal("10"))
        self.assertEqual(order.maximum_output(), Decimal("137.50000"))

    def test_with_no_allowance_it_is_exactly_the_started_quantity(self):
        """
        The allowance out of the way, so the number under it is
        visible. A hundred and twenty-five, not a hundred.
        """
        self.reject("20")
        order = self.run_for("100")
        order.over_production_percent = Decimal("0")
        order.save()
        order.release(TODAY)
        self.assertEqual(order.maximum_output(), Decimal("125.00000"))

    def test_the_last_booking_of_a_run_that_went_to_plan_is_not_refused(self):
        """
        What the old ceiling actually broke. With no allowance and no
        yield the ceiling was a hundred, so the twenty-five spoiled
        units of a run that went exactly to plan had nowhere to go.
        """
        self.reject("20")
        order = self.run_for("100")
        order.over_production_percent = Decimal("0")
        order.save()
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        entry.post()
        self.assertEqual(order.quantity_scrapped(), Decimal("25"))

    def test_booking_the_whole_plan_is_allowed(self):
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        entry.post()
        self.assertEqual(order.quantity_produced(), Decimal("100"))

    def test_past_the_plan_and_its_allowance_is_still_refused(self):
        """
        The ceiling moved; it did not come off. 138 is past 137.5.
        """
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("113"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        with self.assertRaisesMessage(ValidationError, "allows"):
            entry.post()


class BackflushDrawsForTheSpoiledUnitsTests(YieldTestCase):
    def test_a_backflush_draws_against_the_started_quantity(self):
        """
        The frozen requirements are for what must be started, so the
        share a backflush takes must be of that too. Against the
        ordered quantity a run that went exactly to plan would draw
        a hundred and twenty-five per cent of its own material.
        """
        self.reject("20")
        self.widget_bom.backflush = True
        self.widget_bom.save()
        order = self.run_for("100")
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        entry.post()
        issue = entry.backflush_issue
        self.assertIsNotNone(issue)
        # 12.5 kg was frozen onto the run for 125 units; 125 units
        # came off, so all of it and no more.
        self.assertEqual(issue.lines.get().quantity, Decimal("12.5"))

    def test_half_the_run_draws_half_of_it(self):
        self.reject("20")
        self.widget_bom.backflush = True
        self.widget_bom.save()
        order = self.run_for("100")
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("50"),
            quantity_scrapped=Decimal("12.5"), uom=self.kg,
        )
        entry.post()
        self.assertEqual(
            entry.backflush_issue.lines.get().quantity, Decimal("6.25")
        )


class WorkInProgressStillClearsTests(YieldTestCase):
    """
    The half of this that would have been silent.

    Output and scrap are both received at the run's planned unit cost.
    If that cost were the material divided by what the run DELIVERS,
    a run that went exactly to plan would credit work in progress with
    a hundred and twenty-five units' worth of a hundred units' cost —
    twenty-five per cent more than ever went in — and close on a
    favourable variance it never earned. Every run. For ever.
    """

    def plan_and_run(self, reject):
        self.reject(reject)
        order = self.run_for("100")
        order.release(TODAY)
        started = order.started_quantity()
        self.issue(order, [(self.virgin, order.components.get().quantity_required)])
        for document in order.issues.all():
            document.post()
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=started - Decimal("100"), uom=self.kg,
        )
        entry.post()
        return order

    def test_a_run_that_goes_exactly_to_plan_leaves_nothing_behind(self):
        order = self.plan_and_run("20")
        self.assertEqual(order.wip_balance(), Decimal("0"))

    def test_the_same_with_no_reject_rate_at_all(self):
        """The control. If this moved, the change broke the ordinary case."""
        self.reject("0")
        order = self.run_for("100")
        order.release(TODAY)
        self.issue(order, [(self.virgin, Decimal("10"))])
        for document in order.issues.all():
            document.post()
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"), uom=self.kg,
        )
        entry.post()
        self.assertEqual(order.wip_balance(), Decimal("0"))

    def test_the_unit_cost_is_per_unit_off_the_machine(self):
        """
        Which is the same basis the standard cost roll-up uses, so the
        two agree. Expected spoilage lands in the scrap account like
        any other scrap rather than being buried in the price of the
        sacks that passed.
        """
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        # 1,250 of polymer over 125 units off the machine.
        self.assertEqual(order.planned_unit_cost, Decimal("10.000000"))


class MachineTimeIsEarnedOnWhatCameOffTests(YieldTestCase):
    """
    The planned conversion covers the spoiled units, because the loom
    ran to make them. Earned against the delivered quantity, a run that
    went exactly to plan would earn a hundred and twenty-five per cent
    of its time and report a favourable variance nobody achieved.

    Hand-checked: 125 kg off at 100 kg an hour, no setup, is 75
    minutes. At 60 an hour that is 75.00 of conversion planned.
    """

    def setUp(self):
        super().setUp()
        from .routing import Routing, RoutingOperation

        from apps.accounting.models import Account, AccountType

        from .orders import ManufacturingSettings

        settings = ManufacturingSettings.get()
        settings.conversion_absorbed_account = Account.objects.create(
            code="5300", name="Conversion absorbed",
            account_type=AccountType.EXPENSE,
        )
        settings.save()
        self.loom.labour_rate_per_hour = Decimal("60")
        self.loom.save()
        routing = Routing.objects.create(code="R-WID", name="Widget")
        RoutingOperation.objects.create(
            routing=routing, sequence=10, name="Make", work_centre=self.loom,
            setup_minutes=Decimal("0"), units_per_hour=Decimal("100"),
            rate_uom=self.kg,
        )
        self.widget_bom.routing = routing
        self.widget_bom.save()

    def run_to_plan(self):
        from .orders import TimeBooking

        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        operation = order.operations.get()
        self.assertEqual(operation.planned_minutes, Decimal("75.00"))
        self.assertEqual(order.planned_conversion_cost, Decimal("75"))
        booking = TimeBooking.objects.create(
            work_order=order, operation=operation, booking_date=TODAY,
            minutes=Decimal("75"),
        )
        booking.post()
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        entry.post()
        return order

    def test_a_run_to_plan_earns_exactly_its_planned_conversion(self):
        order = self.run_to_plan()
        self.assertEqual(order.conversion_earned(), Decimal("75"))
        self.assertEqual(order.conversion_variance(), Decimal("0"))

    def test_and_took_exactly_its_planned_minutes(self):
        order = self.run_to_plan()
        self.assertEqual(order.time_variance_minutes(), Decimal("0"))


class AByproductShareIsOfTheStartedRunTests(YieldTestCase):
    """
    A by-product valued at a share of the run's planned material cost
    is valued per kilo of what the run was EXPECTED to throw off, and
    it throws off trim for the rejected units too.

    Hand-checked: 125 started is 1.25 batches, so 2.5 kg of regrind is
    expected. Ten per cent of 1,250 planned material is 125, which is
    50 a kilo. Against the delivered hundred the expectation is 2 kg
    and the rate 62.50 — regrind overvalued by a quarter.
    """

    def test_the_rate_is_per_kilo_of_expected_trim_off_the_started_run(self):
        from .bom import BomByproduct, ByproductValuation
        from .orders import ProductionByproduct

        BomByproduct.objects.create(
            bom=self.widget_bom, item=self.regrind, quantity=Decimal("2"),
            uom=self.kg, valuation=ByproductValuation.SHARE,
            cost_share_percent=Decimal("10"),
        )
        self.reject("20")
        order = self.run_for("100")
        order.release(TODAY)
        self.assertEqual(order.planned_material_cost, Decimal("1250"))
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("100"),
            quantity_scrapped=Decimal("25"), uom=self.kg,
        )
        row = ProductionByproduct.objects.create(
            entry=entry, item=self.regrind, quantity=Decimal("2.5"),
            uom=self.kg,
        )
        entry.post()
        row.refresh_from_db()
        self.assertEqual(row.unit_value, Decimal("50"))
