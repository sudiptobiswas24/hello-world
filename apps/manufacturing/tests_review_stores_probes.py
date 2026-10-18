"""
g22 review of fix_stores: probes for O133 (tape on a backflushed run's creels), its MRP
mirror, and O232 (a coating check tied to its roll). Each asserts what SHOULD be true.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.manufacturing import tests_tape_loads as base
from apps.manufacturing.orders import MaterialIssue, MaterialIssueLine, WorkOrder, WorkOrderStatus
from apps.manufacturing.tape_loads import CreelSide
from apps.manufacturing.tests_orders import TODAY
from apps.manufacturing.tests_station import at

D = Decimal


def only_mine(cls):
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


@only_mine
class StoresReviewCreelProbes(base.TapeLoadTestCase):
    def setUp(self):
        super().setUp()
        WorkOrder.objects.filter(pk=self.run.pk).update(backflush=True)
        self.run.refresh_from_db()

    def cover(self):
        """What the plan has to spare of the tape: its opening balance less what live runs still draw."""
        from apps.planning.mrp import opening_balance, work_order_demand

        need = sum((row.quantity for row in work_order_demand(self.tape, self.plant, TODAY)), D("0"))
        return opening_balance(self.tape, self.plant, TODAY) - need

    def test_a_doff_loaded_on_the_run_that_needs_it_leaves_the_cover_as_it_was(self):
        # The doff is neither used nor gone: it is the run's own, which the run's demand already counts.
        before = self.cover()
        self.load("D-1", "100", CreelSide.WARP, 9)
        self.assertEqual(self.cover(), before, "MRP takes the creel's tape off the supply and leaves the run's demand whole")

    def test_q2_a_named_doff_on_a_creel_cannot_be_issued_to_another_run(self):
        self.load("D-1", "100", CreelSide.WARP, 9)
        other = WorkOrder.objects.create(item=self.fabric, bom=self.spec.bom, quantity_ordered=D("100"),
                                         uom=self.kg, warehouse=self.plant)
        other.release(TODAY)
        issue = MaterialIssue.objects.create(work_order=other, issue_date=TODAY, warehouse=self.plant)
        MaterialIssueLine.objects.create(issue=issue, item=self.tape, quantity=D("100"), uom=self.kg,
                                         lot=self.doffs["D-1"], line_number=1)
        with self.assertRaises(ValidationError, msg="100 kg promised to L-17's creel was issued away by name"):
            issue.post()

    def test_q3_half_drawn_leaves_only_the_undrawn_promised(self):
        from apps.inventory.picking import plan_issue

        self.load("D-1", "100", CreelSide.WARP, 9)
        self.load("D-2", "100", CreelSide.WEFT, 9, 5)
        self.weigh(when=at(TODAY, 10, 42))
        d1, d2 = (self.doffs[c].on_hand_at(self.plant) for c in ("D-1", "D-2"))
        free_d3 = self.doffs["D-3"].on_hand_at(self.plant)
        # What is on the shelf and not on a creel: D-3 whole, plus whatever of D-1/D-2 the roll left unloaded (nothing).
        plan = plan_issue(self.tape, self.plant, free_d3)
        self.assertEqual({lot.code for lot, _bin, _q in plan}, {"D-3"})
        with self.assertRaises(ValidationError):
            plan_issue(self.tape, self.plant, free_d3 + D("1"))
        # and exactly the undrawn of D-1 and D-2 is what is on the creels
        from apps.manufacturing.tape_loads import on_creels_undrawn
        self.assertEqual((on_creels_undrawn(self.doffs["D-1"]), on_creels_undrawn(self.doffs["D-2"])), (d1, d2))

    def test_q4_a_run_closed_early_frees_its_creels_for_a_pick(self):
        from apps.inventory.picking import plan_issue

        self.load("D-1", "100", CreelSide.WARP, 9)
        with self.assertRaises(ValidationError):
            plan_issue(self.tape, self.plant, D("300"))
        WorkOrder.objects.filter(pk=self.run.pk).update(status=WorkOrderStatus.CLOSED)
        self.assertEqual(sum(q for _l, _b, q in plan_issue(self.tape, self.plant, D("300"))), D("300"))

    def test_q5_a_withdrawn_load_frees_the_doff_for_a_pick(self):
        from apps.inventory.picking import plan_issue
        from apps.manufacturing.tape_loads import void_load

        load = self.load("D-1", "100", CreelSide.WARP, 9)
        void_load(load, self.station, self.supervisor, self.operator, "Wrong doff")
        self.assertEqual(sum(q for _l, _b, q in plan_issue(self.tape, self.plant, D("300"))), D("300"))

    def test_q6_two_stations_in_two_warehouses_do_not_block_each_others_doff(self):
        # Seen #3: the load check asks on_creels_undrawn(lot) with no warehouse.
        from apps.manufacturing.tape_loads import on_creels_undrawn
        from apps.inventory.models import Warehouse

        self.load("D-1", "100", CreelSide.WARP, 9)
        elsewhere = Warehouse.objects.create(code="W9", name="Other")
        self.assertEqual(on_creels_undrawn(self.doffs["D-1"], elsewhere), D("0"))
        self.assertEqual(on_creels_undrawn(self.doffs["D-1"], self.plant), D("100"))


# ---------------------------------------------------------------------------------------- O232
from apps.manufacturing import tests_process_rolls as rolls_base  # noqa: E402
from apps.manufacturing.process_rolls import mount_roll, weigh_roll  # noqa: E402


@only_mine
class StoresReviewCoatingProbes(rolls_base.RollsTestCase):
    def grams(self):
        return [str(self.lam_spec.bag_grams().quantize(D("0.001")))] * 10

    def two_coated(self, check_on):
        """Fabric roll A mounted, coated LA; fabric roll B mounted, coated LB; the off check taken on A or on B."""
        from apps.manufacturing.station_coat import record_coating
        from apps.manufacturing.tests_station_coat import OFF

        self.mount()
        check = None
        if check_on == "A":
            check = record_coating(self.kx, self.operator, self.k1, "2100", OFF, supervisor=self.supervisor,
                                   reason="Die lip cleaned", at=at(TODAY, 10, 55))
        la = weigh_roll(self.kx, self.operator, self.k1, "127.78", self.core, "1000", at=at(TODAY, 11))
        other = self.weigh(when=at(TODAY, 10, 45))
        self.mount(other.lot.code, when=at(TODAY, 11, 10))
        if check_on == "B":
            check = record_coating(self.kx, self.operator, self.k1, "2100", OFF, supervisor=self.supervisor,
                                   reason="Die lip cleaned", at=at(TODAY, 11, 15))
        lb = weigh_roll(self.kx, self.operator, self.k1, "127.78", self.core, "1000", at=at(TODAY, 11, 20))
        return check, la, lb

    def bundle_from(self, roll, when):
        from apps.manufacturing.conversion import record_bags

        mount_roll(self.cv, self.operator, self.c2, roll.code, at=when)
        return record_bags(self.cv, self.operator, self.c2, "500", self.grams(), at=when + datetime.timedelta(minutes=20))

    def test_c1_a_check_taken_on_the_second_roll_holds_its_bundles_not_the_first(self):
        from apps.quality.models import Disposition

        check, la, lb = self.two_coated("B")
        first = self.bundle_from(la, at(TODAY, 12))
        second = self.bundle_from(lb, at(TODAY, 12, 30))
        self.assertEqual(first.inspection.disposition, Disposition.ACCEPT, "LA was coated before the check")
        self.assertEqual(second.inspection.disposition, Disposition.AWAITING, "LB was coated under the check")

    def test_c2_a_withdrawn_check_holds_no_bundle(self):
        from apps.manufacturing.station_coat import void_coating
        from apps.quality.models import Disposition

        check, la, lb = self.two_coated("A")
        void_coating(check, self.kx, self.supervisor, self.operator, "Wrong discs")
        held = self.bundle_from(la, at(TODAY, 12))
        self.assertEqual(held.inspection.disposition, Disposition.ACCEPT)


# ---------------------------------------------------------------------------------------- O231
from apps.manufacturing import tests_recall  # noqa: E402


@only_mine
class StoresReviewPageGateProbes(tests_recall.RecallTestCase):
    def stocked(self, code, warehouse=None, quantity="5", expires=None):
        from apps.inventory.models import Lot, MovementType, StockMovement

        lot = Lot.objects.create(item=self.bag, code=code, expires_on=expires)
        StockMovement.objects.create(
            item=self.bag, warehouse=warehouse or self.plant, movement_type=MovementType.RECEIPT, uom=self.bag.uom,
            quantity=D(quantity), unit_cost=D("12"), lot=lot,
            occurred_at=datetime.datetime(2026, 6, 1, 8, 0, tzinfo=datetime.timezone.utc))
        return lot

    def test_g1_the_page_says_the_same_words_as_the_single_gate_for_every_hold(self):
        from apps.inventory.models import Warehouse
        from apps.inventory.tracking import held_because, held_because_page
        from apps.manufacturing.tests_bag_counts import IN, OUT

        self.reject_b1()    # recalls A and B (an open recall each), B1 rejected
        bay = Warehouse.objects.create(code="QB", name="Quality bay", is_quarantine=True)
        lots = [self.a, self.b, self.b1]
        unin = self.stocked("G-UNIN")
        passed = self.stocked("G-PASS")
        self.inspect(passed, IN)
        rejected = self.stocked("G-REJ")
        self.inspect(rejected, OUT)
        expired = self.stocked("G-EXP", expires=datetime.date(2026, 1, 1))
        self.inspect(expired, IN)
        quarantined = self.stocked("G-QB", warehouse=bay)
        self.inspect(quarantined, IN)
        split = self.stocked("G-SPLIT")
        self.inspect(split, IN)
        from apps.inventory.models import MovementType, StockMovement
        StockMovement.objects.create(item=self.bag, warehouse=bay, movement_type=MovementType.RECEIPT,
                                     uom=self.bag.uom, quantity=D("2"), unit_cost=D("12"), lot=split,
                                     occurred_at=datetime.datetime(2026, 6, 2, 8, 0, tzinfo=datetime.timezone.utc))
        lots += [unin, passed, rejected, expired, quarantined, split]
        page = held_because_page(lots)
        single = {lot.pk: held_because(lot) for lot in lots}
        differ = {lot.code: (single[lot.pk], page[lot.pk]) for lot in lots if single[lot.pk] != page[lot.pk]}
        self.assertEqual(differ, {}, "the page and the single gate disagree")
        print("\nG1 words:", {lot.code: single[lot.pk][:70] for lot in lots})
        self.assertTrue(all(single[lot.pk] for lot in (unin, rejected, quarantined, split, self.a)))
        self.assertEqual(single[passed.pk], "")

    def test_g2_an_advisory_plan_waits_in_the_same_words(self):
        from apps.inventory.tracking import held_because, held_because_page
        from apps.quality.models import InspectionPlan

        InspectionPlan.objects.filter(item=self.bag).update(is_mandatory=False)
        fresh = self.stocked("G-ADV")
        page, single = held_because_page([fresh])[fresh.pk], held_because(fresh)
        self.assertEqual(page, single)

    def test_g3_queries_for_25_and_100_lots_are_a_page_not_a_row_each(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.inventory.tracking import held_because_page

        small = [self.stocked(f"S-{n:03d}") for n in range(25)]
        with CaptureQueriesContext(connection) as few:
            held_because_page(small)
        big = small + [self.stocked(f"S-{n:03d}") for n in range(25, 100)]
        with CaptureQueriesContext(connection) as many:
            held_because_page(big)
        print(f"\nG3 held_because_page: {len(few)} queries for 25 lots, {len(many)} for 100")
        self.assertLessEqual(len(many), len(few) + 2)

    def test_g4_queries_with_rebatched_and_inspected_lots_stay_a_page(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.inventory.tracking import held_because_page
        from apps.manufacturing.tests_bag_counts import IN

        def build(n, start=0):
            lots = []
            for k in range(start, start + n):
                lot = self.stocked(f"R-{k:03d}")
                self.inspect(lot, IN)
                lots.append(lot)
            return lots

        small = build(8)
        with CaptureQueriesContext(connection) as few:
            held_because_page(small)
        big = small + build(32, 8)
        with CaptureQueriesContext(connection) as many:
            held_because_page(big)
        print(f"\nG4 held_because_page inspected: {len(few)} queries for 8, {len(many)} for 40")
        self.assertLessEqual(len(many), len(few) + 2)
