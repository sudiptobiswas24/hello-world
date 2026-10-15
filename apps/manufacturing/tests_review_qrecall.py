"""
The review of decision 23 (O204, O207, O123), kept as regression tests.

Each test states a fact that must stay true of stock or state; written as
probes by the review of g21 (findings F1 to F8), all passing since the fixes.
"""

import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.inventory.models import Lot, MovementType, StockMovement, Warehouse
from apps.quality.models import Disposition, Inspection, InspectionPlan, ReleaseStatus
from apps.quality.release import release_status, why_held
from apps.sales.models import DeliveryLine

from . import certificates, tests_process_rolls, tests_recall
from .rebatch import rebatch
from .tests_bag_counts import IN, OUT
from .tests_orders import TODAY, RunTestCase


class Base(tests_recall.RecallTestCase):
    def setUp(self):
        super().setUp()
        user = User.objects.create_user("farid")
        user.groups.add(Group.objects.get(name="Production Supervisor"))
        self.clients["farid"] = APIClient()
        self.clients["farid"].force_authenticate(user)

    def api_void(self, who, inspection, reason="typo"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.clients[who].post(f"/api/quality/inspections/{inspection.pk}/void/",
                                          {"reason": reason}, format="json")


class WhoMay(Base):
    def test_an_inspector_does_not_lift_a_recall_by_voiding_the_source_rejection(self):
        """Decision 22/23: the Quality Manager owns the hold. Meera voids B1's rejection: 200, A and B read released."""
        rejection = self.reject_b1()
        recalls = [self.recall_of(self.a), self.recall_of(self.b)]
        response = self.api_void("meera", rejection)
        released = [release_status(lot) for lot in (self.a, self.b)]
        self.assertGreaterEqual(
            response.status_code, 400,
            f"Inspector voided {rejection.number}: {response.status_code}; lots now {released}; "
            f"recalls voided: {[Inspection.objects.get(pk=r.pk).voided_at is not None for r in recalls]}; "
            f"to-dos closed: {[self.to_dos(r, done_on__isnull=True).count() == 0 for r in recalls]}")

    def test_an_inspector_may_still_void_a_pass_which_leaves_the_lot_uninspected_and_held(self):
        """Review F2: voiding a pass lifts nothing; RB-A's own pass voided, it reads as B1, not released."""
        rejection = self.reject_b1()
        response = self.api_void("meera", self.own_pass, reason="Wrong roll measured")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)
        # ...and the Quality Manager still has the hold before them: the recall stands or is filed again.
        self.assertTrue(Inspection.objects.filter(lot=self.a, recalled_by__isnull=False,
                                                  voided_at__isnull=True).exists(), rejection.number)

    def test_the_quality_manager_voids_the_rejection_and_the_recalls_lift(self):
        rejection = self.reject_b1()
        self.assertEqual(self.api_void("asha", rejection, reason="Scale out").status_code, 200)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.RELEASED] * 2)

    def test_a_supervisor_or_the_stores_do_not_void_the_rejection(self):
        rejection = self.reject_b1()
        for who in ("farid", "sunil"):
            self.assertIn(self.api_void(who, rejection).status_code, (400, 403), who)
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)

    def test_an_inspector_does_not_void_an_open_recall(self):
        self.reject_b1()
        recall = self.recall_of(self.a)
        response = self.api_void("meera", recall)
        self.assertIn(response.status_code, (400, 403), response.content)
        self.assertIsNone(Inspection.objects.get(pk=recall.pk).voided_at)

    def test_stores_manager_and_supervisor_cannot_decide_or_void_a_recall(self):
        self.reject_b1()
        recall = self.recall_of(self.a)
        for who in ("sunil", "farid"):
            decided = self.decide(recall, who, Disposition.CONCESSION)
            self.assertIn(decided.status_code, (400, 403), (who, decided.content))
            voided = self.api_void(who, recall)
            self.assertIn(voided.status_code, (400, 403), (who, voided.content))
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)

    def test_the_quality_manager_voiding_a_recall_while_the_source_stands_leaves_the_lot_held(self):
        self.reject_b1()
        recall = self.recall_of(self.a)
        self.assertEqual(self.api_void("asha", recall).status_code, 200)
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)
        # ...and the lot is not left with nothing that can decide it.
        self.assertTrue(
            Inspection.objects.filter(lot=self.a, recalled_by__isnull=False, voided_at__isnull=True).exists(),
            "recall voided by the Quality Manager: the lot is held by derivation and no inspection waits "
            f"for a decision, yet why_held says: {why_held(self.bag, self.a, warehouse=self.plant)}")


class Certificate(Base):
    def test_a_certificate_does_not_read_passed_for_a_lot_the_gate_holds(self):
        delivery = DeliveryLine.objects.get(lot=self.a).delivery
        self.reject_b1()
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)
        try:
            certificate = certificates.issue(delivery)
        except ValidationError:
            return
        said = json.dumps(certificate.content)
        self.assertIn("recall", said.lower(),
                      "certificate issued for a recalled lot (open recall, undecided) with no word of it: "
                      + json.dumps(certificate.content["lines"][0]["batches"][0].get("status")))

    def test_lot_status_names_the_inspection_that_holds_it(self):
        self.reject_b1()
        row = self.clients["meera"].get(f"/api/quality/lot-status/{self.a.pk}/").json()
        self.assertEqual(row["status"], "held")
        self.assertNotEqual(
            (row["disposition"], row["result"]), ("accept", "pass"),
            f"lot-status says held and gives the inspection {row['inspection']} as accepted/pass")


class AHoldThatArrivesWhileTheLotIsAlreadyHeld(Base):
    def test_a_second_source_rejected_while_the_recall_is_open_is_named_before_the_lot_is_released(self):
        joined = self.new("RB-J")
        rebatch(self.bag, self.plant, [(self.b2, "100"), (self.b3, "100")], [(joined, "200")],
                "Short ends", on_date=TODAY)
        self.inspect(joined, IN)
        first = self.inspect(self.b2, OUT)
        recall = self.recall_of(joined)
        second = self.inspect(self.b3, OUT)
        self.assertEqual(Inspection.objects.filter(lot=joined, recalled_by__isnull=False).count(), 1)
        self.assertEqual(self.decide(recall, "asha", Disposition.CONCESSION).status_code, 200)
        told = " ".join(todo.note + todo.summary for todo in FollowUp.objects.filter(
            content_type=ContentType.objects.get_for_model(Inspection)))
        self.assertTrue(
            release_status(joined) != ReleaseStatus.RELEASED or second.number in told,
            f"RB-J read RELEASED after the Quality Manager took {recall.number} as is; {self.b3} "
            f"stands rejected ({second.number}, posted before the decision) and no to-do, recall or "
            f"note names it ({first.number} is the one they were told of)")

    def test_a_lot_awaiting_its_own_decision_is_not_released_by_it_with_a_source_rejected_inside(self):
        InspectionPlan.objects.filter(pk=self.bag_spec.inspection_plan.pk).update(is_mandatory=False)
        own_fail = self.inspect(self.a, OUT)  # advisory: awaits the Quality Manager
        self.assertEqual(own_fail.disposition, Disposition.AWAITING)
        source_fail = self.inspect(self.b1, OUT)  # B1 awaits too
        self.assertEqual(self.decide(source_fail, "asha", Disposition.REJECT).status_code, 200)
        self.assertEqual(self.decide(own_fail, "asha", Disposition.CONCESSION).status_code, 200)
        self.assertNotEqual(
            release_status(self.a), ReleaseStatus.RELEASED,
            f"{self.a} released by the concession on {own_fail.number} while {self.b1}, inside it, stands "
            f"rejected ({source_fail.number}); nobody was told of it ({self.a} was already held, so no recall)")


class TheLiftRacingANewVerdict(Base):
    def test_a_rejection_posted_between_the_lifts_check_and_its_void_is_still_recalled(self):
        """
        The lift (on_commit after the first rejection's void) asks recall_stands() and then voids. A second
        rejection of B1 posted by another request in between finds the old recall open and tells no one;
        the lift then voids that recall. Emulated in one thread: the second post is made from inside the
        lift's own check, after its answer is known.
        """
        from apps.quality import recall as recall_module

        rejection = self.reject_b1()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            rejection.void("typo")
        original, posted = recall_module.recall_stands, []

        def interleaved(recall_of):
            answer = original(recall_of)
            if not answer and not posted:
                posted.append(self.inspect(self.b1, OUT))
            return answer

        recall_module.recall_stands = interleaved
        try:
            for callback in callbacks:
                callback()
        finally:
            recall_module.recall_stands = original
        self.assertTrue(posted, "the interleaving did not happen")
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)
        self.assertTrue(
            Inspection.objects.filter(lot=self.a, recalled_by__isnull=False, voided_at__isnull=True).exists(),
            f"{posted[0].number} holds {self.a} and {self.b} but their recalls were voided by the lift: "
            "nobody is told and nothing waits for a decision")


class PastTheDepth(Base):
    def test_a_lot_held_past_the_walk_has_a_recall_to_decide(self):
        taken = "150"
        previous, chain = self.b1, []
        for level, code in enumerate(("C1", "C2", "C3", "C4", "C5")):
            keep = str(int(taken) - 20)
            # One into one is refused as the same batch: a 20-bag offcut makes the second.
            _, (lot, _offcut) = self.split(taken=taken, rows=((code, keep), (code + "x", "20")), source=previous)
            chain.append(lot)
            previous, taken = lot, keep
        c5 = chain[-1]
        self.inspect(c5, IN)
        self.reject_b1()
        self.assertEqual(release_status(c5), ReleaseStatus.HELD)
        self.assertTrue(
            Inspection.objects.filter(lot=c5, recalled_by__isnull=False, voided_at__isnull=True).exists(),
            f"{c5} (level 5) is held, why_held says: {why_held(self.bag, c5, warehouse=self.plant)!r}; "
            "no recall is filed and decide() has nothing to act on")


class TheColumn(Base):
    def test_the_cost_of_the_column_does_not_multiply_by_the_places_a_lot_is_on_hand(self):
        from apps.inventory.tracking import held_because

        self.reject_b1()
        with CaptureQueriesContext(connection) as one:
            said = held_because(self.a)
        self.assertTrue(said)
        for number in range(4):
            place = Warehouse.objects.create(code=f"W{number}", name=f"Store {number}")
            StockMovement.objects.create(item=self.bag, warehouse=place, movement_type=MovementType.RECEIPT,
                                         uom=self.bag.uom, quantity=Decimal("5"), unit_cost=Decimal("12"),
                                         lot=self.a, occurred_at=datetime.datetime(2026, 6, 1, 8, 0,
                                                                                    tzinfo=datetime.timezone.utc))
        with CaptureQueriesContext(connection) as five:
            held_because(self.a)
        print(f"\nQ10 held_because(A): {len(one)} queries in 1 place, {len(five)} in 5")
        self.assertLessEqual(len(five), 2 * len(one), f"{len(one)} queries in one place, {len(five)} in five")

    def test_the_other_lists_of_lots_on_hand_say_why_too(self):
        self.reject_b1()
        Lot.objects.filter(pk=self.a.pk).update(expires_on=datetime.date(2027, 1, 1))
        rows = self.clients["sunil"].get("/api/inventory/lots/expiring/", {"before": "2030-01-01"}).json()
        mine = [row for row in rows if row["lot"] == self.a.code]
        self.assertTrue(mine, rows)
        self.assertIn("held_because", mine[0], f"expiring lists {self.a} on hand, held, without saying why: {mine[0]}")


class ALoopThroughRuns(RunTestCase):
    """Regrind hoppers alternating: run A eats RG-1 and makes RG-2 as a by-product; run B eats RG-2 and makes RG-1."""

    def run_of(self, eats, makes):
        order = self.order()
        order.release(TODAY)
        self.issue_with_lots(order, [(self.regrind, "15", eats)]).post()
        entry = self.produce(order, "100", byproducts=[(self.regrind, "2")])
        entry.byproducts.update(lot=makes)
        entry.post()
        return order

    def test_two_lots_that_feed_each_other_through_runs_do_not_crash_the_gate(self):
        from apps.inventory.tracking import held_because

        from apps.inventory.models import Item

        Item.objects.filter(pk=self.regrind.pk).update(tracking="lot")
        self.regrind.refresh_from_db()
        rg1, rg2 = (Lot.objects.create(item=self.regrind, code=code) for code in ("RG-1", "RG-2"))
        for lot in (rg1, rg2):
            self.stock(self.regrind, "50", "60", lot=lot)
        self.run_of(eats=rg1, makes=rg2)
        self.run_of(eats=rg2, makes=rg1)
        for lot in (rg1, rg2):
            try:
                release_status(lot)
                why_held(self.regrind, lot, warehouse=self.plant)
                held_because(lot)
            except RecursionError:
                self.fail(f"{lot}: RecursionError in the release gate (a loop of lots through two runs); "
                          "the Batches list, every issue and every delivery that asks it crash")


class ALoopOfThreeThroughRuns(ALoopThroughRuns):
    """RG-1 into a run that makes RG-2, RG-2 into one that makes RG-3, RG-3 into one that makes RG-1."""

    def test_three_lots_that_feed_each_other_through_three_runs_do_not_crash_the_gate(self):
        from apps.inventory.models import Item
        from apps.inventory.tracking import held_because

        from .trace import descendants

        Item.objects.filter(pk=self.regrind.pk).update(tracking="lot")
        self.regrind.refresh_from_db()
        lots = [Lot.objects.create(item=self.regrind, code=f"RG-{n}") for n in (1, 2, 3)]
        for lot in lots:
            self.stock(self.regrind, "50", "60", lot=lot)
        for eats, makes in zip(lots, lots[1:] + lots[:1]):
            self.run_of(eats=eats, makes=makes)
        for lot in lots:
            self.assertEqual(release_status(lot), ReleaseStatus.UNINSPECTED)
            self.assertEqual(why_held(self.regrind, lot, warehouse=self.plant), "")
            self.assertEqual(held_because(lot), "")
            # The trace walks the same loop once round, by the same walk.
            self.assertEqual(sorted({row["lot"].code for row in descendants(lot, depth=10)}),
                             ["RG-1", "RG-2", "RG-3"])


class Escalation(Base):
    def test_a_recall_whose_source_no_longer_holds_is_voided_not_escalated(self):
        rejection = self.reject_b1()
        recall = self.recall_of(self.a)
        rejection.void("Scale out")  # in code: the lift has not run
        root = User.objects.create_superuser("root", "root@example.com", "x")
        recall = Inspection.objects.get(pk=recall.pk)
        told = recall.escalate(timezone.localdate() + datetime.timedelta(days=10))
        recall.refresh_from_db()
        self.assertIsNone(told)
        self.assertIsNotNone(recall.voided_at)
        self.assertFalse(FollowUp.objects.filter(assigned_to=root).exists())

    def test_a_recall_that_stands_is_escalated_once_to_the_administrator(self):
        from apps.quality.decisions import escalate_overdue

        self.reject_b1()
        User.objects.create_superuser("root", "root@example.com", "x")
        later = timezone.localdate() + datetime.timedelta(days=10)
        first = [pair[0].lot.code for pair in escalate_overdue(later)]
        again = escalate_overdue(later)
        self.assertEqual(sorted(first), sorted([self.a.code, self.b.code]))
        self.assertEqual(again, [])

    def test_a_rejection_voided_and_posted_again_leaves_one_recall_and_one_open_to_do_a_lot(self):
        rejection = self.reject_b1()
        self.api_void("asha", rejection)
        self.reject_b1()
        for lot in (self.a, self.b):
            recalls = Inspection.objects.filter(lot=lot, recalled_by__isnull=False, voided_at__isnull=True)
            self.assertEqual(recalls.count(), 1)
            open_to_dos = sum(self.to_dos(r, done_on__isnull=True).count() for r in Inspection.objects.filter(
                lot=lot, recalled_by__isnull=False))
            self.assertEqual(open_to_dos, 1, lot)


class AnOffRollOrCheckThatIsWithdrawn(tests_process_rolls.RollsTestCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.manager = User.objects.create_user("asha")
        self.manager.groups.add(Group.objects.get(name="Quality Manager"))

    def open_to_dos(self, record):
        return FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(type(record)),
                                       object_id=record.pk, done_on__isnull=True).count()

    def test_the_to_do_for_a_roll_the_supervisor_withdrew_is_closed(self):
        self.mount()
        roll = self.laminated("132.78", supervisor=self.supervisor, reason="Die lip cleaned")
        self.assertEqual(self.open_to_dos(roll), 1)
        tests_process_rolls.void_roll(roll, self.kx, self.supervisor, self.operator, "Wrong roll")
        self.assertEqual(self.open_to_dos(roll), 0,
                         "the Quality Manager is still asked to look at a roll record that was withdrawn")

    def test_the_to_do_for_a_roll_is_closed_when_the_bundle_cut_from_it_is_decided(self):
        from .conversion import record_bags
        from .tests_station import at

        self.mount()
        roll = self.laminated("132.78", supervisor=self.supervisor, reason="Die lip cleaned")
        tests_process_rolls.mount_roll(self.cv, self.operator, self.c2, roll.code, at=at(TODAY, 11, 30))
        grams = str(self.lam_spec.bag_grams().quantize(Decimal("0.001")))
        count = record_bags(self.cv, self.operator, self.c2, "500", [grams] * 10, at=at(TODAY, 12))
        count.inspection.decide(self.manager, Disposition.REJECT, "Sent for regrind")
        self.assertEqual(self.open_to_dos(roll), 0, "decided, and the roll's to-do still stands open")

    def test_the_to_do_for_a_coating_check_the_supervisor_withdrew_is_closed(self):
        from .station_coat import void_coating
        from .tests_station_coat import OFF

        check = self.check(samples=OFF, supervisor=self.supervisor, reason="Die lip cleaned")
        self.assertEqual(self.open_to_dos(check), 1)
        void_coating(check, self.kx, self.supervisor, self.operator, "Wrong disc")
        self.assertEqual(self.open_to_dos(check), 0)
