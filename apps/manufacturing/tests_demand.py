"""
What has been sold against what is being made.

A sack plant makes to order far more than it makes to stock, so the
planner's morning question is not "what is on the board" but "what have
we promised that nothing is making yet". Until a run could say which
customer line it was for, nothing could answer it.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Party
from apps.inventory.models import Lot, TrackingMode
from apps.sales.models import SalesOrder, SalesOrderLine

from .demand import consumed_lots, coverage, genealogy, runs_that_made, uncovered
from .orders import WorkOrderStatus
from .tests_orders import TODAY, RunTestCase


class DemandTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.customer = Party.objects.create(code="CEM", name="Deccan Cement")
        # The base fixture stocks a 1,000 kg run; some of these are
        # 4,000 kg ones. Same prices, so the averages do not move.
        self.stock(self.virgin, "8000", "100")
        self.stock(self.regrind, "2000", "60")
        self.stock(self.filler, "1200", "30")
        self.stock(self.colour, "400", "200")
        self.sale = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 9, 1),
            currency=self.usd,
        )

    def sold(self, quantity="4000", item=None):
        return SalesOrderLine.objects.create(
            order=self.sale, item=item or self.tape, uom=self.kg,
            quantity=Decimal(quantity), unit_price=Decimal("120"),
        )

    def run_for(self, line, quantity="4000"):
        order = self.order(quantity)
        order.sales_order_line = line
        order.save()
        return order


class WhatIsPromisedAgainstWhatIsStartedTests(DemandTestCase):
    def test_a_line_nobody_has_started(self):
        line = self.sold()
        report = coverage(line)
        self.assertEqual(report["ordered"], Decimal("4000"))
        self.assertEqual(report["on_work_orders"], Decimal("0"))
        self.assertEqual(report["uncovered"], Decimal("4000"))

    def test_a_line_with_a_run_against_it(self):
        line = self.sold()
        run = self.run_for(line)
        report = coverage(line)
        self.assertEqual(report["on_work_orders"], Decimal("4000"))
        self.assertEqual(report["uncovered"], Decimal("0"))
        self.assertEqual(report["runs"], [run])

    def test_a_part_covered_line(self):
        line = self.sold("4000")
        self.run_for(line, "2500")
        self.assertEqual(coverage(line)["uncovered"], Decimal("1500"))

    def test_covered_counts_what_is_on_the_loom_not_what_is_off_it(self):
        # Measured against what is on work orders rather than what they
        # have finished, or a planner raises a second run for something
        # already running.
        line = self.sold()
        run = self.run_for(line)
        run.release(TODAY)
        self.assertEqual(coverage(line)["made"], Decimal("0"))
        self.assertEqual(coverage(line)["uncovered"], Decimal("0"))

    def test_a_cancelled_run_covers_nothing(self):
        line = self.sold()
        run = self.run_for(line)
        run.release(TODAY)
        run.cancel()
        self.assertEqual(run.status, WorkOrderStatus.CANCELLED)
        self.assertEqual(coverage(line)["uncovered"], Decimal("4000"))

    def test_a_closed_run_still_does(self):
        # It made what it made; whether that was enough is the `made`
        # figure's business, not the status's.
        line = self.sold()
        run = self.run_for(line)
        run.release(TODAY)
        self.full_issue(run).post()
        self.produce(run, "4000").post()
        run.close(TODAY)
        report = coverage(line)
        self.assertEqual(report["uncovered"], Decimal("0"))
        self.assertEqual(report["made"], Decimal("4000"))

    def test_a_run_for_a_different_item(self):
        line = self.sold(item=self.virgin)
        order = self.order("1000")
        order.sales_order_line = line
        order.save()
        with self.assertRaises(ValidationError) as caught:
            order.release(TODAY)
        self.assertIn("covers the line it is for", str(caught.exception))


class ThePlannersMorningListTests(DemandTestCase):
    def test_it_names_the_lines_nobody_has_started(self):
        first = self.sold("4000")
        second = self.sold("1000")
        self.run_for(second, "1000")
        rows = uncovered()
        self.assertEqual([row["line"] for row in rows], [first])

    def test_an_item_this_plant_buys_is_not_uncovered(self):
        # A line with no bill of materials is not uncovered, it is
        # purchased, and putting it on a production planner's list is
        # noise they will learn to ignore.
        self.sold(item=self.virgin)
        self.assertEqual(uncovered(), [])

    def test_it_can_be_narrowed_to_one_order(self):
        self.sold("4000")
        other = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 9, 2),
            currency=self.usd,
        )
        SalesOrderLine.objects.create(
            order=other, item=self.tape, uom=self.kg,
            quantity=Decimal("500"), unit_price=Decimal("120"),
        )
        self.assertEqual(len(uncovered(sales_order=other)), 1)
        self.assertEqual(len(uncovered()), 2)


class FromASackBackToThePolymerTests(DemandTestCase):
    """
    A customer rejects a pallet for low GSM. The question is which
    fabric went into it and which blend went into that, and it is the
    question a sack plant is asked most often.
    """

    def setUp(self):
        super().setUp()
        self.tape.tracking = TrackingMode.LOT
        self.tape.save()
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        self.polymer_lot = Lot.objects.create(item=self.virgin, code="PP-2609")
        self.tape_lot = Lot.objects.create(item=self.tape, code="TAPE-A")
        self.stock(self.virgin, "3000", "100", lot=self.polymer_lot)

    def make_the_tape(self):
        run = self.order("1000")
        run.release(TODAY)
        rows = [
            (component.item, component.quantity_required,
             self.polymer_lot if component.item_id == self.virgin.pk else None)
            for component in run.components.all()
        ]
        issue = self.issue_with_lots(run, rows)
        issue.post()
        self.produce(run, "1000", lot=self.tape_lot).post()
        return run

    def test_a_run_says_what_went_into_it(self):
        run = self.make_the_tape()
        rows = {lot.code: quantity for lot, _item, quantity in consumed_lots(run)}
        self.assertIn("PP-2609", rows)
        self.assertGreater(rows["PP-2609"], Decimal("700"))

    def test_a_batch_says_which_run_made_it(self):
        run = self.make_the_tape()
        self.assertEqual(runs_that_made(self.tape_lot), [run])

    def test_and_the_two_together_walk_back(self):
        run = self.make_the_tape()
        trail = genealogy(self.tape_lot)
        self.assertTrue(trail)
        step = trail[0]
        self.assertEqual(step["lot"], self.tape_lot)
        self.assertEqual(step["made_by"], run)
        self.assertEqual(step["from_lot"], self.polymer_lot)

    def test_a_material_nobody_batched_is_not_a_silent_empty_answer(self):
        run = self.make_the_tape()
        # Filler and masterbatch are untracked; they have no batch to
        # name and simply do not appear.
        codes = {lot.code for lot, _item, _quantity in consumed_lots(run)}
        self.assertEqual(codes, {"PP-2609"})

    def test_the_regrind_loop_does_not_send_the_walk_round_forever(self):
        # A fabric lot leads back to a tape lot that leads back to a
        # regrind lot that came off the fabric. A walk with no limit
        # does not come back.
        run = self.make_the_tape()
        self.assertLess(len(genealogy(self.tape_lot, depth=4)), 40)


class LinesThatAreNotThePlannersProblemTests(DemandTestCase):
    """
    Three kinds of line used to sit on the morning list and be dismissed
    every morning, which is how a list stops being read. Found by
    probing.
    """

    def test_a_line_on_a_cancelled_order(self):
        # Nobody is promised it, so nobody should be making it.
        self.sold("4000")
        self.assertEqual(len(uncovered()), 1)
        self.sale.status = "cancelled"
        self.sale.save()
        self.assertEqual(uncovered(), [])

    def test_a_line_already_shipped_in_full(self):
        # The one case where netting the shipment off is exact: there is
        # nothing left to make. The shipped figure itself is sales' own
        # and is tested there; what is tested here is that this list
        # drops the line.
        from unittest.mock import patch

        line = self.sold("4000")
        with patch.object(
            SalesOrderLine, "quantity_shipped_in_stock_units",
            return_value=Decimal("4000"),
        ):
            self.assertEqual(uncovered(), [])

    def test_but_a_part_shipped_line_stays(self):
        from unittest.mock import patch

        self.sold("4000")
        with patch.object(
            SalesOrderLine, "quantity_shipped_in_stock_units",
            return_value=Decimal("1000"),
        ):
            rows = uncovered()
        # Not netted off: a shipment may have come out of one of these
        # runs or out of stock that was already there, and nothing here
        # can tell which. Over-reporting is the cheaper mistake.
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uncovered"], Decimal("4000"))


class AByProductHasAProvenanceTooTests(DemandTestCase):
    """
    The genealogy could only walk back from a run's main output, so a
    by-product batch had no origin at all — and in this plant the
    by-product is the regrind, which is exactly the batch somebody wants
    to trace when three runs in a row come out wrong.

    Its own fixture rather than the one above's: inheriting a test case
    inherits its tests, and those issue regrind without a batch.
    """

    def setUp(self):
        super().setUp()
        for item in (self.tape, self.virgin, self.regrind):
            item.tracking = TrackingMode.LOT
            item.save()
        self.polymer_lot = Lot.objects.create(item=self.virgin, code="PP-2609")
        self.tape_lot = Lot.objects.create(item=self.tape, code="TAPE-A")
        self.grind = Lot.objects.create(item=self.regrind, code="RG-1")
        self.stock(self.virgin, "3000", "100", lot=self.polymer_lot)
        self.stock(self.regrind, "1000", "60", lot=self.grind)

    def batch_for(self, component):
        if component.item_id == self.virgin.pk:
            return self.polymer_lot
        if component.item_id == self.regrind.pk:
            return self.grind
        return None

    def a_run_that_eats_and_makes_regrind(self):
        run = self.order("1000")
        run.release(TODAY)
        rows = [
            (component.item, component.quantity_required, self.batch_for(component))
            for component in run.components.all()
        ]
        self.issue_with_lots(run, rows).post()
        entry = self.produce(
            run, "1000", lot=self.tape_lot,
            byproducts=[(self.regrind, "24.7423")],
        )
        entry.byproducts.update(lot=self.grind)
        entry.post()
        return run

    def test_a_byproduct_batch_names_the_run_it_came_off(self):
        run = self.a_run_that_eats_and_makes_regrind()
        self.assertEqual(runs_that_made(self.grind), [run])

    def test_and_the_walk_back_from_it_terminates(self):
        # The regrind loop in its purest form: this batch came off the
        # very run that ate it.
        self.a_run_that_eats_and_makes_regrind()
        trail = genealogy(self.grind, depth=6)
        self.assertTrue(trail)
        self.assertLess(len(trail), 40)
        self.assertEqual(
            {step["from_lot"].code for step in trail}, {"PP-2609", "RG-1"}
        )

    def test_the_walk_enters_each_batch_once_however_deep_it_is_allowed(self):
        # The depth limit alone stops the walk; it does not stop it
        # re-expanding a batch it has already been inside, which on a
        # loop like this one grows with every level allowed. Only the
        # path guard makes the trail the same whatever the depth.
        self.a_run_that_eats_and_makes_regrind()
        self.assertEqual(
            len(genealogy(self.grind, depth=2)),
            len(genealogy(self.grind, depth=6)),
        )

    def test_a_run_is_named_once_however_many_ways_it_made_the_batch(self):
        run = self.a_run_that_eats_and_makes_regrind()
        self.assertEqual(runs_that_made(self.grind).count(run), 1)
