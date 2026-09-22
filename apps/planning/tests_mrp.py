"""
Netting, from one sack order back to the polymer.

Every quantity asserted here was worked out from the bills of
materials in `tests_base` and the arithmetic is in the comment beside
it. Where a figure surprised me I recomputed it by hand rather than
adjusting it to match: two of these were the code being right and my
arithmetic being wrong, and one was the reverse.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import MovementType, StockMovement, Warehouse
from apps.inventory.tracking import Lot, TrackingMode
from apps.manufacturing.orders import ProductionEntry, WorkOrder
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    VendorPrice,
)
from apps.sales.models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

from .models import DemandSource, PlannedOrderKind, PlanningSettings
from .mrp import plan
from .tests_base import TODAY, PlantTestCase


class PlanningTestCase(PlantTestCase):
    def setUp(self):
        super().setUp()
        self.settings = PlanningSettings.objects.create(
            horizon_days=120, default_buy_lead_days=7,
            default_make_lead_days=2, requisition_requester=self.buyer,
        )

    def plan(self, warehouse=None, **kwargs):
        kwargs.setdefault("planned_on", TODAY)
        return plan(warehouse or self.plant, **kwargs)

    def orders(self, run=None):
        run = run or self.plan()
        return {order.item.sku: order for order in run.orders.all()}


class OneSackOrderPullsTheWholePlantTests(PlanningTestCase):
    def test_a_sale_of_fabric_raises_a_run_for_it(self):
        self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.kind, PlannedOrderKind.MAKE)
        self.assertEqual(fabric.quantity, Decimal("1000"))
        self.assertEqual(fabric.bom, self.fabric_bom)

    def test_the_tape_below_it_is_grossed_up_for_loom_waste(self):
        self.sell(self.fabric, "1000", self.day(30))
        # 2% of the input is lost on the loom, so 1000 / 0.98 =
        # 1020.4081632653... kg of tape, rounded up at four places.
        self.assertEqual(
            self.orders()["TAPE-1000"].quantity, Decimal("1020.4082")
        )

    def test_the_polymer_below_that_is_grossed_up_again(self):
        self.sell(self.fabric, "1000", self.day(30))
        # 75 kg of virgin per 100 kg batch with 3% of the input lost:
        # 1020.4082 x 75 / 0.97 / 100 = 788.975412...  -> 788.9755.
        self.assertEqual(
            self.orders()["PP-RAFFIA"].quantity, Decimal("788.9755")
        )
        # and 15 kg of regrind the same way: 157.795082... -> 157.7951.
        self.assertEqual(
            self.orders()["REGRIND"].quantity, Decimal("157.7951")
        )

    def test_what_is_stored_is_what_was_exploded(self):
        """
        The quantity is rounded before the explosion, not after.

        A requirement carried at twenty places and saved into four is
        exploded against one number and read back as another, and the
        components of the run a planner firms then disagree with the
        components the plan showed them.
        """
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        self.assertEqual(
            run.orders.get(item=self.tape).quantity, Decimal("1020.4082")
        )

    def test_an_item_with_a_bom_is_made_and_one_without_is_bought(self):
        self.sell(self.fabric, "1000", self.day(30))
        found = self.orders()
        self.assertEqual(found["TAPE-1000"].kind, PlannedOrderKind.MAKE)
        self.assertEqual(found["PP-RAFFIA"].kind, PlannedOrderKind.BUY)

    def test_stock_on_the_shelf_stops_the_whole_cascade(self):
        self.stock(self.fabric, "2000")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders(), {})


class WhenToStartTests(PlanningTestCase):
    def test_each_stage_is_wanted_the_day_the_one_above_starts(self):
        self.sell(self.fabric, "1000", self.day(30))
        found = self.orders()
        fabric, tape, virgin = (
            found["FAB-10X10"], found["TAPE-1000"], found["PP-RAFFIA"]
        )
        self.assertEqual(fabric.needed_by, self.day(30))
        self.assertEqual(tape.needed_by, fabric.release_on)
        self.assertEqual(virgin.needed_by, tape.release_on)

    def test_a_bought_item_takes_the_stated_default_when_nothing_is_agreed(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders()["PP-RAFFIA"].lead_days, 7)

    def test_an_agreed_lead_time_beats_the_default(self):
        VendorPrice.objects.create(
            item=self.virgin, vendor=self.vendor, currency=self.inr,
            unit_price=Decimal("90"), lead_time_days=21, is_preferred=True,
        )
        self.rule(self.virgin, minimum="1000")
        virgin = self.orders()["PP-RAFFIA"]
        self.assertEqual(virgin.lead_days, 21)
        self.assertEqual(virgin.vendor, self.vendor)

    def test_the_rules_own_vendor_wins(self):
        VendorPrice.objects.create(
            item=self.virgin, vendor=self.vendor, currency=self.inr,
            unit_price=Decimal("90"), lead_time_days=21, is_preferred=True,
        )
        self.rule(self.virgin, minimum="1000", vendor=self.vendor)
        self.assertEqual(self.orders()["PP-RAFFIA"].vendor, self.vendor)

    def test_a_made_item_is_timed_off_its_routing(self):
        self.sell(self.fabric, "1000", self.day(30))
        # 1000 kg on a loom rated 60 kg an hour is 16h 40m plus an
        # hour of setup, against a 24-hour day: one day.
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 1)

    def test_a_single_shift_line_takes_longer_than_a_continuous_one(self):
        """
        Minutes are divided by the machine's own open hours.

        An extruder on three shifts and a stitching line on one turn
        the same minutes into very different numbers of days, and a
        plant-wide day would promise the second one's work at the
        first one's speed.
        """
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        # 1000 kg at 60 an hour is 16h 40m, plus an hour of setup:
        # 17.667 hours against an eight-hour day is 2.21, so three.
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 3)

    def test_a_queue_allowance_is_added_to_the_run_time(self):
        self.settings.queue_days = 2
        self.settings.save()
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders()["FAB-10X10"].lead_days, 3)

    def test_a_chain_that_cannot_fit_reports_as_late_rather_than_moving(self):
        """
        The release date stays in the past.

        Sliding it forward to today would make an impossible plan read
        as feasible, which is the most expensive thing a planner can be
        told.
        """
        self.sell(self.fabric, "1000", self.day(3))
        found = self.orders()
        self.assertFalse(found["FAB-10X10"].is_late())
        self.assertTrue(found["PP-RAFFIA"].is_late())
        # Wanted on day 1, seven days to buy: six days behind already.
        self.assertEqual(found["PP-RAFFIA"].days_late(), 6)
        self.assertLess(found["PP-RAFFIA"].release_on, TODAY)

    def test_an_overdue_line_is_still_owed_and_wanted_now(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            order_date=TODAY - datetime.timedelta(days=60), currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.plant,
            delivery_date=TODAY - datetime.timedelta(days=30),
        )
        order.confirm()
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.needed_by, TODAY)
        self.assertTrue(fabric.is_late())

    def test_a_line_with_no_date_of_its_own_falls_back_to_the_order(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.plant,
        )
        order.confirm()
        self.assertEqual(line.promised_date(), TODAY)
        self.assertEqual(self.orders()["FAB-10X10"].needed_by, TODAY)


class WhenThePlantIsShutTests(PlanningTestCase):
    """
    Run time is counted in the plant's working days; a vendor's quoted
    lead time is not, because their weekends are already inside the
    number they quoted. Both land on a day this plant works.

    June 2026 starts on a Monday, so the 6th and 7th are a weekend.
    """

    def weekdays(self):
        self.settings.working_days = "12345"
        self.settings.save()

    def test_a_run_wanted_on_monday_starts_the_friday_before(self):
        self.weekdays()
        self.sell(self.fabric, "1000", self.day(7))
        fabric = self.orders()["FAB-10X10"]
        # Wanted Monday the 8th, one working day of loom time.
        self.assertEqual(fabric.needed_by, datetime.date(2026, 6, 8))
        self.assertEqual(fabric.release_on, datetime.date(2026, 6, 5))

    def test_a_run_wanted_on_a_sunday_is_finished_by_the_friday(self):
        self.weekdays()
        self.sell(self.fabric, "1000", self.day(6))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.needed_by, datetime.date(2026, 6, 7))
        # A Sunday is not a day this plant finishes anything on, so the
        # single day of loom time is the Thursday into the Friday.
        self.assertEqual(fabric.release_on, datetime.date(2026, 6, 4))

    def test_a_festival_shutdown_pushes_the_start_back_further(self):
        from apps.hr.calendars import PublicHoliday

        self.weekdays()
        for day in (3, 4):
            PublicHoliday.objects.create(
                name="Shutdown", date=datetime.date(2026, 6, day)
            )
        self.sell(self.fabric, "1000", self.day(7))
        fabric = self.orders()["FAB-10X10"]
        # Monday the 8th back one working day is the Friday still, but
        # the tape below it now steps over the shut Wednesday and
        # Thursday.
        tape = self.orders()["TAPE-1000"]
        self.assertEqual(tape.needed_by, datetime.date(2026, 6, 5))
        self.assertEqual(tape.release_on, datetime.date(2026, 6, 2))

    def test_a_continuous_line_is_unaffected(self):
        self.sell(self.fabric, "1000", self.day(7))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.release_on, datetime.date(2026, 6, 7))

    def test_a_vendors_promise_is_counted_in_calendar_days(self):
        """
        Five weeks from the importer is five weeks of the world's time.
        Counting it in this plant's working days would stretch a
        five-day lead time into a seven-day one and buy everything
        early.
        """
        self.weekdays()
        self.rule(self.virgin, minimum="1000")
        virgin = self.orders()["PP-RAFFIA"]
        self.assertEqual(virgin.lead_days, 7)
        # Wanted Monday the 1st, seven calendar days back is the Monday
        # before — a day this plant works, so it stands.
        self.assertEqual(virgin.needed_by, TODAY)
        self.assertEqual(virgin.release_on, datetime.date(2026, 5, 25))

    def test_a_purchase_is_never_released_on_a_day_nobody_is_in(self):
        self.weekdays()
        self.rule(self.virgin, minimum="1000")
        self.settings.default_buy_lead_days = 9
        self.settings.save()
        virgin = self.orders()["PP-RAFFIA"]
        # Nine calendar days before Monday the 1st is Saturday 23 May,
        # so the order goes out on the Friday before it.
        self.assertEqual(virgin.release_on, datetime.date(2026, 5, 22))


class SupplyTests(PlanningTestCase):
    def confirmed_purchase(self, item, quantity, expected, uom=None):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=item, uom=uom or self.kg,
            quantity=Decimal(quantity), unit_price=Decimal("90"),
            expected_date=expected,
        )
        order.confirm()
        return order

    def test_a_receipt_on_the_day_it_is_wanted_covers_it(self):
        """
        Supply lands before demand on a date they share.

        Told otherwise, a planner raises an order for material sitting
        on the dock.
        """
        self.sell(self.fabric, "1000", self.day(30))
        self.confirmed_purchase(self.virgin, "2000", self.day(22))
        self.assertNotIn("PP-RAFFIA", self.orders())

    def test_a_receipt_a_day_late_does_not_cover_it(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.confirmed_purchase(self.virgin, "2000", self.day(30))
        self.assertIn("PP-RAFFIA", self.orders())

    def test_a_part_received_line_only_supplies_the_rest(self):
        self.rule(self.virgin, minimum="2000")
        self.confirmed_purchase(self.virgin, "2000", TODAY)
        self.assertEqual(self.orders(), {})

    def test_an_open_run_supplies_what_it_has_left_to_make(self):
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
            scheduled_end=self.day(5),
        )
        order.release(TODAY)
        self.issue_everything(order)
        ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("600"), uom=self.kg,
        ).post()
        self.sell(self.tape, "1000", self.day(10))
        # 600 on the shelf and 400 still to come off the run.
        self.assertNotIn("TAPE-1000", self.orders())

    def test_a_cancelled_run_supplies_nothing(self):
        self.sell(self.tape, "1000", self.day(40))
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_end=self.day(20),
        )
        self.assertNotIn("TAPE-1000", self.orders())
        order.cancel()
        self.assertIn("TAPE-1000", self.orders())

    def test_a_draft_run_is_counted_both_ways(self):
        """
        A run nobody has released is still a run somebody decided on.

        Counting only released runs meant a planner who firmed on
        Monday was handed the whole of Monday's plan again on Tuesday.
        """
        self.sell(self.tape, "1000", self.day(40))
        WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=self.day(20),
            scheduled_end=self.day(25),
        )
        found = self.orders()
        self.assertNotIn("TAPE-1000", found)
        # and its components are demand, exploded from the bill of
        # materials, because a draft run has no frozen requirements.
        self.assertIn("PP-RAFFIA", found)
        self.assertEqual(found["PP-RAFFIA"].needed_by, self.day(20))

    def test_a_requisition_already_raised_is_supply(self):
        from apps.purchasing.models import (
            PurchaseRequisition,
            PurchaseRequisitionLine,
        )

        self.rule(self.virgin, minimum="1000")
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.buyer, request_date=TODAY, needed_by=TODAY,
        )
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.virgin, uom=self.kg,
            quantity=Decimal("1000"),
        )
        self.assertEqual(self.orders(), {})

    def test_a_rejected_requisition_supplies_nothing(self):
        from apps.purchasing.models import (
            PurchaseRequisition,
            PurchaseRequisitionLine,
        )

        self.rule(self.virgin, minimum="1000")
        requisition = PurchaseRequisition.objects.create(
            requested_by=self.buyer, request_date=TODAY, needed_by=TODAY,
        )
        PurchaseRequisitionLine.objects.create(
            requisition=requisition, item=self.virgin, uom=self.kg,
            quantity=Decimal("1000"),
        )
        requisition.submit()
        requisition.reject(note="Not this quarter.")
        self.assertIn("PP-RAFFIA", self.orders())


class RegrindComesBackTests(PlanningTestCase):
    def test_an_open_runs_byproduct_is_counted(self):
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
            scheduled_end=self.day(5),
        )
        order.release(TODAY)
        self.issue_everything(order)
        # 154.639175 kg drawn leaves 4,845.360825 on the shelf, and
        # 24.74227 kg is due back off the run on day 5.
        self.sell(self.regrind, "4875", self.day(10))
        # 4875 - 4845.360825 - 24.74227 = 4.896905 -> 4.8970.
        self.assertEqual(self.orders()["REGRIND"].quantity, Decimal("4.8970"))

    def test_regrind_from_an_earlier_planned_run_covers_a_later_one(self):
        """
        The plant's own scrap is not bought twice.

        A run planned for June throws off reground trim that the July
        run eats, and a plan that cannot see that sends a buyer out for
        polymer waste the plant is about to produce.
        """
        self.sell(self.fabric, "1000", self.day(30))
        self.sell(self.fabric, "1000", self.day(60))
        run = self.plan()
        regrinds = list(run.orders.filter(item=self.regrind).order_by("needed_by"))
        self.assertEqual(len(regrinds), 2)
        self.assertEqual(regrinds[0].quantity, Decimal("157.7951"))
        # 1020.4082 x 2.474227 / 100 = 25.24718 kg back, less the
        # 0.000014 kg the first order over-covered by: 132.547892
        # short, rounded up to 132.5479.
        self.assertEqual(regrinds[1].quantity, Decimal("132.5479"))

    def test_a_runs_own_byproduct_does_not_feed_itself(self):
        """
        Trim comes off at the end of a run, not at the start.

        Netting it against the same run's input would have the
        extruder eating waste it has not produced yet.
        """
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders()["REGRIND"].quantity, Decimal("157.7951"))


class EveryReversePutsTheDemandBackTests(PlanningTestCase):
    """
    The shape that has cost this codebase more than any other: a guard
    on the forward path and nothing on its mirror. Every figure the
    planner reads is a net of a forward document and its reversal, so
    each of those reversals has to put the requirement back.
    """

    def test_a_customer_return_makes_the_line_owed_again(self):
        self.stock(self.fabric, "1000")
        line = self.sell(self.fabric, "1000", self.day(40))
        delivery = Delivery.objects.create(
            sales_order=line.order, delivery_date=TODAY
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.plant,
            quantity_shipped=Decimal("1000"),
        )
        delivery.post()
        self.assertEqual(self.orders(), {})

        delivery.create_return()
        # Shipped nets back to nothing, and the thousand kilos that
        # came back are on the shelf, so the line is owed and covered.
        self.assertEqual(self.orders(), {})
        # Take the returned stock away and the shortage reappears.
        StockMovement.objects.create(
            item=self.fabric, warehouse=self.plant,
            movement_type=MovementType.ADJUSTMENT, uom=self.kg,
            quantity=Decimal("-1000"), unit_cost=Decimal("100"),
            occurred_at=timezone.now(),
        )
        self.assertEqual(self.orders()["FAB-10X10"].quantity, Decimal("1000"))

    def test_material_returned_to_store_is_wanted_by_the_run_again(self):
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
        )
        order.release(TODAY)
        issue = self.issue_everything(order)
        self.rule(self.virgin, minimum="4300")
        # 773.195876 drawn from 5,000 leaves 4,226.80, so the floor of
        # 4,300 is 73.20 short and the run wants nothing more.
        self.assertEqual(
            self.orders()["PP-RAFFIA"].quantity, Decimal("73.1959")
        )
        issue.void(TODAY)
        # Back on the shelf, and the run has yet to draw it: 5,000 on
        # hand less 773.195876 still owed to the run is the same figure.
        self.assertEqual(
            self.orders()["PP-RAFFIA"].quantity, Decimal("73.1959")
        )

    def test_a_voided_production_entry_takes_its_supply_back(self):
        self.stock(self.virgin, "5000")
        self.stock(self.regrind, "5000")
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=TODAY,
            scheduled_end=self.day(5),
        )
        order.release(TODAY)
        self.issue_everything(order)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("1000"), uom=self.kg,
        )
        entry.post()
        self.sell(self.tape, "1000", self.day(10))
        self.assertNotIn("TAPE-1000", self.orders())
        entry.void(TODAY)
        # The tape is off the shelf and the run owes it again, so the
        # sale is still covered — by the run rather than by stock.
        self.assertNotIn("TAPE-1000", self.orders())
        # Close the run and the cover goes with it: a closed run makes
        # no more, and what it did make is already counted.
        order.close(TODAY)
        self.assertEqual(
            self.orders()["TAPE-1000"].quantity, Decimal("1000")
        )


class WhatIsOnTheShelfTests(PlanningTestCase):
    def test_a_reservation_does_not_hide_the_order_that_made_it(self):
        """
        On hand, not available.

        Confirming a sales order reserves stock for it. Reading
        `available_at` here would net the order against a shelf its own
        reservation had already emptied, and buy the same fabric twice.
        """
        self.stock(self.fabric, "1200")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(self.orders(), {})

    def test_an_expired_batch_is_not_cover(self):
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        lot = Lot.objects.create(item=self.virgin, code="L1")
        StockMovement.objects.create(
            item=self.virgin, warehouse=self.plant,
            movement_type=MovementType.RECEIPT, uom=self.kg,
            quantity=Decimal("5000"), unit_cost=Decimal("90"), lot=lot,
            occurred_at=timezone.now(),
        )
        self.rule(self.virgin, minimum="1000")
        self.assertEqual(self.orders(), {})
        lot.expires_on = TODAY - datetime.timedelta(days=1)
        lot.save()
        self.assertEqual(self.orders()["PP-RAFFIA"].quantity, Decimal("1000"))

    def test_a_batch_a_mandatory_plan_has_not_passed_is_not_cover(self):
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine

        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        characteristic = Characteristic.objects.create(
            code="MFI", name="Melt flow index", uom=self.kg
        )
        inspection = InspectionPlan.objects.create(
            item=self.virgin, name="Incoming polymer", is_mandatory=True
        )
        PlanLine.objects.create(
            plan=inspection, characteristic=characteristic,
            lower_limit=Decimal("2"), upper_limit=Decimal("4"), line_number=1,
        )
        lot = Lot.objects.create(item=self.virgin, code="L1")
        StockMovement.objects.create(
            item=self.virgin, warehouse=self.plant,
            movement_type=MovementType.RECEIPT, uom=self.kg,
            quantity=Decimal("5000"), unit_cost=Decimal("90"), lot=lot,
            occurred_at=timezone.now(),
        )
        self.rule(self.virgin, minimum="1000")
        # Not yet inspected is not passed, so none of it is cover.
        self.assertEqual(self.orders()["PP-RAFFIA"].quantity, Decimal("1000"))

    def test_a_negative_shelf_is_a_shortage_in_its_own_right(self):
        StockMovement.objects.create(
            item=self.virgin, warehouse=self.plant,
            movement_type=MovementType.ADJUSTMENT, uom=self.kg,
            quantity=Decimal("-50"), unit_cost=Decimal("90"),
            occurred_at=timezone.now(),
        )
        self.rule(self.virgin, minimum="100")
        self.assertEqual(self.orders()["PP-RAFFIA"].quantity, Decimal("150"))


class UnitsTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )

    def sell_in_tonnes(self, quantity, due):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        line = SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.tonne,
            quantity=Decimal(quantity), unit_price=Decimal("90000"),
            revenue_account=self.revenue, warehouse=self.plant,
            delivery_date=due,
        )
        order.confirm()
        return line

    def test_a_sale_in_tonnes_is_netted_in_kilogrammes(self):
        self.sell_in_tonnes("2", self.day(40))
        self.assertEqual(self.orders()["FAB-10X10"].quantity, Decimal("2000"))

    def test_a_shelf_in_kilogrammes_covers_a_sale_in_tonnes(self):
        self.stock(self.fabric, "2500")
        self.sell_in_tonnes("2", self.day(40))
        self.assertEqual(self.orders(), {})

    def test_a_purchase_in_tonnes_is_supply_in_kilogrammes(self):
        self.rule(self.virgin, minimum="1500")
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=TODAY, currency=self.inr,
        )
        PurchaseOrderLine.objects.create(
            order=order, item=self.virgin, uom=self.tonne, quantity=Decimal("2"),
            unit_price=Decimal("90000"), expected_date=TODAY,
        )
        order.confirm()
        self.assertEqual(self.orders(), {})


class WarehouseTests(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.other = Warehouse.objects.create(code="N", name="Nagpur")

    def test_demand_at_another_warehouse_is_planned_there_and_not_here(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.other, delivery_date=self.day(40),
        )
        order.confirm()
        self.assertEqual(self.orders(), {})
        self.assertIn("FAB-10X10", self.orders(self.plan(self.other)))

    def test_another_shelfs_demand_is_ignored_on_an_item_planned_here_anyway(self):
        """
        Polymer in the Hyderabad godown does not cover a run in
        Nagpur, and Nagpur's order book must not pull on Hyderabad.
        """
        self.rule(self.fabric, minimum="100")
        self.stock(self.fabric, "500")
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.other, delivery_date=self.day(40),
        )
        order.confirm()
        self.assertEqual(self.orders(), {})

    def test_stock_on_another_shelf_does_not_cover(self):
        StockMovement.objects.create(
            item=self.fabric, warehouse=self.other,
            movement_type=MovementType.RECEIPT, uom=self.kg,
            quantity=Decimal("5000"), unit_cost=Decimal("100"),
            occurred_at=timezone.now(),
        )
        self.sell(self.fabric, "1000", self.day(40))
        self.assertIn("FAB-10X10", self.orders())

    def test_a_shelf_nobody_may_pick_from_is_refused(self):
        hold = Warehouse.objects.create(code="Q", name="Hold", is_quarantine=True)
        with self.assertRaises(ValidationError):
            self.plan(hold)


class LotSizingAndSafetyStockTests(PlanningTestCase):
    def test_a_multiple_rounds_the_order_up_and_says_by_how_much(self):
        self.rule(self.virgin, multiple_of="1000")
        self.sell(self.fabric, "1000", self.day(30))
        virgin = self.orders()["PP-RAFFIA"]
        self.assertEqual(virgin.quantity, Decimal("1000"))
        # The reason peg covers 788.9754 and the multiple adds the
        # rest: 1000 - 788.9754 = 211.0246.
        self.assertEqual(virgin.rounded_up_by, Decimal("211.0246"))
        self.assertEqual(
            virgin.demands.get().quantity + virgin.rounded_up_by,
            virgin.quantity,
        )

    def test_a_floor_is_topped_up_even_when_nothing_has_asked(self):
        """
        With no demand at all, nothing would check the balance against
        the floor, and an item sitting under its safety level would
        read as fine because nobody had wanted any.
        """
        self.rule(self.virgin, minimum="500")
        virgin = self.orders()["PP-RAFFIA"]
        self.assertEqual(virgin.quantity, Decimal("500"))
        self.assertEqual(
            [d.source for d in virgin.demands.all()], [DemandSource.SAFETY]
        )

    def test_a_floor_beyond_the_days_demand_is_the_floors_to_own(self):
        """
        A share capped at what the document asked for.

        Uncapped, a customer line that wanted a hundred kilos would be
        pegged for six hundred, and the reasons under the order would
        come to nearly twice the order.
        """
        self.rule(self.fabric, minimum="500")
        # Wanted today, so it shares the bucket the floor is checked
        # in — the one place the balance can start below the floor and
        # a document can be asking in the same breath.
        self.sell(self.fabric, "100", TODAY)
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.quantity, Decimal("600"))
        self.assertEqual(fabric.needed_by, TODAY)
        self.assertEqual(
            sorted(
                (d.source, d.quantity) for d in fabric.demands.all()
            ),
            [(DemandSource.SAFETY, Decimal("500")),
             (DemandSource.SALES, Decimal("100"))],
        )
        self.assertEqual(
            sum(d.quantity for d in fabric.demands.all()) + fabric.rounded_up_by,
            fabric.quantity,
        )

    def test_the_floor_is_kept_under_dated_demand_too(self):
        self.stock(self.fabric, "1200")
        self.rule(self.fabric, minimum="400")
        self.sell(self.fabric, "1000", self.day(30))
        # 1200 on the shelf, 1000 promised, 400 to be kept back.
        self.assertEqual(self.orders()["FAB-10X10"].quantity, Decimal("200"))


class WhyEachOrderExistsTests(PlanningTestCase):
    def test_an_order_names_the_customer_line_that_asked(self):
        line = self.sell(self.fabric, "1000", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        demand = fabric.demands.get()
        self.assertEqual(demand.sales_order_line, line)
        self.assertEqual(demand.quantity, Decimal("1000"))
        self.assertIn(line.order.number, fabric.explanation())

    def test_a_days_shortage_is_shared_between_the_lines_due_that_day(self):
        """
        Pro rata, not first-come-first-served.

        Serving the list in order pegs the shortage to whichever line
        the query returned first, so a run that exists because one
        customer is short reads as belonging to another whose stock was
        already on the shelf.
        """
        self.stock(self.fabric, "700")
        self.sell(self.fabric, "600", self.day(30))
        self.sell(self.fabric, "400", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        self.assertEqual(fabric.quantity, Decimal("300"))
        self.assertEqual(
            sorted(d.quantity for d in fabric.demands.all()),
            [Decimal("120"), Decimal("180")],
        )

    def test_the_pegs_add_up_to_the_order(self):
        self.stock(self.fabric, "700")
        self.sell(self.fabric, "600", self.day(30))
        self.sell(self.fabric, "400", self.day(30))
        fabric = self.orders()["FAB-10X10"]
        pegged = sum(d.quantity for d in fabric.demands.all())
        self.assertEqual(pegged + fabric.rounded_up_by, fabric.quantity)

    def test_every_order_in_a_plan_adds_up_to_its_reasons(self):
        """
        The invariant the whole explanation rests on.

        Reasons that came to less than the order left the difference
        unexplained; reasons that came to more claimed material nobody
        had asked for. Both happened, and both were invisible until
        this was asserted across a whole plan rather than one order.
        """
        self.rule(self.virgin, multiple_of="1000")
        self.rule(self.regrind, minimum="40")
        self.stock(self.fabric, "300")
        self.sell(self.fabric, "600", self.day(30))
        self.sell(self.fabric, "400", self.day(30))
        self.sell(self.fabric, "900", self.day(60))
        run = self.plan()
        self.assertTrue(run.orders.exists())
        for order in run.orders.all():
            pegged = sum(d.quantity for d in order.demands.all())
            self.assertEqual(
                pegged + order.rounded_up_by, order.quantity, str(order)
            )

    def test_rounding_is_never_dressed_up_as_safety_stock(self):
        """
        A peg reading "to hold safety stock" where no floor is set is
        a sentence the system made up. The sub-hundredth left over by
        rounding a requirement up belongs on the order as rounding.
        """
        self.sell(self.fabric, "1000", self.day(30))
        tape = self.orders()["TAPE-1000"]
        self.assertEqual(
            [d.source for d in tape.demands.all()], [DemandSource.PLANNED]
        )
        self.assertEqual(tape.rounded_up_by, Decimal("0.0001"))
        # Recorded, so the reasons add up, and not said out loud.
        self.assertNotIn("rounding", tape.explanation())

    def test_a_rounding_worth_reading_about_is_said_out_loud(self):
        self.rule(self.virgin, multiple_of="1000")
        self.sell(self.fabric, "1000", self.day(30))
        virgin = self.orders()["PP-RAFFIA"]
        self.assertIn("211.0246 added by rounding", virgin.explanation())

    def test_a_run_naming_a_work_order_says_which(self):
        order = WorkOrder.objects.create(
            item=self.tape, bom=self.tape_bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant, scheduled_start=self.day(5),
        )
        demand = self.orders()["PP-RAFFIA"].demands.get()
        self.assertEqual(demand.source, DemandSource.WORK_ORDER)
        self.assertEqual(demand.work_order, order)

    def test_a_lower_level_order_names_the_planned_one_above_it(self):
        self.sell(self.fabric, "1000", self.day(30))
        found = self.orders()
        demand = found["TAPE-1000"].demands.get()
        self.assertEqual(demand.source, DemandSource.PLANNED)
        self.assertEqual(demand.parent, found["FAB-10X10"])


class DatedNettingTests(PlanningTestCase):
    def test_two_dates_raise_two_orders(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.sell(self.fabric, "800", self.day(60))
        run = self.plan()
        self.assertEqual(
            [(o.quantity, o.needed_by)
             for o in run.orders.filter(item=self.fabric).order_by("needed_by")],
            [(Decimal("1000"), self.day(30)), (Decimal("800"), self.day(60))],
        )

    def test_stock_covers_the_first_call_off_and_not_the_second(self):
        self.stock(self.fabric, "1200")
        self.sell(self.fabric, "1000", self.day(30))
        self.sell(self.fabric, "800", self.day(60))
        fabrics = list(self.plan().orders.filter(item=self.fabric))
        self.assertEqual(len(fabrics), 1)
        self.assertEqual(fabrics[0].quantity, Decimal("600"))
        self.assertEqual(fabrics[0].needed_by, self.day(60))

    def test_demand_past_the_horizon_is_left_alone(self):
        self.sell(self.fabric, "1000", self.day(200))
        self.assertEqual(self.plan(horizon_days=90).orders.count(), 0)

    def test_far_demand_is_left_alone_on_an_item_being_planned_anyway(self):
        """
        The horizon is applied twice, and both have to hold.

        An item reaches the planner either because something asked for
        it or because it has a floor to keep. The second route does not
        go past the horizon check that the first one does, so an item
        with a reorder rule would drag in every distant order on the
        book if only the first were tested.
        """
        self.rule(self.virgin, minimum="100")
        self.stock(self.virgin, "500")
        self.sell(self.virgin, "1000", self.day(200))
        self.assertEqual(self.plan(horizon_days=90).orders.count(), 0)

    def test_a_part_shipped_line_only_asks_for_the_rest(self):
        self.stock(self.fabric, "400")
        line = self.sell(self.fabric, "1000", self.day(40))
        delivery = Delivery.objects.create(
            sales_order=line.order, delivery_date=TODAY
        )
        DeliveryLine.objects.create(
            delivery=delivery, order_line=line, warehouse=self.plant,
            quantity_shipped=Decimal("400"),
        )
        delivery.post()
        self.assertEqual(self.orders()["FAB-10X10"].quantity, Decimal("600"))

    def test_a_draft_sale_is_not_a_promise_on_an_item_being_planned_anyway(self):
        """
        Same two routes as the horizon: an item with a floor is
        planned whether or not anybody confirmed anything, so the
        confirmed-only rule has to be tested on that route too.
        """
        self.rule(self.fabric, minimum="100")
        self.stock(self.fabric, "500")
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.plant, delivery_date=self.day(40),
        )
        self.assertEqual(self.orders(), {})

    def test_a_draft_sale_is_not_a_promise(self):
        order = SalesOrder.objects.create(
            customer=self.customer, order_date=TODAY, currency=self.inr,
        )
        SalesOrderLine.objects.create(
            order=order, item=self.fabric, uom=self.kg, quantity=Decimal("1000"),
            unit_price=Decimal("90"), revenue_account=self.revenue,
            warehouse=self.plant, delivery_date=self.day(40),
        )
        self.assertEqual(self.orders(), {})


class RefusalTests(PlanningTestCase):
    def test_an_item_with_no_agreed_lead_time_and_no_default_refuses(self):
        self.settings.default_buy_lead_days = None
        self.settings.save()
        self.sell(self.fabric, "1000", self.day(30))
        with self.assertRaises(ValidationError):
            self.plan()

    def test_a_bom_with_no_routing_and_no_default_refuses(self):
        self.settings.default_make_lead_days = None
        self.settings.save()
        self.fabric_bom.routing = None
        self.fabric_bom.save()
        self.sell(self.fabric, "1000", self.day(30))
        with self.assertRaises(ValidationError):
            self.plan()

    def test_a_routing_rated_in_the_wrong_kind_of_unit_refuses(self):
        """
        A loom quoted in pieces, asked how long a thousand kilogrammes
        takes, has no answer — and the plausible wrong one is a
        delivery date somebody will promise.
        """
        pieces = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        operation = self.weaving.operations.get()
        operation.units_per_hour = Decimal("5000")
        operation.rate_uom = pieces
        operation.save()
        self.sell(self.fabric, "1000", self.day(30))
        with self.assertRaises(ValidationError):
            self.plan()
