"""
rereview (gate g21): the recall review fixes (8c5db57, d7242d8, a550ec6, d2d336c) asked what they broke or left.
Each test states a fact about stock or state; PASS is the fact holding on the tip, a failure is a finding.
Run: PYTHONPATH=scripts/gate .venv/bin/python manage.py test apps.manufacturing.tests_probe_rereview --settings=fastsettings
"""

import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client

from apps.core.chatter import FollowUp
from apps.quality import release
from apps.quality.models import Disposition, Inspection, Reading, ReleaseStatus
from apps.quality.release import release_status, why_held

from .rebatch import rebatch
from .tests_bag_counts import IN, OUT
from .tests_orders import TODAY
from .tests_review_qrecall import Base


class WhoLiftsAHold(Base):
    """'Only the Quality Manager lifts a hold': every path that lifts one."""

    def states(self):
        return {
            "status": [release_status(lot).value if hasattr(release_status(lot), "value") else release_status(lot)
                       for lot in (self.a, self.b)],
            "recalls_open": Inspection.objects.filter(recalled_by__isnull=False, voided_at__isnull=True,
                                                      disposition=Disposition.AWAITING).count(),
            "todos_open": FollowUp.objects.filter(
                content_type=ContentType.objects.get_for_model(Inspection), done_on__isnull=True).count(),
        }

    def test_P1_an_inspector_posting_a_fresh_pass_of_the_rejected_batch_does_not_lift_the_recall(self):
        """The F2 door by another way: B1 stands rejected (mandatory plan), meera posts a pass of B1 the same day."""
        rejection = self.reject_b1()
        before = self.states()
        plan = self.bag_spec.inspection_plan
        line = plan.lines.get()
        draft = Inspection.objects.create(lot=self.b1, plan=plan, inspected_on=TODAY)
        for n, value in enumerate(IN[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value), sample_reference=f"S{n}")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.clients["meera"].post(f"/api/quality/inspections/{draft.pk}/post/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(f"{rejection.number} holds {self.b1} (rejected)", response.json()[0])
        self.assertIn("the Quality Manager posts the new verdict", response.json()[0])
        self.assertEqual(self.states(), before,
                         "an Inspector's fresh pass of B1 lifted the Quality Manager's recall")
        # The Quality Manager's own pass of it lifts them.
        with self.captureOnCommitCallbacks(execute=True):
            self.inspect(self.b1, IN, who="asha")
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.RELEASED] * 2)

    def test_P2_an_inspector_posting_a_failing_reading_as_a_concession_does_not_lift_the_recall(self):
        """The concession path: meera posts B1's off-weight readings with disposition=concession and a note."""
        self.reject_b1()
        plan = self.bag_spec.inspection_plan
        line = plan.lines.get()
        draft = Inspection.objects.create(lot=self.b1, plan=plan, inspected_on=TODAY,
                                          disposition=Disposition.CONCESSION, decision_note="Customer takes them")
        for n, value in enumerate(OUT[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value), sample_reference=f"S{n}")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.clients["meera"].post(f"/api/quality/inspections/{draft.pk}/post/")
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD,
                         f"meera's concession answered {response.status_code}; now {self.states()}")

    def test_P3_the_admin_void_action_does_not_lift_a_rejection_for_a_staff_inspector(self):
        """admin.py _act calls row.void(reason) with by=None, so void() never asks for decide_inspection."""
        rejection = self.reject_b1()
        staff = User.objects.create_user("adm", password="x", is_staff=True)
        staff.groups.add(Group.objects.get(name="Quality Inspector"))
        client = Client()
        client.force_login(staff)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/admin/quality/inspection/",
                                   {"action": "action_void", "_selected_action": [rejection.pk]}, follow=True)
        rejection.refresh_from_db()
        self.assertIsNone(rejection.voided_at,
                          f"a staff Inspector voided {rejection.number} from the admin ({response.status_code}); "
                          f"now {self.states()}")

    def test_P4_an_inspectors_fresh_pass_does_not_overturn_the_quality_managers_reject_of_a_recall(self):
        """QM rejects RB-A's recall; the source rejection is later voided by the QM; meera passes RB-A."""
        rejection = self.reject_b1()
        recall = self.recall_of(self.a)
        self.assertEqual(self.decide(recall, "asha", Disposition.REJECT).status_code, 200)
        self.assertEqual(self.api_void("asha", rejection, reason="Scale out").status_code, 200)
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD, "the QM's reject stands (author's test)")
        plan = self.bag_spec.inspection_plan
        line = plan.lines.get()
        from django.utils import timezone

        draft = Inspection.objects.create(lot=self.a, plan=plan, inspected_on=timezone.localdate())
        for n, value in enumerate(IN[:line.sample_size], start=1):
            Reading.objects.create(inspection=draft, plan_line=line, value=Decimal(value), sample_reference=f"S{n}")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.clients["meera"].post(f"/api/quality/inspections/{draft.pk}/post/")
        self.assertEqual(release_status(self.a), ReleaseStatus.HELD,
                         f"RB-A was rejected by the Quality Manager's decision; meera's fresh pass answered "
                         f"{response.status_code} and it reads {release_status(self.a)}")

    def test_P5_an_inspector_voiding_a_pass_that_lifted_the_hold_puts_the_hold_and_the_recall_back(self):
        """Legitimate path 1 (regression check): the Inspector's correction of her own typed pass."""
        self.reject_b1()
        typed = self.inspect(self.b1, IN, who="asha")  # the Quality Manager lifts it (see P1) ...
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api_void("meera", typed, reason="typed against the wrong bundle")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.HELD] * 2)
        self.assertEqual(Inspection.objects.filter(lot=self.a, recalled_by__isnull=False,
                                                   voided_at__isnull=True).count(), 1,
                         "one recall waits for the Quality Manager again")

    def test_P6_the_inspector_can_still_void_her_own_typed_pass_on_a_plain_lot(self):
        typed = self.inspect(self.b2, IN)
        response = self.api_void("meera", typed, reason="typo")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(release_status(self.b2), ReleaseStatus.RELEASED)

    def test_P8_the_quality_manager_can_void_a_mistaken_reject_typed_by_an_inspector_and_lots_lift(self):
        rejection = self.reject_b1()
        response = self.api_void("asha", rejection, reason="Scale out")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([release_status(lot) for lot in (self.a, self.b)], [ReleaseStatus.RELEASED] * 2)
        self.assertFalse(Inspection.objects.filter(recalled_by__isnull=False, voided_at__isnull=True).exists())

    def test_P9_an_inspector_voiding_a_mistaken_reject_of_a_lot_nothing_was_made_from_is_refused(self):
        """The cost of the F2 rule on the plain case: meera's own typo reject of B2 (nothing made from it)
        needs the Quality Manager. Recorded: PASS if refused (rule as decided), FAIL if allowed."""
        typed = self.inspect(self.b2, OUT)
        response = self.api_void("meera", typed, reason="typo")
        self.assertIn(response.status_code, (400, 403), response.content)


class TheRecallsOwnWords(Base):
    def test_P10_a_recall_voided_by_the_quality_manager_while_two_sources_hold_is_filed_again_naming_both(self):
        joined = self.new("RB-J")
        rebatch(self.bag, self.plant, [(self.b2, "100"), (self.b3, "100")], [(joined, "200")],
                "Short ends", on_date=TODAY)
        self.inspect(joined, IN)
        first = self.inspect(self.b2, OUT)
        second = self.inspect(self.b3, OUT)
        old = self.recall_of(joined)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.api_void("asha", old, reason="filed in error").status_code, 200)
        new = self.recall_of(joined)
        told = " ".join(t.note + t.summary for t in FollowUp.objects.filter(
            content_type=ContentType.objects.get_for_model(Inspection), object_id=new.pk, done_on__isnull=True))
        self.assertNotEqual(new.pk, old.pk)
        self.assertTrue(first.number in told + new.notes and second.number in told + new.notes,
                        f"the new recall {new.number} names: {new.notes!r}; to-do: {told!r}; "
                        f"two sources hold the lot ({first.number}, {second.number})")


class TheGateOnALoopWithAHeldInput(Base):
    """a550ec6's stack cut: a loop of lots (provider injected) with a rejected input outside it."""

    def with_graph(self, graph):
        original = list(release.LOT_SOURCES)

        def provider(lot):
            return list(graph.get(lot.pk, []))

        release.LOT_SOURCES.append(provider)
        return original

    def test_P11_a_loop_with_a_rejected_input_reads_held_whichever_lot_is_asked_first_and_in_one_question(self):
        self.reject_b1()
        l1, l2, l3 = self.new("LP-1"), self.new("LP-2"), self.new("LP-3")
        graph = {l1.pk: [l2, self.b1], l2.pk: [l1], l3.pk: [l2]}
        original = self.with_graph(graph)
        try:
            for order in ((l1, l2, l3), (l2, l1, l3), (l3, l2, l1)):
                got = [release_status(lot) for lot in order]
                self.assertEqual(got, [ReleaseStatus.HELD] * 3, f"asked {[l.code for l in order]} separately: {got}")
                with release.one_question():
                    got = [release_status(lot) for lot in order]
                    again = [release_status(lot) for lot in reversed(order)]
                self.assertEqual(got + again, [ReleaseStatus.HELD] * 6,
                                 f"asked {[l.code for l in order]} in one question: {got} then {again}")
                self.assertTrue(why_held(self.bag, order[0], warehouse=self.plant))
        finally:
            release.LOT_SOURCES[:] = original

    def test_P12_a_loop_of_never_inspected_lots_cannot_be_made_by_rebatch(self):
        """
        The cut answers a lot met again as RELEASED, so a loop whose members have no verdict and no held input
        reads RELEASED (an injected provider shows it). Reachable only if a lot can be re-made from what it was
        itself the source of: rebatch refuses an output lot that held stock before, and the run lineage returns
        held inputs only. Recorded as the guard that keeps it unreachable.
        """
        from django.core.exceptions import ValidationError

        self.b3.inspections.get().void("Scale out")
        held = self.b3.on_hand_at(self.plant)
        x, y = self.new("LOOP-X"), self.new("LOOP-Y")
        rebatch(self.bag, self.plant, [(self.b3, str(held))], [(x, str(held - 10)), (y, "10")], "repack", on_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "has held stock before"):
            rebatch(self.bag, self.plant, [(x, str(held - 10)), (y, "10")], [(self.b3, str(held))], "back",
                    on_date=TODAY)


class TraceRecallShape(Base):
    """Item 5: the trace's recall report on a plain tree, and on a chain past its depth. Printed for a diff."""

    def snapshot(self, lot, depth=None):
        from .trace import recall

        found = recall(lot, depth) if depth is not None else recall(lot)
        rows = [(r["level"], r["lot"].code, str(r["used_by"]), str(r["quantity"]), [m.code for m in r["made"]])
                for r in found["descendants"]]
        return {
            "rows": rows,
            "sorted_rows": sorted(rows),
            "customers": sorted((c["lot"].code, str(c["quantity"])) for c in found["customers"]),
            "not_followed": sorted(lot.code for lot in found["not_followed"]),
        }

    def test_P13_trace_recall_of_a_plain_tree_and_a_deep_chain(self):
        plain = self.snapshot(self.b1)
        chain, previous, taken = [], self.b1, "150"
        for code in ("C1", "C2", "C3", "C4", "C5"):
            keep = str(int(taken) - 20)
            _, (lot, _x) = self.split(taken=taken, rows=((code, keep), (code + "x", "20")), source=previous)
            chain.append(lot)
            previous, taken = lot, keep
        deep = self.snapshot(self.b1)
        deeper = self.snapshot(self.b1, depth=10)
        print("\nTRACE_PLAIN " + json.dumps(plain, sort_keys=True))
        print("TRACE_DEEP " + json.dumps(deep, sort_keys=True))
        print("TRACE_DEEPER " + json.dumps(deeper, sort_keys=True))
        self.assertTrue(deep["not_followed"], "a chain of five re-batches is not followed to its end by the default depth")


from .tests_orders import RunTestCase  # noqa: E402


class TraceOnALoop(RunTestCase):
    def run_of(self, eats, makes):
        order = self.order()
        order.release(TODAY)
        self.issue_with_lots(order, [(self.regrind, "15", eats)]).post()
        entry = self.produce(order, "100", byproducts=[(self.regrind, "2")])
        entry.byproducts.update(lot=makes)
        entry.post()
        return order

    def test_P14_trace_recall_ends_on_a_loop_and_lists_each_lot_once(self):
        from apps.inventory.models import Item, Lot

        from .trace import descendants, recall

        Item.objects.filter(pk=self.regrind.pk).update(tracking="lot")
        self.regrind.refresh_from_db()
        rg1, rg2 = (Lot.objects.create(item=self.regrind, code=code) for code in ("RG-1", "RG-2"))
        for lot in (rg1, rg2):
            self.stock(self.regrind, "50", "60", lot=lot)
        self.run_of(eats=rg1, makes=rg2)
        self.run_of(eats=rg2, makes=rg1)
        found = recall(rg1)
        rows = [(r["level"], r["lot"].code, [m.code for m in r["made"]]) for r in found["descendants"]]
        print("\nTRACE_LOOP " + json.dumps({"rows": rows, "not_followed": [x.code for x in found["not_followed"]],
                                          "customers": [c["lot"].code for c in found["customers"]]}, sort_keys=True))
        self.assertTrue(rows)
        self.assertLess(len(descendants(rg1, depth=10)), 10)
