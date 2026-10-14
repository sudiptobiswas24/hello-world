"""
A failure under an advisory plan waits for the Quality Manager.

Decided by the owner on 14 October 2026 (RISKS.md, "Decisions taken",
22). The roll R-001 against 83.125 to 91.875 gsm on the mean of three:
readings of 92, 92 and 92 average 92 and fail. Under an advisory plan the
system no longer rejects it: it posts as awaiting the Quality Manager's
decision, the roll is held, and the Quality Manager (to decide), the
Production Supervisor and the Stores Manager (for information) each get
a to-do due the next working day, and a mail. Left undecided past that
day it goes to the administrator. Figures and days worked in
scratchpad/calc_fix_qdec/scenarios.py.

Refusals first, each on its own build; then what is done.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.core.models import PartyRole, PartyRoleAssignment
from apps.hr.models import Employee

from .decisions import escalate_overdue, next_working_day, tell_the_quality_manager
from .models import Disposition, Inspection, Reading
from .release import check_released, release_status, why_held
from .tests import TODAY, QualityTestCase

MONDAY = datetime.date(2026, 6, 1)


class AwaitingTestCase(QualityTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.advisory = self.plan(mandatory=False)
        self.users, self.clients = {}, {}
        # Meera measures; the plant manager decides; Ravi runs the floor; Sunil keeps the store
        # and has no address to mail.
        for name, role, party, email in (
                ("meera", "Quality Inspector", self.inspector, "meera@plant.example"),
                ("asha", "Quality Manager", self.manager, "asha@plant.example"),
                ("ravi", "Production Supervisor", None, "ravi@plant.example"),
                ("sunil", "Stores Manager", None, "")):
            user = User.objects.create_user(name, email=email)
            user.groups.add(Group.objects.get(name=role))
            if party is not None:
                PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
                Employee.objects.create(party=party, employee_number=name.upper(),
                                        hire_date=datetime.date(2020, 1, 1), user=user)
            self.users[name] = user
            self.clients[name] = APIClient()
            self.clients[name].force_authenticate(user)

    def mandatory(self):
        """The same plan made mandatory: one plan stands for an item at a time."""
        self.advisory.__class__.objects.filter(pk=self.advisory.pk).update(is_mandatory=True)
        return self.advisory

    def failed(self, plan=None, **kwargs):
        """A draft inspection of R-001 reading 92, 92, 92: a mean of 92 against 91.875."""
        return self.inspect(plan or self.advisory, [92, 92, 92], **kwargs)

    def post(self, inspection, who="meera"):
        response = self.clients[who].post(f"/api/quality/inspections/{inspection.pk}/post/")
        inspection.refresh_from_db()
        return response

    def decide(self, inspection, who, disposition, note="Customer takes it for a liner."):
        response = self.clients[who].post(f"/api/quality/inspections/{inspection.pk}/decide/",
                                          {"disposition": disposition, "note": note}, format="json")
        inspection.refresh_from_db()
        return response

    def to_dos(self, inspection, **filters):
        return FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(Inspection),
                                       object_id=inspection.pk, **filters).order_by("assigned_to__username")

    def awaiting(self):
        inspection = self.failed()
        response = self.post(inspection)
        self.assertEqual(response.status_code, 200, response.content)
        return inspection


class NobodyDecidesItAtPostingTests(AwaitingTestCase):
    def test_an_inspector_may_not_reject_it_as_they_post(self):
        inspection = self.failed(disposition=Disposition.REJECT)
        response = self.post(inspection)
        self.assertEqual(response.status_code, 400)
        self.assertIn("the Quality Manager decides what happens to it", str(response.content))
        self.assertFalse(inspection.posted)
        self.assertEqual(self.to_dos(inspection).count(), 0)

    def test_nor_take_it_by_concession(self):
        inspection = self.failed(disposition=Disposition.CONCESSION, decision_note="Customer takes it.")
        self.assertEqual(self.post(inspection, who="asha").status_code, 400)
        self.assertFalse(inspection.posted)

    def test_awaiting_is_not_chosen_by_hand(self):
        inspection = self.inspect(self.mandatory(), [87, 88, 89], disposition=Disposition.AWAITING)
        with self.assertRaisesMessage(ValidationError, "is not chosen"):
            inspection.post()

    def test_a_mandatory_plan_still_rejects_as_before(self):
        inspection = self.failed(plan=self.mandatory())
        inspection.post()
        self.assertEqual(inspection.disposition, Disposition.REJECT)
        self.assertEqual((inspection.told, self.to_dos(inspection).count()), ("", 0))


class OnlyTheQualityManagerDecidesTests(AwaitingTestCase):
    def test_the_inspector_is_refused_by_the_server(self):
        inspection = self.awaiting()
        self.assertEqual(self.decide(inspection, "meera", Disposition.REJECT).status_code, 403)
        self.assertEqual(inspection.disposition, Disposition.AWAITING)

    def test_nor_without_the_permission_from_code(self):
        inspection = self.awaiting()
        with self.assertRaisesMessage(ValidationError, "The Quality Manager decides"):
            inspection.decide(self.users["ravi"], Disposition.REJECT, "Scrap it.")

    def test_not_by_whoever_recorded_the_readings(self):
        """Asha raises the inspection herself, over the API, and so may not decide it."""
        response = self.clients["asha"].post("/api/quality/inspections/", {
            "lot": self.roll.pk, "plan": self.advisory.pk, "inspected_on": str(TODAY)}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        inspection = Inspection.objects.get(pk=response.json()["id"])
        line = self.advisory.lines.get()
        for value in (92, 92, 92):
            Reading.objects.create(inspection=inspection, plan_line=line, value=value)
        self.assertEqual(self.post(inspection, who="asha").status_code, 200)
        response = self.decide(inspection, "asha", Disposition.CONCESSION)
        self.assertEqual(response.status_code, 400)
        self.assertIn("somebody else approves it", str(response.content))
        self.assertEqual(inspection.disposition, Disposition.AWAITING)

    def test_a_concession_is_signed_by_a_person(self):
        stranger = User.objects.create_user("kiran")
        stranger.groups.add(Group.objects.get(name="Quality Manager"))
        inspection = self.awaiting()
        with self.assertRaisesMessage(ValidationError, "not linked to an employee"):
            inspection.decide(stranger, Disposition.CONCESSION, "Customer takes it.")
        inspection.refresh_from_db()
        self.assertEqual((inspection.disposition, inspection.decided_by), (Disposition.AWAITING, None))

    def test_a_decision_says_why(self):
        inspection = self.awaiting()
        response = self.decide(inspection, "asha", Disposition.REJECT, note="  ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Say why", str(response.content))

    def test_only_the_three_decisions(self):
        inspection = self.awaiting()
        self.assertEqual(self.decide(inspection, "asha", Disposition.ACCEPT).status_code, 400)
        self.assertEqual(inspection.disposition, Disposition.AWAITING)

    def test_the_first_decision_stands(self):
        second = User.objects.create_user("kiran")
        second.groups.add(Group.objects.get(name="Quality Manager"))
        inspection = self.awaiting()
        self.assertEqual(self.decide(inspection, "asha", Disposition.REJECT, "Too heavy.").status_code, 200)
        with self.assertRaisesMessage(ValidationError, "was decided already: rejected, by asha"):
            Inspection.objects.get(pk=inspection.pk).decide(second, Disposition.REWORK, "Re-wind it.")
        inspection.refresh_from_db()
        self.assertEqual(inspection.disposition, Disposition.REJECT)

    def test_nothing_waits_on_a_mandatory_rejection(self):
        inspection = self.failed(plan=self.mandatory())
        inspection.post()
        with self.assertRaisesMessage(ValidationError, "is not waiting for a decision: it is rejected"):
            inspection.decide(self.users["asha"], Disposition.CONCESSION, "Take it.")


class ItWaitsHeldTests(AwaitingTestCase):
    def test_posted_it_is_held_and_says_so_to_the_inspector(self):
        inspection = self.failed()
        response = self.post(inspection)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((inspection.result, inspection.disposition, body["lot_status"]),
                         ("fail", Disposition.AWAITING, "held"))
        due = next_working_day(timezone.localdate())
        self.assertIn(f"{self.roll} FAILED: {self.gsm_check} outside the plan's limits ({inspection.number}).",
                      body["warning"])
        self.assertIn("The batch is HELD. Nobody may issue it, load it or ship it until the Quality "
                      "Manager decides", body["warning"])
        self.assertIn(f"Told to decide, by a to-do due {due:%d %b %Y}: asha.", body["warning"])
        self.assertIn("Told for information: ravi, sunil.", body["warning"])
        self.assertIn("Not mailed: sunil (the login has no email address).", body["warning"])
        self.assertEqual(body["told"], {"to_decide": ["asha"], "for_information": ["ravi", "sunil"]})
        self.assertEqual(body["not_mailed"], {"sunil": "the login has no email address"})
        self.assertEqual(inspection.told, body["warning"])  # kept as said
        self.assertEqual(sorted(message.to[0] for message in mail.outbox),
                         ["asha@plant.example", "ravi@plant.example"])
        rows = list(self.to_dos(inspection).values_list("assigned_to__username", "due_on", "link"))
        self.assertEqual(rows, [(name, due, f"/quality/inspections/{inspection.pk}")
                                for name in ("asha", "ravi", "sunil")])
        self.assertTrue(self.to_dos(inspection).get(assigned_to=self.users["asha"]).summary.startswith(
            f"Decide {inspection.number}"))
        self.assertEqual(why_held(self.fabric, self.roll),
                         f"{self.roll} is held: it failed {inspection.number} on 2026-06-01 and waits for "
                         "the Quality Manager's decision. It cannot issue until they decide.")

    @override_settings(EMAIL_BACKEND="apps.core.mail.NotConfiguredBackend")
    def test_a_mail_not_sent_is_said(self):
        body = self.post(self.failed()).json()
        self.assertEqual(sorted(body["not_mailed"]), ["asha", "ravi", "sunil"])
        self.assertIn("Not mailed: asha, ravi (Email is not set up on this server", body["warning"])
        self.assertIn("Not mailed: sunil (the login has no email address).", body["warning"])
        self.assertEqual(body["told"]["to_decide"], ["asha"])  # the to-do stands either way

    def test_nobody_in_the_role_is_said(self):
        self.users["asha"].groups.clear()
        body = self.post(self.failed()).json()
        self.assertIn("Nobody signs in as Quality Manager, so nobody has been told to decide",
                      body["warning"])
        self.assertEqual(body["told"]["to_decide"], [])

    def test_a_reinspection_waits_for_the_decision(self):
        inspection = self.awaiting()
        retest = self.inspect(self.advisory, [87, 88, 89])
        with self.assertRaisesMessage(ValidationError, f"{inspection.number} failed {self.roll} on "
                                                       "2026-06-01 and waits for the Quality Manager"):
            retest.post()
        self.assertEqual(self.decide(inspection, "asha", Disposition.REWORK, "Re-wind and test.").status_code,
                         200)
        Inspection.objects.get(pk=retest.pk).post()
        self.assertEqual(release_status(self.roll), "released")


class TheQualityManagerDecidesTests(AwaitingTestCase):
    def test_use_as_is_releases_it_signed_by_them(self):
        inspection = self.awaiting()
        response = self.decide(inspection, "asha", Disposition.CONCESSION)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((inspection.disposition, inspection.decided_by, inspection.decision_login),
                         (Disposition.CONCESSION, self.manager, self.users["asha"]))
        self.assertEqual(inspection.decision_note, "Customer takes it for a liner.")
        self.assertEqual(release_status(self.roll), "released")
        check_released(self.fabric, self.roll)
        done = list(self.to_dos(inspection).values_list("assigned_to__username", "done_by__username", "done_on"))
        self.assertEqual(done, [(name, "asha", timezone.localdate()) for name in ("asha", "ravi", "sunil")])

    def test_rework_and_reject_keep_it_held(self):
        for disposition in (Disposition.REWORK, Disposition.REJECT):
            with self.subTest(disposition=disposition):
                Inspection.objects.filter(lot=self.roll, posted=True).update(voided_at=timezone.now())
                inspection = self.awaiting()
                self.assertEqual(self.decide(inspection, "asha", disposition).status_code, 200)
                self.assertEqual(release_status(self.roll), "held")
                self.assertEqual(self.to_dos(inspection, done_on__isnull=True).count(), 0)

    def test_what_awaits_them_oldest_first_overdue_marked(self):
        older, newer = self.awaiting(), None
        other = self.roll.__class__.objects.create(item=self.fabric, code="R-002")
        newer = self.failed(lot=other)
        self.post(newer)
        Inspection.objects.filter(pk=older.pk).update(
            posted_at=timezone.make_aware(datetime.datetime.combine(MONDAY, datetime.time(10))))
        response = self.clients["asha"].get("/api/quality/inspections/awaiting/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = [(row["number"], row["due_on"], row["overdue"]) for row in response.json()]
        self.assertEqual(rows, [
            (older.number, "2026-06-02", True),
            (newer.number, str(next_working_day(timezone.localdate())), False),
        ])
        self.assertEqual(self.clients["meera"].get("/api/quality/inspections/awaiting/").status_code, 403)
        listed = self.clients["ravi"].get("/api/quality/inspections/", {"disposition": "awaiting"})
        self.assertEqual(listed.json()["count"] if isinstance(listed.json(), dict) else len(listed.json()), 2)


class TheReversePathsTests(AwaitingTestCase):
    def test_voiding_one_that_waits_closes_the_to_dos_and_hands_back(self):
        passed = self.inspect(self.advisory, [87, 88, 89])
        passed.post()
        Inspection.objects.filter(pk=passed.pk).update(inspected_on=TODAY - datetime.timedelta(days=3))
        inspection = self.awaiting()
        self.assertEqual(release_status(self.roll), "held")
        response = self.clients["meera"].post(f"/api/quality/inspections/{inspection.pk}/void/",
                                              {"reason": "Wrong roll tested"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(release_status(self.roll), "released")  # the pass before it speaks again
        done = set(self.to_dos(inspection).values_list("done_by__username", flat=True))
        self.assertEqual(done, {"meera"})
        self.assertIn("was voided", self.to_dos(inspection).first().outcome)

    def test_voiding_from_code_closes_them_as_each_persons_own(self):
        inspection = self.awaiting()
        Inspection.objects.get(pk=inspection.pk).void("Wrong roll tested")
        self.assertEqual(release_status(self.roll), "uninspected")
        self.assertEqual(list(self.to_dos(inspection).values_list("assigned_to__username", "done_by__username")),
                         [("asha", "asha"), ("ravi", "ravi"), ("sunil", "sunil")])

    def test_voiding_a_decided_one_hands_back_to_the_verdict_before(self):
        inspection = self.awaiting()
        self.decide(inspection, "asha", Disposition.CONCESSION)
        self.assertEqual(release_status(self.roll), "released")
        Inspection.objects.get(pk=inspection.pk).void("Readings taken on the wrong gauge")
        self.assertEqual(release_status(self.roll), "uninspected")
        with self.assertRaisesMessage(ValidationError, "is voided: there is nothing to decide"):
            Inspection.objects.get(pk=inspection.pk).decide(self.users["asha"], Disposition.REJECT, "No.")


class LeftUndecidedTests(AwaitingTestCase):
    """Posted Monday 1 June: due Tuesday 2 June; escalated by the run on Wednesday 3 June, once."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("root", email="root@plant.example")
        self.inspection = self.awaiting()
        Inspection.objects.filter(pk=self.inspection.pk).update(
            posted_at=timezone.make_aware(datetime.datetime.combine(MONDAY, datetime.time(10))))

    def admins(self):
        return self.to_dos(self.inspection, assigned_to=self.admin)

    def test_not_before_the_next_working_day_has_passed(self):
        self.assertEqual(escalate_overdue(MONDAY), [])
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 2)), [])
        self.assertEqual(self.admins().count(), 0)

    def test_after_it_the_administrator_is_told_once(self):
        ((escalated, told),) = escalate_overdue(datetime.date(2026, 6, 3))
        self.assertEqual((escalated.pk, told.told), (self.inspection.pk, ["root"]))
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 3)), [])
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 4)), [])
        self.assertEqual(list(self.admins().values_list("due_on", flat=True)), [datetime.date(2026, 6, 3)])
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.escalated_on, datetime.date(2026, 6, 3))
        self.assertTrue(self.admins().get().summary.startswith(
            f"Overdue: {self.inspection.number} has waited for the Quality Manager's decision since 01 Jun 2026"))

    def test_the_command_twice_writes_one(self):
        from io import StringIO

        first, second = StringIO(), StringIO()
        call_command("escalate_quality_decisions", stdout=first)
        call_command("escalate_quality_decisions", stdout=second)
        self.assertIn("1 escalated.", first.getvalue())
        self.assertIn("0 escalated.", second.getvalue())
        self.assertEqual(self.admins().count(), 1)

    def test_deciding_closes_the_administrators_too(self):
        escalate_overdue(datetime.date(2026, 6, 3))
        self.decide(self.inspection, "asha", Disposition.REJECT, "Too heavy for the order.")
        self.assertEqual(self.admins().get().done_by, self.users["asha"])

    def test_one_decided_or_voided_is_not_escalated(self):
        self.decide(self.inspection, "asha", Disposition.REJECT, "Too heavy for the order.")
        self.assertEqual(escalate_overdue(datetime.date(2026, 6, 3)), [])


class TellingIsForAnyRecordTests(AwaitingTestCase):
    def test_the_quality_manager_is_given_a_to_do_about_a_batch(self):
        told = tell_the_quality_manager(self.roll, "Look at R-001 before it is received",
                                        link="/stock/lots/1")
        self.assertEqual((told.told, told.not_mailed, told.nobody), (["asha"], {}, ""))
        to_do = FollowUp.objects.get(assigned_to=self.users["asha"])
        self.assertEqual((to_do.record_model, to_do.object_id, to_do.due_on),
                         (type(self.roll), self.roll.pk, next_working_day(timezone.localdate())))
        self.assertEqual(mail.outbox[0].subject, "Look at R-001 before it is received")
