"""
A plan that knows the plant rejects some of what it makes.

The fixture weaves fabric from tape. At no reject rate a thousand
kilos of fabric wanted is a run for a thousand, and 1,020.4082 kg of
tape below it once the loom's 2% input waste is grossed up. Put a
twenty per cent reject on the fabric and the run must still deliver a
thousand, but it must MAKE a thousand two hundred and fifty to do it
— so the tape below it, the loom hours it takes and the trim it
throws off all move with it, and the promise date moves with them.
"""

from decimal import Decimal

from .tests_mrp import PlanningTestCase
from .tests_base import TODAY


class RejectsPullMaterialThroughTests(PlanningTestCase):
    def reject(self, percent):
        self.fabric_bom.expected_reject_percent = Decimal(percent)
        self.fabric_bom.save()

    def test_the_order_still_promises_what_the_customer_asked_for(self):
        """
        The planned order is what must be DELIVERED. Inflating it here
        as well as below would order a thousand two hundred and fifty
        kilos of fabric against a customer who wants a thousand, and
        the plant would make the rejects twice.
        """
        self.reject("20")
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(
            self.orders()["FAB-10X10"].quantity, Decimal("1000")
        )

    def test_the_tape_below_it_covers_the_fabric_that_will_be_rejected(self):
        self.sell(self.fabric, "1000", self.day(30))
        self.assertEqual(
            self.orders()["TAPE-1000"].quantity, Decimal("1020.4082")
        )

        self.reject("20")
        # 1,250 kg of fabric must come off the loom, and 2% of what
        # goes in is lost there: 1250 / 0.98 = 1275.5102040...
        self.assertEqual(
            self.orders()["TAPE-1000"].quantity, Decimal("1275.5103")
        )

    def test_a_shortfall_nobody_planned_for_is_what_this_replaces(self):
        """
        The old behaviour, stated as arithmetic rather than as a
        grievance: the plant bought tape for a thousand kilos of
        fabric, made a thousand, rejected two hundred, and shipped
        eight hundred against an order for a thousand.
        """
        self.reject("20")
        self.sell(self.fabric, "1000", self.day(30))
        tape = self.orders()["TAPE-1000"].quantity
        without = Decimal("1020.4082")
        self.assertGreater(tape, without)
        self.assertEqual(
            (tape / without).quantize(Decimal("0.0001")), Decimal("1.2500")
        )


class RejectsTakeMachineTimeTests(PlanningTestCase):
    def test_a_run_that_makes_more_holds_the_loom_longer(self):
        """
        The loom runs for the rejects too. A plan that books it for
        the delivered quantity promises a date on a machine that is
        twenty per cent busier than it thinks.
        """
        self.loom.available_hours_per_day = Decimal("8")
        self.loom.save()
        self.sell(self.fabric, "1000", self.day(30))
        plain = self.orders()["FAB-10X10"].lead_days

        self.fabric_bom.expected_reject_percent = Decimal("50")
        self.fabric_bom.save()
        doubled = self.orders()["FAB-10X10"].lead_days
        # Half rejected means twice the fabric off the loom.
        self.assertGreater(doubled, plain)


class RejectsThrowOffTheirOwnTrimTests(PlanningTestCase):
    def test_trim_comes_off_what_goes_through_the_machine(self):
        """
        A sack rejected at the stitching table threw off its offcut on
        the way. Counting the by-product off the delivered quantity
        would have the plant buying regrind it is about to make.
        """
        self.sell(self.fabric, "1000", self.day(30))
        plain = self.orders()["PP-RAFFIA"].quantity

        self.fabric_bom.expected_reject_percent = Decimal("20")
        self.fabric_bom.save()
        more = self.orders()["PP-RAFFIA"].quantity
        self.assertGreater(more, plain)


class TheRunTakesLongerWithoutAScheduleToo(PlanningTestCase):
    """
    `make_run_days` is the answer when there is no capacity book to
    ask — a plant that has not turned finite scheduling on, or a
    shortage planned outside it. It has to gross up for rejects on its
    own, because nothing upstream of it does.
    """

    def test_run_days_are_of_what_must_be_started(self):
        from .leadtime import make_run_days

        # Half rejected means two thousand off the loom to deliver one
        # thousand. Not "twice the days": the hour of setup is paid
        # once however long the run, which is what an earlier draft of
        # this assertion forgot. The same as a clean run of two
        # thousand is the claim that actually holds.
        two_thousand = make_run_days(self.fabric_bom, Decimal("2000"), self.kg)
        self.fabric_bom.expected_reject_percent = Decimal("50")
        self.fabric_bom.save()
        half_rejected = make_run_days(
            self.fabric_bom, Decimal("1000"), self.kg
        )
        self.assertEqual(half_rejected, two_thousand)


class ADraftRunAsksForWhatItWillStartTests(PlanningTestCase):
    """
    A draft run has frozen nothing, so its demand is exploded from the
    bill. An earlier version of this read `started_quantity()`, which
    on a draft is just the ordered quantity — under a comment claiming
    it read the frozen figure. The mutation that swapped it back to the
    ordered quantity survived, because the two were the same thing.
    """

    def test_a_draft_run_demands_tape_for_the_rejects_too(self):
        from apps.manufacturing.orders import WorkOrder

        from .mrp import work_order_demand

        self.fabric_bom.expected_reject_percent = Decimal("20")
        self.fabric_bom.save()
        WorkOrder.objects.create(
            item=self.fabric, bom=self.fabric_bom,
            quantity_ordered=Decimal("1000"), uom=self.kg,
            warehouse=self.plant,
        )
        rows = work_order_demand(self.tape, self.plant, TODAY)
        self.assertEqual(len(rows), 1)
        # 1,250 kg of fabric to start; 2% of the tape fed in is lost
        # on the loom, so 1250 / 0.98 = 1275.510204...
        self.assertEqual(
            rows[0].quantity.quantize(Decimal("0.0001")), Decimal("1275.5102")
        )


class TrimComesOffTheStartedQuantityTests(PlanningTestCase):
    def test_a_run_that_rejects_a_fifth_throws_off_a_quarter_more_trim(self):
        """
        The tape bill returns 2.474227 kg of regrind a hundred-kilo
        batch. A thousand delivered at no reject is ten batches, 24.74227
        kg. At twenty per cent reject it is twelve and a half batches,
        because the rejected tape threw off its trim on the way.
        """
        from .mrp import byproduct_supply

        plain = byproduct_supply(
            self.tape_bom, Decimal("1000"), self.kg, self.regrind, TODAY
        )
        self.assertEqual(plain[0].quantity, Decimal("24.74227"))
        self.tape_bom.expected_reject_percent = Decimal("20")
        self.tape_bom.save()
        more = byproduct_supply(
            self.tape_bom, Decimal("1000"), self.kg, self.regrind, TODAY
        )
        self.assertEqual(more[0].quantity, Decimal("30.9278375"))


class CapableToPromiseCountsTheRejectsTests(PlanningTestCase):
    def test_stock_that_covers_the_order_but_not_the_rejects_is_not_enough(self):
        """
        1,100 kg of tape on the shelf covers a thousand kilos of fabric
        (1,020.41 kg needed) and does not cover the 1,275.51 kg needed
        once a fifth of the fabric is rejected. The promise has to move
        by the buy lead time, seven days in the fixture.
        """
        import datetime

        from .promise import _material_ready

        self.stock(self.tape, "1100")
        ready = _material_ready(
            self.fabric, self.fabric_bom, Decimal("1000"), self.plant,
            TODAY, self.settings,
        )
        self.assertEqual(ready, TODAY)
        self.fabric_bom.expected_reject_percent = Decimal("20")
        self.fabric_bom.save()
        ready = _material_ready(
            self.fabric, self.fabric_bom, Decimal("1000"), self.plant,
            TODAY, self.settings,
        )
        self.assertEqual(ready, TODAY + datetime.timedelta(days=7))
