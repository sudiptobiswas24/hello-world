"""
A recall (RISKS.md, "Decisions taken", 23; O207).

Bundle B1 of 500 bags passed at its count. 300 of it were re-made into
RB-A (200) and RB-B (100); B1 keeps 200. RB-A passed its own inspection,
and 150 of it went on a delivery: 50 are left. Then B1 is rejected. RB-A's
pass predates what is now known about what is inside it, so the 50 left
are held, RB-B's 100 too, and the Quality Manager is told once a lot,
naming where it is and what has gone. Figures in
scratchpad/calc_fix_qrecall/scenarios.py.

Refusals first; then what is done, and each reverse.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.quality.models import Disposition, Inspection, Reading, ReleaseStatus
from apps.quality.release import release_status, why_held
from apps.sales.models import DeliveryLine

from .tests_bag_counts import IN, OUT
from .tests_orders import TODAY
from .tests_rebatch import RebatchTestCase


class RecallTestCase(RebatchTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.clients = {}
        for username, role, number in (("asha", "Quality Manager", "EMP-0300"),
                                       ("meera", "Quality Inspector", "EMP-0301"),
                                       ("sunil", "Stores Manager", None)):
            user = User.objects.create_user(username)
            user.groups.add(Group.objects.get(name=role))
            if number is not None:
                employee = self.employee(number, username.title())
                employee.user = user
                employee.save()
            self.clients[username] = APIClient()
            self.clients[username].force_authenticate(user)
        _, (self.a, self.b) = self.split()
        self.own_pass = self.inspect(self.a, IN)
        delivery = self.delivery("150")
        DeliveryLine.objects.create(delivery=delivery, order_line=delivery.sales_order.lines.get(),
                                    warehouse=self.plant, lot=self.a, quantity_shipped=Decimal("150"))
        delivery.post()
        self.delivery_number = delivery.number

    def inspect(self, lot, values, who="meera"):
        plan = self.bag_spec.inspection_plan
        inspection = Inspection.objects.create(lot=lot, plan=plan, inspected_on=TODAY)
        line = plan.lines.get()
        for number, value in enumerate(values[:line.sample_size], start=1):
            Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal(value),
                                   sample_reference=f"S{number}")
        response = self.clients[who].post(f"/api/quality/inspections/{inspection.pk}/post/")
        self.assertEqual(response.status_code, 200, response.content)
        inspection.refresh_from_db()
        return inspection

    def reject_b1(self):
        """B1 measured again and off weight: the mandatory plan rejects it."""
        rejection = self.inspect(self.b1, OUT)
        self.assertEqual(rejection.disposition, Disposition.REJECT)
        return rejection

    def recall_of(self, lot):
        return Inspection.objects.get(lot=lot, recalled_by__isnull=False, voided_at__isnull=True,
                                      disposition=Disposition.AWAITING)

    def to_dos(self, record, **filters):
        return FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(Inspection),
                                       object_id=record.pk, **filters)

    def decide(self, inspection, who, disposition, note="Customer takes them for a liner."):
        response = self.clients[who].post(f"/api/quality/inspections/{inspection.pk}/decide/",
                                          {"disposition": disposition, "note": note}, format="json")
        inspection.refresh_from_db()
        return response


class RefusedTests(RecallTestCase):
    def test_an_inspector_does_not_decide_a_recall(self):
        self.reject_b1()
        response = self.decide(self.recall_of(self.a), "meera", Disposition.CONCESSION)
        self.assertEqual(response.status_code, 403, response.content)

    def test_no_second_verdict_beside_an_open_recall(self):
        self.reject_b1()
        inspection = Inspection.objects.create(lot=self.a, plan=self.bag_spec.inspection_plan,
                                               inspected_on=TODAY)
        line = self.bag_spec.inspection_plan.lines.get()
        for number in range(line.sample_size):
            Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal(IN[0]),
                                   sample_reference=f"S{number}")
        response = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/post/")
        self.assertEqual(response.status_code, 400)
        self.assertIn(f"{self.recall_of(self.a).number} recalled {self.a}", str(response.content))

    def test_a_recall_whose_source_rejection_was_voided_has_nothing_to_decide(self):
        rejection = self.reject_b1()
        recall = self.recall_of(self.a)
        # Voided in code, inside this test's transaction: the lift waits for a commit that never
        # comes here, so the recall is still open and its decision is asked.
        rejection.void("Scale out")
        response = self.decide(recall, "asha", Disposition.REJECT)
        self.assertEqual(response.status_code, 400)
        self.assertIn("nothing to decide", str(response.content))


class HeldTests(RecallTestCase):
    def test_what_is_left_of_a_lot_that_passed_is_held(self):
        self.assertEqual(release_status(self.a), ReleaseStatus.RELEASED)
        rejection = self.reject_b1()
        self.assertEqual([release_status(lot) for lot in (self.b1, self.a, self.b)],
                         [ReleaseStatus.HELD] * 3)
        said = why_held(self.bag, self.a, warehouse=self.plant, action="be shipped")
        self.assertIn(f"{self.a} is held: made from {self.b1}, rejected on 1 June 2026 "
                      f"({rejection.number}), after its own release ({self.own_pass.number})", said)
        self.assertIn(f"The Quality Manager decides it ({self.recall_of(self.a).number})", said)
        # Nothing shipped is touched: the delivery stands, 50 are left.
        self.assertEqual((self.a.on_hand_at(self.plant), self.b.on_hand_at(self.plant)),
                         (Decimal("50"), Decimal("100")))

    def test_the_quality_manager_is_told_once_a_lot_with_where_it_is_and_what_has_gone(self):
        self.reject_b1()
        recall = self.recall_of(self.a)
        (to_do,) = self.to_dos(recall)
        self.assertEqual(to_do.assigned_to.username, "asha")
        self.assertIn(f"In stock: 50 {self.bag.uom.code} in {self.plant}.", to_do.note)
        self.assertIn(f"Already gone, not touched: 150 {self.bag.uom.code} on {self.delivery_number}.",
                      to_do.note)
        self.assertEqual(self.to_dos(self.recall_of(self.b)).count(), 1)
        # One recall a lot made from B1, and none of B1 itself or of what was not made from it.
        self.assertEqual(sorted(Inspection.objects.filter(recalled_by__isnull=False)
                                .values_list("lot__code", flat=True)), ["RB-A", "RB-B"])

    def test_the_stock_list_says_why_each_batch_is_held(self):
        rejection = self.reject_b1()
        rows = {row["code"]: row["held_because"] for row in
                self.clients["sunil"].get("/api/inventory/lots/", {"item": self.bag.pk}).json()}
        self.assertIn(f"made from {self.b1}, rejected on 1 June 2026 ({rejection.number})", rows["RB-A"])
        self.assertIn("It cannot go until then.", rows["RB-A"])
        self.assertIn(f"{self.b1} is held: rejected on 2026-06-01 ({rejection.number})", rows[self.b1.code])
        self.assertEqual(rows[self.b2.code], "")


class DecidedTests(RecallTestCase):
    def test_taken_as_is_by_the_quality_manager_it_goes_and_the_to_do_closes(self):
        self.reject_b1()
        recall = self.recall_of(self.a)
        response = self.decide(recall, "asha", Disposition.CONCESSION)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(release_status(self.a), ReleaseStatus.RELEASED)
        self.assertEqual(self.to_dos(recall, done_on__isnull=True).count(), 0)
        # A recalled lot the Quality Manager released: a later hold of what is inside it holds it again.
        Inspection.objects.filter(lot=self.b1).exclude(recalled_by__isnull=False).filter(
            disposition=Disposition.REJECT).get().void("Scale out")
        self.reject_b1()
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)
        self.assertNotEqual(self.recall_of(self.a).pk, recall.pk)

    def test_rejected_by_the_quality_manager_it_stays_held_whatever_b1_reads_after(self):
        rejection = self.reject_b1()
        self.assertEqual(self.decide(self.recall_of(self.a), "asha", Disposition.REJECT).status_code, 200)
        rejection.void("Scale out")
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD)


class LiftedTests(RecallTestCase):
    def test_the_rejection_voided_each_lot_reads_as_before_and_its_recall_closes(self):
        rejection = self.reject_b1()
        recalls = [self.recall_of(self.a), self.recall_of(self.b)]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.clients["asha"].post(f"/api/quality/inspections/{rejection.pk}/void/",
                                                 {"reason": "Scale out"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.RELEASED] * 2)
        for recall in recalls:
            recall.refresh_from_db()
            self.assertIsNotNone(recall.voided_at)
            self.assertEqual(self.to_dos(recall, done_on__isnull=True).count(), 0)

    def test_b1_passed_again_after_rework_the_same(self):
        """
        The Quality Manager put B1 on rework, so its re-inspection is what they asked for: the
        Inspector's pass of it lifts the recalls (rereview 1). Until rereview this test had the
        Inspector pass B1 over a plain rejection, with no rework at all: the very door it closes.
        """
        from apps.quality.models import InspectionPlan

        InspectionPlan.objects.filter(pk=self.bag_spec.inspection_plan.pk).update(is_mandatory=False)
        failed = self.inspect(self.b1, OUT)
        self.assertEqual(failed.disposition, Disposition.AWAITING)
        self.assertEqual(self.decide(failed, "asha", Disposition.REWORK).status_code, 200)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.HELD] * 2)
        with self.captureOnCommitCallbacks(execute=True):
            self.inspect(self.b1, IN)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.RELEASED] * 2)
        self.assertFalse(Inspection.objects.filter(recalled_by__isnull=False, voided_at__isnull=True).exists())

    def test_a_recalled_lot_split_while_held_its_pieces_stay_held(self):
        self.reject_b1()
        _, (piece, _) = self.split(taken="50", rows=(("RB-A1", "30"), ("RB-A2", "20")), source=self.a)
        self.assertEqual(release_status(piece), ReleaseStatus.HELD)
        self.assertEqual(self.decide(self.recall_of(self.a), "asha", Disposition.CONCESSION).status_code, 200)
        self.assertEqual(release_status(piece), ReleaseStatus.RELEASED)


class NoPlanToFileUnderTests(RecallTestCase):
    def test_a_lot_with_no_plan_is_held_and_the_quality_manager_is_told_so(self):
        """Follow-up 5: RB-B has no inspection of its own and its item no plan in force today."""
        import datetime

        from apps.quality.models import InspectionPlan

        InspectionPlan.objects.filter(pk=self.bag_spec.inspection_plan.pk).update(
            valid_to=datetime.date(2026, 10, 1))
        rejection = self.reject_b1()
        self.assertEqual(release_status(self.b), ReleaseStatus.HELD)
        self.assertFalse(Inspection.objects.filter(lot=self.b, recalled_by__isnull=False).exists())
        (to_do,) = self.to_dos(rejection)
        self.assertEqual(to_do.assigned_to.username, "asha")
        self.assertIn("no inspection plan to file one under", to_do.note)
        self.assertIn(f"{self.b} (100 {self.bag.uom.code} in {self.plant})", to_do.note)
