"""
Notes and follow-ups on a record, asked of the API as people in their
roles. Rep A carries Acme, rep B carries Beta (tests_reps.RepTestCase).

  Rep A notes on Acme's order and plans a call on it for tomorrow; rep B
  cannot read either, nor the order's history or files, and cannot be
  given the call. Done, the call leaves a note saying how it went, and
  is a fact: not changed, not removed, not done twice.
"""

import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.chatter import FollowUp, Note

from .tests_reps import RepTestCase

NOTES = "/api/core/notes/"
FOLLOW_UPS = "/api/core/follow-ups/"


class ChatterTestCase(RepTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.a, self.b = self.as_user(self.rep_a), self.as_user(self.rep_b)
        self.on_acme = {"model": "sales.salesorder", "id": self.acme_order.pk}

    def plan(self, client=None, **extra):
        body = {**self.on_acme, "kind": "call", "summary": "Ask about the October schedule",
                "due_on": str(self.today + datetime.timedelta(days=1)), "link": f"/sales/orders/{self.acme_order.pk}",
                **extra}
        return (client or self.a).post(FOLLOW_UPS, body, format="json")


class OnlyWhereTheRecordMayBeReadTests(ChatterTestCase):
    def test_another_reps_order_has_no_notes_follow_ups_history_or_files_to_them(self):
        self.assertEqual(self.a.post(NOTES, {**self.on_acme, "body": "Wants 50-kg sacks next time"},
                                     format="json").status_code, 201)
        self.assertEqual(self.plan().status_code, 201)
        for url in (NOTES, FOLLOW_UPS, "/api/core/history/", "/api/core/attachments/"):
            with self.subTest(url=url):
                self.assertEqual(self.a.get(url, self.on_acme).status_code, 200)
                self.assertEqual(self.b.get(url, self.on_acme).status_code, 404)
        self.assertEqual(self.b.post(NOTES, {**self.on_acme, "body": "Mine now"}, format="json").status_code, 404)

    def test_nobody_is_given_a_follow_up_on_a_record_they_cannot_open(self):
        refused = self.plan(assigned_to=self.rep_b.pk)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("cannot open this record", refused.json()["assigned_to"][0])
        self.assertFalse(FollowUp.objects.exists())

    def test_a_link_stays_inside_the_application(self):
        refused = self.plan(link="https://elsewhere.example/phish")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("link", refused.json())


class NotesTests(ChatterTestCase):
    def test_a_note_is_kept_as_written_and_removed_only_by_its_writer(self):
        made = self.a.post(NOTES, {**self.on_acme, "body": "  Wants 50-kg sacks next time  "}, format="json").json()
        note = Note.objects.get(pk=made["id"])
        self.assertEqual(note.body, "Wants 50-kg sacks next time")
        note.body = "Something else"
        with self.assertRaisesMessage(ValidationError, "kept as it was written"):
            note.save()
        manager = self.as_role("AR Manager")
        self.assertEqual(manager.delete(f"{NOTES}{note.pk}/").status_code, 403)
        self.assertEqual(self.a.delete(f"{NOTES}{note.pk}/").status_code, 204)

    def test_an_empty_note_says_nothing(self):
        refused = self.a.post(NOTES, {**self.on_acme, "body": "   "}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("body", refused.json())


class FollowUpTests(ChatterTestCase):
    def test_done_is_a_note_on_the_record_and_a_fact(self):
        planned = self.plan().json()
        self.assertEqual(planned["state"], "planned")
        done = self.a.post(f"{FOLLOW_UPS}{planned['id']}/done/", {"outcome": "They confirm 40,000 sacks"},
                           format="json")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual((done.json()["state"], done.json()["done_on"]), ("done", str(self.today)))
        self.assertEqual(Note.objects.get().body, "Call done: Ask about the October schedule. They confirm 40,000 sacks")
        again = self.a.post(f"{FOLLOW_UPS}{planned['id']}/done/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(Note.objects.count(), 1)
        self.assertEqual(self.a.patch(f"{FOLLOW_UPS}{planned['id']}/", {"due_on": str(self.today)},
                                      format="json").status_code, 400)
        self.assertEqual(self.a.delete(f"{FOLLOW_UPS}{planned['id']}/").status_code, 400)

    def test_mine_are_my_open_ones_by_day_with_where_they_are(self):
        late = self.plan(due_on=str(self.today - datetime.timedelta(days=2))).json()
        today = self.plan(due_on=str(self.today), summary="Send the test certificate").json()
        done = self.plan().json()
        self.a.post(f"{FOLLOW_UPS}{done['id']}/done/", {}, format="json")
        rows = self.a.get(FOLLOW_UPS, {"mine": "true"}).json()
        self.assertEqual([(row["id"], row["state"]) for row in rows], [(late["id"], "overdue"), (today["id"], "today")])
        self.assertEqual((rows[0]["record_kind"], rows[0]["link"]), ("Sales order", f"/sales/orders/{self.acme_order.pk}"))
        self.assertEqual(self.b.get(FOLLOW_UPS, {"mine": "true"}).json(), [])

    def test_rescheduled_or_handed_on_by_its_people_only(self):
        planned = self.plan().json()
        stranger = User.objects.create_user("stranger")  # a rep's role, linked to no one: sees no customer
        stranger.groups.add(*self.rep_a.groups.all())
        self.assertEqual(self.as_user(stranger).patch(f"{FOLLOW_UPS}{planned['id']}/", {"summary": "x"},
                                                      format="json").status_code, 404)
        moved = self.a.patch(f"{FOLLOW_UPS}{planned['id']}/", {"due_on": str(self.today + datetime.timedelta(days=7))},
                             format="json")
        self.assertEqual(moved.status_code, 200, moved.content)
        handed = self.a.patch(f"{FOLLOW_UPS}{planned['id']}/", {"assigned_to": self.rep_b.pk}, format="json")
        self.assertEqual(handed.status_code, 400)
        self.assertEqual(FollowUp.objects.get().assigned_to, self.rep_a)

    def test_the_office_application_can_name_a_screens_kind(self):
        kinds = self.a.get("/api/core/endpoints/").json()
        self.assertEqual(kinds["/api/sales/sales-orders/"], "sales.salesorder")
        self.assertEqual(kinds["/api/core/parties/"], "core.party")
