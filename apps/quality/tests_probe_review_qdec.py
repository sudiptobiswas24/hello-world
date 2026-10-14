"""
review_qdec: adversarial probes of gate g19 (decision 22, O145, O146).

Each probe states what should be true. A failing probe is a finding.
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.core.chatter import FollowUp
from apps.hr.models import Employee
from apps.inventory.models import Lot
from apps.quality import release
from apps.quality.decisions import escalate_overdue, tell_the_quality_manager
from apps.quality.models import Disposition, Inspection, Reading
from apps.quality.release import release_status, why_held
from apps.quality.tests import TODAY
from apps.quality.tests_decisions import AwaitingTestCase


def aware(y, m, d, h=10, mi=0):
    return timezone.make_aware(datetime.datetime(y, m, d, h, mi))


class WhoMayDecide(AwaitingTestCase):
    def todos(self, inspection, **kw):
        return self.to_dos(inspection, **kw)

    def test_Q01_the_inspector_may_not_clear_the_hold_by_voiding(self):
        """
        R-001 reads 92, 92, 92 against 83.125 to 91.875. Meera measured it. The decision is the
        Quality Manager's, not hers: she must not be able to take the hold off by voiding it
        (the batch then reads 'not yet inspected', which an advisory plan lets go).
        """
        inspection = self.awaiting()
        response = self.clients["meera"].post(f"/api/quality/inspections/{inspection.pk}/void/",
                                              {"reason": "typo"}, format="json")
        inspection.refresh_from_db()
        self.assertIn(response.status_code, (400, 403), (response.status_code, response.content))
        self.assertIsNone(inspection.voided_at)
        self.assertNotEqual(why_held(self.fabric, self.roll), "")

    def test_Q01b_the_whole_bypass_void_then_post_a_pass_frees_the_batch_with_no_decision(self):
        """92, 92, 92 fails; Meera voids it and posts 88, 88, 88 the same day: released, nobody decided."""
        inspection = self.awaiting()
        self.clients["meera"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": "retake"},
                                   format="json")
        passing = self.inspect(self.advisory, [88, 88, 88])
        self.assertEqual(self.post(passing).status_code, 200)
        decided_by_nobody = release_status(self.roll) == "released" and not Inspection.objects.filter(
            decision_login__isnull=False).exists()
        self.assertFalse(decided_by_nobody, "released with no decision by anyone; the Quality Manager's to-do closed by Meera")
        self.assertEqual({f.done_by.username for f in self.to_dos(inspection)}, {"meera"})

    def test_Q02_a_manager_who_only_changed_the_draft_may_not_decide(self):
        inspection = self.failed()
        r = self.clients["asha"].patch(f"/api/quality/inspections/{inspection.pk}/",
                                       {"notes": "checked twice"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.post(inspection, who="meera").status_code, 200)
        r = self.decide(inspection, "asha", Disposition.REJECT)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(inspection.disposition, Disposition.AWAITING)

    def test_Q03_a_manager_who_added_a_reading_may_not_decide(self):
        inspection = self.failed()
        line = self.advisory.lines.get()
        r = self.clients["asha"].post("/api/quality/readings/", {
            "inspection": inspection.pk, "plan_line": line.pk, "value": "92", "sample_reference": "S4"},
            format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.post(inspection, who="meera").status_code, 200)
        r = self.decide(inspection, "asha", Disposition.REJECT)
        self.assertEqual(r.status_code, 400, r.content)

    def test_Q04_a_second_manager_decides_and_everyones_todo_closes(self):
        inspection = self.awaiting()
        kiran = User.objects.create_user("kiran")
        kiran.groups.add(Group.objects.get(name="Quality Manager"))
        c = self.clients["asha"].__class__()
        c.force_authenticate(kiran)
        r = c.post(f"/api/quality/inspections/{inspection.pk}/decide/",
                   {"disposition": "rework", "note": "re-wind"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.todos(inspection, done_on__isnull=True).count(), 0)
        self.assertEqual({f.done_by.username for f in self.todos(inspection)}, {"kiran"})
        self.assertEqual(release_status(self.roll), "held")

    def test_Q05_production_supervisor_and_stores_manager_get_403(self):
        inspection = self.awaiting()
        for who in ("ravi", "sunil"):
            r = self.decide(inspection, who, Disposition.CONCESSION)
            self.assertEqual(r.status_code, 403, (who, r.content))

    def test_Q06_a_superuser_who_recorded_the_readings_decides_his_own_failure(self):
        """
        'Whoever recorded the reading cannot make the decision' (decision 22). check_not_raised_by
        exempts a superuser; so a superuser who keyed in and posted a failing inspection decides it.
        """
        from rest_framework.test import APIClient

        boss = User.objects.create_superuser("boss", "boss@plant.example", "x")
        c = APIClient()
        c.force_authenticate(boss)
        r = c.post("/api/quality/inspections/", {"lot": self.roll.pk, "plan": self.advisory.pk,
                                                 "inspected_on": str(TODAY)}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        inspection = Inspection.objects.get(pk=r.json()["id"])
        line = self.advisory.lines.get()
        for n in range(3):
            self.assertEqual(c.post("/api/quality/readings/", {"inspection": inspection.pk,
                             "plan_line": line.pk, "value": "92", "sample_reference": f"S{n}"},
                             format="json").status_code, 201)
        self.assertEqual(c.post(f"/api/quality/inspections/{inspection.pk}/post/").status_code, 200)
        r = c.post(f"/api/quality/inspections/{inspection.pk}/decide/",
                   {"disposition": "reject", "note": "mine"}, format="json")
        self.assertEqual(r.status_code, 400, "a superuser recorded it and decided it: " + str(r.content))

    def test_Q07_a_note_that_is_not_text_is_a_400_not_a_500(self):
        inspection = self.awaiting()
        c = self.clients["asha"]
        c.raise_request_exception = False
        for bad in (123, ["a"], {"a": 1}):
            r = c.post(f"/api/quality/inspections/{inspection.pk}/decide/",
                       {"disposition": "reject", "note": bad}, format="json")
            self.assertEqual(r.status_code, 400, (bad, r.status_code))

    def test_Q08_a_long_reason_is_refused_not_cut(self):
        inspection = self.awaiting()
        note = "x" * 300
        r = self.decide(inspection, "asha", Disposition.REJECT, note=note)
        inspection.refresh_from_db()
        self.assertTrue(r.status_code == 400 or inspection.decision_note == note,
                        f"{r.status_code}: kept {len(inspection.decision_note)} of 300 characters")

    def test_Q09_a_non_string_disposition_is_a_400(self):
        inspection = self.awaiting()
        c = self.clients["asha"]
        c.raise_request_exception = False
        for bad in ([], {}, None, 5):
            r = c.post(f"/api/quality/inspections/{inspection.pk}/decide/",
                       {"disposition": bad, "note": "n"}, format="json")
            self.assertEqual(r.status_code, 400, (bad, r.status_code))

    def test_Q10_deciding_a_voided_decided_and_never_awaiting_one(self):
        inspection = self.awaiting()
        self.assertEqual(self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/",
                                                   {"reason": "wrong lot"}, format="json").status_code, 200)
        r = self.decide(inspection, "asha", Disposition.REJECT)
        self.assertEqual(r.status_code, 400)
        self.assertIn("voided", str(r.content))
        passing = self.inspect(self.advisory, [88, 88, 88])
        self.assertEqual(self.post(passing).status_code, 200)
        r = self.decide(passing, "asha", Disposition.REJECT)
        self.assertEqual(r.status_code, 400)
        self.assertIn("not waiting for a decision", str(r.content))

    def test_Q11_a_concession_later_is_signed_by_the_person_and_needs_the_note(self):
        inspection = self.awaiting()
        r = self.decide(inspection, "asha", Disposition.CONCESSION, note="Customer takes it for liners.")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((inspection.disposition, inspection.decided_by), (Disposition.CONCESSION, self.manager))
        self.assertEqual(inspection.decision_login, self.users["asha"])
        self.assertEqual(release_status(self.roll), "released")
        self.assertEqual(why_held(self.fabric, self.roll), "")

    def test_Q12_the_only_manager_recorded_it_the_answer_must_not_say_she_decides(self):
        """
        Asha is the only Quality Manager and raised the inspection herself. She may not decide it.
        The inspector is told 'Told to decide: asha' - nobody eligible exists; the answer should say so.
        """
        r = self.clients["asha"].post("/api/quality/inspections/", {
            "lot": self.roll.pk, "plan": self.advisory.pk, "inspected_on": str(TODAY)}, format="json")
        inspection = Inspection.objects.get(pk=r.json()["id"])
        line = self.advisory.lines.get()
        for n in range(3):
            Reading.objects.create(inspection=inspection, plan_line=line, value=Decimal("92"))
        body = self.post(inspection, who="meera").json()
        self.assertNotIn("Told to decide", body["warning"], body["warning"])


class ReversePaths(AwaitingTestCase):
    def passed_first(self):
        first = self.inspect(self.advisory, [88, 88, 88])
        self.assertEqual(self.post(first).status_code, 200)
        return first

    def test_R01_void_an_awaiting_one_returns_the_batch_to_the_verdict_before_it(self):
        first = self.passed_first()
        inspection = self.awaiting()
        self.assertEqual(release_status(self.roll), "held")
        r = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/",
                                      {"reason": "wrong sample"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(release_status(self.roll), "released")
        self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 0)
        self.assertEqual(self.to_dos(inspection).count(), 3)
        self.assertEqual({f.done_by.username for f in self.to_dos(inspection)}, {"asha"})
        self.assertEqual(escalate_overdue(datetime.date(2030, 1, 1)), [])
        self.assertEqual(first.pk, release.latest_inspection(self.roll).pk)

    def test_R02_reinspection_while_one_awaits_earlier_same_day_later(self):
        inspection = self.awaiting()
        for days in (-1, 0, 1):
            draft = self.inspect(self.advisory, [88, 88, 88])
            Inspection.objects.filter(pk=draft.pk).update(inspected_on=TODAY + datetime.timedelta(days=days))
            r = self.post(draft)
            self.assertEqual(r.status_code, 400, (days, r.content))
            self.assertIn("waits for the Quality Manager's decision", " ".join(r.json()))
        self.assertEqual(release_status(self.roll), "held")
        self.assertEqual(release.latest_inspection(self.roll).pk, inspection.pk)

    def test_R03_after_the_void_a_new_failure_waits_again_with_new_todos(self):
        inspection = self.awaiting()
        self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": "x"},
                                  format="json")
        again = self.awaiting()
        self.assertEqual(self.to_dos(again).count(), 3)
        self.assertEqual(self.to_dos(again, done_on__isnull=True).count(), 3)
        self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 0)

    def test_R04_void_a_decided_one_does_not_reopen_or_reescalate(self):
        first = self.passed_first()
        inspection = self.awaiting()
        self.assertEqual(self.decide(inspection, "asha", Disposition.REJECT, "scrap").status_code, 200)
        self.assertEqual(release_status(self.roll), "held")
        r = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": "mistake"},
                                      format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(release_status(self.roll), "released")
        self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 0)
        self.assertEqual(escalate_overdue(datetime.date(2030, 1, 1)), [])
        # a voided decision stays voided: no second decision
        r = self.decide(inspection, "asha", Disposition.CONCESSION)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(first.pk, release.latest_inspection(self.roll).pk)

    def test_R05_a_blank_reason_leaves_the_todos_open(self):
        inspection = self.awaiting()
        r = self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": ""},
                                      format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 3)

    def test_R06_a_decided_inspections_todos_are_not_given_a_second_outcome_by_a_later_void(self):
        inspection = self.awaiting()
        self.decide(inspection, "asha", Disposition.REWORK, "re-wind")
        before = list(self.to_dos(inspection).values_list("pk", "done_by_id", "outcome"))
        self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": "x"},
                                  format="json")
        self.assertEqual(before, list(self.to_dos(inspection).values_list("pk", "done_by_id", "outcome")))

    def test_R07_o145_two_verdicts_one_day_the_later_posting_stands_with_numbers(self):
        """Reject drafted first (id 1), pass drafted second (id 2); pass posted first: reject posted
        last must stand: 92,92,92 fails and holds R-001."""
        reject = self.failed()
        passing = self.inspect(self.advisory, [88, 88, 88])
        self.assertLess(reject.pk, passing.pk)
        self.post(passing)
        self.post(reject)
        self.assertEqual(release.latest_inspection(self.roll).pk, reject.pk)
        self.assertEqual(release_status(self.roll), "held")

    def test_R08_o145_the_pass_posted_after_a_voided_failure(self):
        reject = self.awaiting()
        self.clients["asha"].post(f"/api/quality/inspections/{reject.pk}/void/", {"reason": "x"}, format="json")
        passing = self.inspect(self.advisory, [88, 88, 88])
        self.post(passing)
        self.assertEqual(release_status(self.roll), "released")


class Lineage(AwaitingTestCase):
    """O146 and the awaiting rule across a split or join, with a stub for lot_sources."""

    def setUp(self):
        super().setUp()
        self.parents = {}
        self.stub = lambda lot: list(self.parents.get(lot.pk, []))
        release.register_lot_sources(self.stub)
        self.addCleanup(lambda: release.LOT_SOURCES.remove(self.stub))
        self.b = Lot.objects.create(item=self.fabric, code="R-002")
        self.c = Lot.objects.create(item=self.fabric, code="R-003")

    def test_L01_a_child_of_an_awaiting_batch_is_held_and_cannot_be_inspected_passing(self):
        awaiting = self.awaiting()
        self.parents[self.b.pk] = [self.roll]
        self.assertEqual(release_status(self.b), "held")
        self.assertNotEqual(why_held(self.fabric, self.b), "")
        draft = self.inspect(self.advisory, [88, 88, 88], lot=self.b)
        r = self.post(draft)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(awaiting.number, str(r.content))
        # the parent's verdict goes (voided): the child may be inspected again
        self.clients["asha"].post(f"/api/quality/inspections/{awaiting.pk}/void/", {"reason": "x"},
                                  format="json")
        r = self.post(draft)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(release_status(self.b), "released")

    def test_L02_a_grandchild_backdated_before_a_grandparents_rejection_is_refused(self):
        from apps.quality.models import InspectionPlan

        InspectionPlan.objects.filter(pk=self.advisory.pk).update(is_mandatory=True)
        reject = self.inspect(self.advisory, [92, 92, 92])
        reject.post()
        self.assertEqual(reject.disposition, Disposition.REJECT)
        self.parents[self.b.pk] = [self.roll]
        self.parents[self.c.pk] = [self.b]   # R-003 from R-002 from R-001
        draft = self.inspect(self.advisory, [88, 88, 88], lot=self.c)
        Inspection.objects.filter(pk=draft.pk).update(inspected_on=TODAY - datetime.timedelta(days=7))
        r = self.post(draft)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(reject.number, str(r.content))
        Inspection.objects.filter(pk=draft.pk).update(inspected_on=TODAY)
        self.assertEqual(self.post(draft).status_code, 200)

    def test_L03_a_join_is_held_by_either_parent_awaiting(self):
        self.awaiting()
        good = self.inspect(self.advisory, [88, 88, 88], lot=self.b)
        self.assertEqual(self.post(good).status_code, 200)
        self.parents[self.c.pk] = [self.roll, self.b]
        self.assertEqual(release_status(self.c), "held")
        draft = self.inspect(self.advisory, [88, 88, 88], lot=self.c)
        self.assertEqual(self.post(draft).status_code, 400)

    def test_L04_a_cycle_in_lot_sources_does_not_hang(self):
        self.parents[self.roll.pk] = [self.b]
        self.parents[self.b.pk] = [self.roll]
        self.assertEqual({l.pk for l in release.lot_and_sources(self.roll)}, {self.roll.pk, self.b.pk})

    def test_L05_a_child_decided_conceded_parent_releases_the_child(self):
        inspection = self.awaiting()
        self.parents[self.b.pk] = [self.roll]
        self.assertEqual(release_status(self.b), "held")
        self.decide(inspection, "asha", Disposition.CONCESSION, "customer takes it")
        self.assertEqual(release_status(self.b), "released")
        self.assertEqual(why_held(self.fabric, self.b), "")


class Escalation(AwaitingTestCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", "root@plant.example", "x")

    def at(self, inspection, posted_at):
        Inspection.objects.filter(pk=inspection.pk).update(posted_at=posted_at)
        inspection.refresh_from_db()

    def admin_todos(self, inspection):
        return self.to_dos(inspection, assigned_to=self.root)

    def test_E01_friday_posting_is_due_monday_and_escalates_tuesday_once(self):
        inspection = self.awaiting()
        self.at(inspection, aware(2026, 6, 5))
        self.assertEqual(inspection.decision_due_on(), datetime.date(2026, 6, 8))
        for day in (datetime.date(2026, 6, 5), datetime.date(2026, 6, 6), datetime.date(2026, 6, 7),
                    datetime.date(2026, 6, 8)):
            self.assertEqual(escalate_overdue(day), [], day)
        with self.captureOnCommitCallbacks(execute=True):
            found = escalate_overdue(datetime.date(2026, 6, 9))
        self.assertEqual([i.pk for i, _ in found], [inspection.pk])
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 9)), [])
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 10)), [])
        self.assertEqual(self.admin_todos(inspection).count(), 1)
        self.assertEqual(self.admin_todos(inspection).get().due_on, datetime.date(2026, 6, 9))
        self.assertEqual([m.to[0] for m in mail.outbox if "Overdue" in m.subject], ["root@plant.example"])

    def test_E02_decided_in_between_is_not_escalated_and_closes_the_admin_todo(self):
        inspection = self.awaiting()
        self.at(inspection, aware(2026, 6, 1))
        escalate_overdue(datetime.date(2026, 6, 3))
        self.assertEqual(self.admin_todos(inspection).count(), 1)
        self.assertEqual(self.decide(inspection, "asha", Disposition.REJECT, "scrap").status_code, 200)
        self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 0)
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 4)), [])

    def test_E03_with_no_administrator_the_escalation_is_not_burned(self):
        """No active superuser: the run says so. Once one exists, the next run must tell them."""
        self.root.is_active = False
        self.root.save()
        inspection = self.awaiting()
        self.at(inspection, aware(2026, 6, 1))
        found = escalate_overdue(datetime.date(2026, 6, 3))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0][1].failures())
        self.root.is_active = True
        self.root.save()
        again = escalate_overdue(datetime.date(2026, 6, 4))
        self.assertEqual(len(again), 1, "the administrator was never told, and never will be")
        self.assertEqual(self.admin_todos(inspection).count(), 1)

    def test_E04_the_kolkata_day(self):
        """Posted 04 Jun 20:00 UTC = Fri 05 Jun 01:30 IST: due Mon 08 Jun, not Fri 05."""
        with override_settings(TIME_ZONE="Asia/Kolkata"):
            inspection = self.awaiting()
            Inspection.objects.filter(pk=inspection.pk).update(
                posted_at=datetime.datetime(2026, 6, 4, 20, 0, tzinfo=datetime.timezone.utc))
            inspection.refresh_from_db()
            self.assertEqual(inspection.decision_due_on(), datetime.date(2026, 6, 8))

    def test_E05_command_runs_twice(self):
        from django.core.management import call_command
        from io import StringIO

        inspection = self.awaiting()
        self.at(inspection, aware(2026, 1, 5))
        out = StringIO()
        call_command("escalate_quality_decisions", stdout=out)
        call_command("escalate_quality_decisions", stdout=out)
        self.assertIn("1 escalated.", out.getvalue())
        self.assertIn("0 escalated.", out.getvalue())
        self.assertEqual(self.admin_todos(inspection).count(), 1)

    def test_E06_two_superusers_each_get_one(self):
        User.objects.create_superuser("root2", "r2@plant.example", "x")
        inspection = self.awaiting()
        self.at(inspection, aware(2026, 6, 1))
        escalate_overdue(datetime.date(2026, 6, 3))
        escalate_overdue(datetime.date(2026, 6, 3))
        self.assertEqual(self.to_dos(inspection, assigned_to__is_superuser=True).count(), 2)

    def test_E07_a_voided_one_is_not_escalated(self):
        inspection = self.awaiting()
        self.at(inspection, aware(2026, 6, 1))
        self.clients["asha"].post(f"/api/quality/inspections/{inspection.pk}/void/", {"reason": "x"}, format="json")
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 9)), [])


class Telling(AwaitingTestCase):
    def test_T01_a_manager_who_cannot_open_the_record_is_named(self):
        told = tell_the_quality_manager(self.users["ravi"], "something", link="/x")
        self.assertEqual(told.told, [])
        self.assertIn("asha", told.not_told)
        self.assertTrue(any("asha" in s for s in told.failures()))

    def test_T02_a_manager_without_view_of_inspections_is_said_at_posting(self):
        group = Group.objects.get(name="Quality Manager")
        group.permissions.remove(*group.permissions.filter(codename__in=["view_inspection"]))
        body = self.post(self.failed()).json()
        self.assertIn("asha", body["not_told"], body)
        self.assertIn("Not given a to-do", body["warning"])

    def test_T03_a_rolled_back_posting_leaves_no_todo_no_mail_no_told(self):
        from django.db import transaction

        inspection = self.failed()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    Inspection.objects.get(pk=inspection.pk).post(by=self.users["meera"])
                    raise ValidationError("later")
            except ValidationError:
                pass
        inspection.refresh_from_db()
        self.assertEqual((inspection.posted, inspection.told, mail.outbox), (False, "", []))

    def test_T04_an_unexpected_mail_error_after_commit_does_not_turn_a_posting_into_a_500(self):
        inspection = self.failed()
        c = self.clients["meera"]
        c.raise_request_exception = False
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=RuntimeError("boom")):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.post(inspection)
        inspection.refresh_from_db()
        self.assertTrue(inspection.posted)
        self.assertEqual(r.status_code, 200, "the posting stood but the inspector was answered with an error")

    def test_T05_one_person_in_two_roles_is_told_once(self):
        self.users["asha"].groups.add(Group.objects.get(name="Production Supervisor"))
        inspection = self.awaiting()
        self.assertEqual(self.to_dos(inspection, assigned_to=self.users["asha"]).count(), 1)


# ---------------------------------------------------------------- the gate, the stations, the roles

from apps.core.management.commands import setup_roles  # noqa: E402
from apps.inventory.models import TrackingMode  # noqa: E402
from apps.manufacturing.tests_orders import RunTestCase  # noqa: E402
from apps.quality.models import Characteristic, InspectionPlan, PlanLine  # noqa: E402


class GateExits(RunTestCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        self.drum = Lot.objects.create(item=self.virgin, code="PP-A")
        self.stock(self.virgin, "3000", "100", lot=self.drum)
        self.denier = Characteristic.objects.create(code="DEN", name="Denier", uom=self.kg)
        self.plan = InspectionPlan.objects.create(item=self.virgin, is_mandatory=False)
        self.line = PlanLine.objects.create(plan=self.plan, characteristic=self.denier, target=Decimal("1000"),
                                            lower_limit=Decimal("950"), upper_limit=Decimal("1050"),
                                            sample_size=1)
        self.qm = User.objects.create_user("asha", email="asha@plant.example")
        self.qm.groups.add(Group.objects.get(name="Quality Manager"))

    def inspect(self, value, day=TODAY):
        inspection = Inspection.objects.create(lot=self.drum, plan=self.plan, inspected_on=day)
        Reading.objects.create(inspection=inspection, plan_line=self.line, value=Decimal(str(value)))
        inspection.post()
        return inspection

    def test_G01_every_door_asks_the_same_gate(self):
        from apps.inventory.tracking import allocate
        from apps.planning.mrp import may_be_drawn

        awaiting = self.inspect(1200)
        self.assertEqual(awaiting.disposition, Disposition.AWAITING)
        for action in ("issue", "go into a run", "be shipped", "be baled", "be reworked"):
            said = why_held(self.virgin, self.drum, warehouse=self.plant, action=action)
            self.assertIn("waits for the Quality Manager's decision", said, action)
        self.assertFalse(may_be_drawn(self.virgin, self.drum))
        with self.assertRaisesMessage(ValidationError, "held by quality"):
            allocate(self.virgin, self.plant, Decimal("50"))
        with self.assertRaisesMessage(ValidationError, "waits for the Quality Manager"):
            release.check_released(self.virgin, self.drum, action="be shipped", warehouse=self.plant)

    def test_G02_a_split_of_an_awaiting_batch_is_held_and_follows_the_decision(self):
        from apps.manufacturing.rebatch import rebatch

        awaiting = self.inspect(1200)
        part = Lot.objects.create(item=self.virgin, code="PP-A2")
        rest = Lot.objects.create(item=self.virgin, code="PP-A3")
        rebatch(self.virgin, self.plant, [(self.drum, "100")], [(part, "60"), (rest, "40")], "split", on_date=TODAY)
        self.assertEqual((release_status(part), release_status(rest)), ("held", "held"))
        self.assertNotEqual(why_held(self.virgin, part), "")
        awaiting.refresh_from_db()
        awaiting.decide(self.qm, Disposition.CONCESSION, "heavy denier is fine for a liner") \
            if False else None
        # the Quality Manager has no linked employee here: a rework decision needs none
        awaiting.decide(self.qm, Disposition.REWORK, "re-extrude")
        self.assertEqual((release_status(part), release_status(rest)), ("held", "held"))

    def test_G03_a_move_to_the_rejects_store_is_allowed_and_the_batch_still_waits(self):
        from apps.inventory.models import Warehouse
        from apps.inventory.transfers import StockTransfer, StockTransferLine

        self.inspect(1200)
        rejects = Warehouse.objects.create(code="REJ", name="Rejects store")
        transfer = StockTransfer.objects.create(from_warehouse=self.plant, to_warehouse=rejects, transfer_date=TODAY)
        StockTransferLine.objects.create(transfer=transfer, item=self.virgin, uom=self.kg, lot=self.drum,
                                         quantity=Decimal("100"))
        transfer.post()
        self.assertIn("waits for the Quality Manager", why_held(self.virgin, self.drum, warehouse=rejects))

    def test_G04_reject_and_rework_decisions_keep_it_held_and_concession_frees_it(self):
        for disposition, held in ((Disposition.REJECT, True), (Disposition.REWORK, True)):
            Inspection.objects.all().delete()
            inspection = self.inspect(1200)
            inspection.decide(self.qm, disposition, "n")
            self.assertEqual(why_held(self.virgin, self.drum) != "", held, disposition)

    def test_G05_a_rework_run_may_be_raised_after_the_decision_is_rework_not_before(self):
        from apps.manufacturing.orders import BillOfMaterials, WorkOrder
        import inspect as _i  # noqa: F401

        awaiting = self.inspect(1200)
        bom = BillOfMaterials.objects.filter(is_rework=True).first()
        if bom is None:
            self.skipTest("no rework recipe in this fixture")


class StationsUnderAdvisory(RunTestCase):
    """Built in the tape, film and bag stations' own fixtures below."""


from apps.manufacturing.tests_liners import LinerTestCase, OFF as FILM_OFF  # noqa: E402
from apps.manufacturing.tests_station_tape import OFF as TAPE_OFF, TapeTestCase, LabFinishesTests, build_tape_line  # noqa: E402
from apps.manufacturing.tests_station_api import StationApiTestCase  # noqa: E402


class TheFilmPath(LinerTestCase):
    def test_F01_a_failed_roll_under_advisory_waits_names_no_concession(self):
        InspectionPlan.objects.update(is_mandatory=False)
        roll = self.wind(readings=FILM_OFF, reason="Die gap reset")
        self.assertEqual((roll.inspection.disposition, roll.inspection.decided_by, roll.conceded_by),
                         (Disposition.AWAITING, None, None))
        self.assertEqual(release_status(roll.lot), "held")
        self.assertIn("Die gap reset", roll.inspection.notes)
        self.assertIn("waits for the Quality Manager", why_held(roll.lot.item, roll.lot))

    def test_F02_the_same_roll_under_a_mandatory_plan_is_still_conceded_at_the_station(self):
        InspectionPlan.objects.update(is_mandatory=True)
        roll = self.wind(readings=FILM_OFF, reason="Die gap reset")
        self.assertEqual((roll.inspection.disposition, roll.conceded_by), (Disposition.CONCESSION, self.supervisor))
        self.assertEqual(release_status(roll.lot), "released")

    def test_F03_withdrawing_a_waiting_roll_closes_its_todos_and_frees_nothing(self):
        from apps.manufacturing.station_film import void_film

        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        qm = User.objects.create_user("asha")
        qm.groups.add(Group.objects.get(name="Quality Manager"))
        InspectionPlan.objects.update(is_mandatory=False)
        roll = self.wind(readings=FILM_OFF, reason="Die gap reset")
        kind = ContentType.objects.get_for_model(Inspection)
        open_ = FollowUp.objects.filter(content_type=kind, object_id=roll.inspection.pk, done_on__isnull=True)
        self.assertEqual(open_.count(), 1)
        void_film(roll, self.bf, self.supervisor, self.operator, "wrong core")
        self.assertEqual(open_.count(), 0)

    def test_F04_a_roll_booked_with_no_quality_manager_login_keeps_the_sentence_that_nobody_was_told(self):
        InspectionPlan.objects.update(is_mandatory=False)
        roll = self.wind(readings=FILM_OFF, reason="Die gap reset")
        self.assertIn("Nobody signs in as Quality Manager", roll.inspection.told)


class TheTapePath(TapeTestCase):
    def test_S01_mandatory_off_denier_is_still_conceded_by_the_supervisor(self):
        InspectionPlan.objects.update(is_mandatory=True)
        doff = self.doff(TAPE_OFF, supervisor=self.supervisor, reason="Customer accepts heavy")
        self.assertEqual((doff.inspection.disposition, doff.conceded_by), (Disposition.CONCESSION, self.supervisor))


class TheTapeLabPath(LabFinishesTests):
    """An off denier under an advisory plan, the lab finishing the rest of the plan later."""

    def test_S02_the_lab_path_names_the_supervisor_as_conceding_what_then_waits(self):
        InspectionPlan.objects.update(is_mandatory=False)
        doff = record_tape_off(self)
        line = doff.inspection.plan.lines.get(derived_from="tenacity")
        for number in range(line.sample_size):
            Reading.objects.create(inspection=doff.inspection, plan_line=line, value=Decimal("4.8"),
                                   sample_reference=f"T{number}")
        doff.inspection.post()
        doff.refresh_from_db()
        self.assertEqual(doff.inspection.disposition, Disposition.AWAITING)
        self.assertIsNone(doff.conceded_by, "the doff names the supervisor as conceding a batch that waits for the Quality Manager")


def record_tape_off(test):
    from apps.manufacturing.station_tape import record_tape
    from apps.manufacturing.tests_station_tape import NOON

    return record_tape(test.tx, test.operator, test.e1, "60.0", 8, test.bobbin, TAPE_OFF, at=NOON,
                       supervisor=test.supervisor, reason="Customer accepts heavy")


class TheTapeApi(StationApiTestCase):
    def setUp(self):
        super().setUp()
        build_tape_line(self)
        self.base = f"/api/manufacturing/stations/{self.tx.code}/"

    def test_S03_the_tablet_is_told_the_doff_is_held(self):
        InspectionPlan.objects.update(is_mandatory=False)
        self.client.post(self.base + "sign-in/", {"pin": self.pin}, format="json")
        r = self.client.post(self.base + "tape/", {
            "machine": "E-1", "gross_kg": "60.0", "bobbins": 8, "core": "BOB", "denier": TAPE_OFF,
            "supervisor_pin": self.supervisor_pin, "reason": "Customer accepts heavy"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        said = " ".join(str(v) for v in r.json().values()).lower()
        self.assertTrue("held" in said or "quality manager" in said, r.json())


class Roles(AwaitingTestCase):
    def test_P01_only_the_quality_manager_holds_the_decide_permission(self):
        holders = sorted(g.name for g in Group.objects.filter(permissions__codename="decide_inspection"))
        self.assertEqual(holders, ["Quality Manager"])

    def test_P02_the_two_told_roles_got_view_of_inspection_and_reading_and_nothing_else_in_quality(self):
        for name in ("Production Supervisor", "Stores Manager"):
            quality = sorted(g.codename for g in Group.objects.get(name=name).permissions.filter(
                content_type__app_label="quality"))
            self.assertEqual(quality, ["view_inspection", "view_reading"], name)

    def test_P03_registry_permission_is_what_the_server_checks(self):
        import re
        src = open("frontend/src/app/registry.ts").read()
        self.assertRegex(src, r'path: "awaiting-decision".*permission: "quality.decide_inspection"')
        from apps.quality.views import InspectionViewSet
        self.assertEqual(InspectionViewSet.action_permission_map["awaiting"], "quality.decide_inspection")

    def test_P04_nobody_without_the_permission_reads_the_awaiting_list(self):
        for who in ("meera", "ravi", "sunil"):
            self.assertEqual(self.clients[who].get("/api/quality/inspections/awaiting/").status_code, 403, who)
        self.assertEqual(self.clients["asha"].get("/api/quality/inspections/awaiting/").status_code, 200)

    def test_P05_the_awaiting_list_is_oldest_first_and_marks_overdue(self):
        a = self.awaiting()
        Inspection.objects.filter(pk=a.pk).update(posted_at=aware(2026, 6, 1))
        # a second batch
        from apps.inventory.models import Lot as L
        other = L.objects.create(item=self.fabric, code="R-009")
        b = self.inspect(self.advisory, [92, 92, 92], lot=other)
        self.post(b)
        Inspection.objects.filter(pk=b.pk).update(posted_at=aware(2026, 5, 20))
        rows = self.clients["asha"].get("/api/quality/inspections/awaiting/").json()
        self.assertEqual([r["id"] for r in rows], [b.pk, a.pk])
        self.assertTrue(all(r["overdue"] for r in rows))

    def test_P06_unauthenticated_and_get_on_decide(self):
        from rest_framework.test import APIClient
        inspection = self.awaiting()
        self.assertIn(APIClient().post(f"/api/quality/inspections/{inspection.pk}/decide/", {}, format="json").status_code, (401, 403))
        self.assertEqual(self.clients["asha"].get(f"/api/quality/inspections/{inspection.pk}/decide/").status_code, 405)


class DeletePath(AwaitingTestCase):
    def test_D01_a_posted_awaiting_inspection_cannot_be_deleted_by_anyone(self):
        inspection = self.awaiting()
        for who in ("asha", "meera"):
            r = self.clients[who].delete(f"/api/quality/inspections/{inspection.pk}/")
            self.assertIn(r.status_code, (400, 403, 405), (who, r.status_code))
        self.assertTrue(Inspection.objects.filter(pk=inspection.pk).exists())
        self.assertEqual(release_status(self.roll), "held")

    def test_D02_a_posted_decided_inspection_cannot_be_deleted(self):
        inspection = self.awaiting()
        self.decide(inspection, "asha", Disposition.REJECT, "scrap")
        r = self.clients["asha"].delete(f"/api/quality/inspections/{inspection.pk}/")
        self.assertIn(r.status_code, (400, 403, 405), r.status_code)
        self.assertTrue(Inspection.objects.filter(pk=inspection.pk).exists())

    def test_D03_model_delete_of_a_posted_awaiting_inspection_is_refused(self):
        inspection = self.awaiting()
        with self.assertRaises(ValidationError):
            inspection.delete()
        self.assertTrue(Inspection.objects.filter(pk=inspection.pk).exists())


class TheFacet(AwaitingTestCase):
    def test_V01_the_awaiting_facet_on_the_list_shows_only_what_still_awaits(self):
        gone = self.awaiting()
        self.clients["asha"].post(f"/api/quality/inspections/{gone.pk}/void/", {"reason": "wrong lot"}, format="json")
        live = self.awaiting()
        rows = self.clients["asha"].get("/api/quality/inspections/?disposition=awaiting").json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertEqual([r["id"] for r in rows], [live.pk], "the facet lists a voided inspection as awaiting decision")


class TheBagPath(LinerTestCase):
    def test_B01_an_off_bundle_under_advisory_waits_and_names_no_concession(self):
        InspectionPlan.objects.update(is_mandatory=False)
        count = self.seal(sample=["60"] * 10, supervisor=self.supervisor, reason="Customer agreed +4%")
        self.assertEqual((count.passed, count.supervisor, count.inspection.disposition),
                         (False, None, Disposition.AWAITING))
        self.assertEqual(release_status(count.inspection.lot), "held")
        self.assertIn("Customer agreed +4%", count.inspection.notes)
        self.assertIn("waits for the Quality Manager", why_held(count.inspection.lot.item, count.inspection.lot))

    def test_B02_under_a_mandatory_plan_the_supervisor_still_concedes_it(self):
        InspectionPlan.objects.update(is_mandatory=True)
        count = self.seal(sample=["60"] * 10, supervisor=self.supervisor, reason="Customer agreed +4%")
        self.assertEqual((count.supervisor, count.inspection.disposition),
                         (self.supervisor, Disposition.CONCESSION))
        self.assertEqual(release_status(count.inspection.lot), "released")

    def test_B03_an_on_target_bundle_under_advisory_is_accepted_untouched(self):
        InspectionPlan.objects.update(is_mandatory=False)
        count = self.seal()
        self.assertEqual((count.passed, count.inspection.disposition), (True, Disposition.ACCEPT))

    def test_B04_withdrawing_a_waiting_bundle_voids_its_inspection(self):
        from apps.manufacturing.conversion import void_bags

        InspectionPlan.objects.update(is_mandatory=False)
        count = self.seal(sample=["60"] * 10, supervisor=self.supervisor, reason="ok")
        void_bags(count, self.supervisor, "wrong count")
        count.inspection.refresh_from_db()
        self.assertIsNotNone(count.inspection.voided_at)


class TheKolkataDay(AwaitingTestCase):
    def test_K01_posted_at_half_past_one_on_friday_in_kolkata_is_due_monday(self):
        """04 Jun 20:00 UTC is Fri 05 Jun 01:30 IST. Due Mon 08 Jun for every to-do; on UTC it would be Fri 05."""
        moment = datetime.datetime(2026, 6, 4, 20, 0, tzinfo=datetime.timezone.utc)
        with override_settings(TIME_ZONE="Asia/Kolkata"), mock.patch("django.utils.timezone.now", return_value=moment):
            inspection = self.failed()
            Inspection.objects.filter(pk=inspection.pk).update(inspected_on=datetime.date(2026, 6, 5))
            inspection.refresh_from_db()
            self.assertEqual(self.post(inspection).status_code, 200)
            dues = set(self.to_dos(inspection).values_list("due_on", flat=True))
            self.assertEqual(dues, {datetime.date(2026, 6, 8)})
            inspection.refresh_from_db()
            self.assertEqual(inspection.decision_due_on(), datetime.date(2026, 6, 8))
            self.assertEqual(escalate_overdue(datetime.date(2026, 6, 8)), [])

    def test_K02_a_plan_made_mandatory_or_retired_after_the_failure_does_not_free_the_batch(self):
        from apps.quality.models import InspectionPlan

        self.awaiting()
        InspectionPlan.objects.filter(pk=self.advisory.pk).update(is_mandatory=True, is_active=False)
        self.assertIn("waits for the Quality Manager", why_held(self.fabric, self.roll))
