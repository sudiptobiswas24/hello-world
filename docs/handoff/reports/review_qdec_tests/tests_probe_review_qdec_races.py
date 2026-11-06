"""review_qdec: races and commit-time behaviour for decision 22 (PostgreSQL for the races)."""

import datetime
import unittest
from unittest import mock

from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import tag
from rest_framework.test import APIClient

from apps.core.chatter import FollowUp
from apps.e2e.tests_races import RaceCase, fixture, race
from apps.quality import tests_decisions as decisions_fixture
from apps.quality.models import Disposition, Inspection
from apps.quality.release import release_status

pg = unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")


def todos(inspection, **kw):
    return FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(Inspection),
                                   object_id=inspection.pk, **kw)


@tag("race")
@pg
class DecisionRaces(RaceCase):
    def test_X01_decide_and_void_at_once(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        inspection = made.awaiting()
        asha = made.users["asha"]
        outcomes = race(
            Inspection,
            lambda: Inspection.objects.get(pk=inspection.pk).decide(User.objects.get(pk=asha.pk),
                                                                    Disposition.REJECT, "scrap"),
            lambda: Inspection.objects.get(pk=inspection.pk).void("wrong lot", by=User.objects.get(pk=asha.pk)))
        inspection.refresh_from_db()
        self.assertEqual(todos(inspection, done_on__isnull=True).count(), 0, outcomes)
        if inspection.voided_at:
            self.assertEqual(release_status(inspection.lot), "uninspected", outcomes)
        else:
            self.assertEqual((inspection.disposition, release_status(inspection.lot)), ("reject", "held"), outcomes)

    def test_X02_a_failing_and_a_passing_inspection_posting_at_once(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        failing = made.failed()
        passing = made.inspect(made.advisory, [88, 88, 88])
        outcomes = race(
            Inspection,
            lambda: Inspection.objects.get(pk=failing.pk).post(),
            lambda: Inspection.objects.get(pk=passing.pk).post())
        failing.refresh_from_db()
        passing.refresh_from_db()
        if failing.posted and passing.posted:
            self.assertLess(passing.posted_at, failing.posted_at,
                            f"a pass posted after an awaiting failure stands beside it: {outcomes}")
        self.assertEqual(release_status(failing.lot), "held" if failing.posted else release_status(failing.lot))
        self.assertTrue(failing.posted, outcomes)
        self.assertEqual(release_status(failing.lot), "held", outcomes)

    def test_X03_two_escalations_at_once_tell_each_administrator_once(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        root = User.objects.create_superuser("root", "root@plant.example", "x")
        inspection = made.awaiting()
        Inspection.objects.filter(pk=inspection.pk).update(
            posted_at=datetime.datetime(2026, 6, 1, 10, tzinfo=datetime.timezone.utc))
        day = datetime.date(2026, 6, 4)
        outcomes = race(FollowUp, lambda: Inspection.objects.get(pk=inspection.pk).escalate(day),
                        lambda: Inspection.objects.get(pk=inspection.pk).escalate(day))
        self.assertEqual(todos(inspection, assigned_to=root).count(), 1, outcomes)

    def test_X04_decide_and_escalate_at_once_leave_nothing_open(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        root = User.objects.create_superuser("root", "root@plant.example", "x")
        inspection = made.awaiting()
        Inspection.objects.filter(pk=inspection.pk).update(
            posted_at=datetime.datetime(2026, 6, 1, 10, tzinfo=datetime.timezone.utc))
        asha = made.users["asha"]
        day = datetime.date(2026, 6, 4)
        outcomes = race((Inspection, FollowUp),
                        lambda: Inspection.objects.get(pk=inspection.pk).decide(
                            User.objects.get(pk=asha.pk), Disposition.REWORK, "re-wind"),
                        lambda: Inspection.objects.get(pk=inspection.pk).escalate(day))
        self.assertEqual(todos(inspection, done_on__isnull=True).count(), 0, outcomes)

    def test_X05_two_failing_inspections_of_one_lot_posting_at_once_leave_one_waiting(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        one, two = made.failed(), made.failed()
        outcomes = race(Inspection, lambda: Inspection.objects.get(pk=one.pk).post(),
                        lambda: Inspection.objects.get(pk=two.pk).post())
        self.once(outcomes)
        self.assertEqual(Inspection.objects.filter(posted=True, disposition="awaiting").count(), 1)
        self.assertEqual(sum(todos(i).count() for i in (one, two)), 3)


class OnCommit(RaceCase):
    """Real commits (no test transaction): what the inspector is answered when the mail errors after the commit."""

    def test_X06_an_unexpected_mailer_error_after_the_commit_still_answers_the_post(self):
        made = fixture(self, decisions_fixture.AwaitingTestCase)
        inspection = made.failed()
        client = made.clients["meera"]
        client.raise_request_exception = False
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=RuntimeError("boom")):
            response = client.post(f"/api/quality/inspections/{inspection.pk}/post/")
        inspection.refresh_from_db()
        self.assertTrue(inspection.posted)
        self.assertEqual(response.status_code, 200,
                         f"posted and committed, but the inspector was answered {response.status_code}")
