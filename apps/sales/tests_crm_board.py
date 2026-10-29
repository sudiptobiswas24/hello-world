"""
The warm score, the mail to a lead and the pipeline board.

Shree Cement came from the exhibition (+20), left a phone number (+10)
and a contact (+5), said what they want (+15), through a campaign (+5)
with Dana on it (+5), and asked this fortnight (+10): 70 of 100. A
cold "other" lead with nothing but a name scores 10 (fresh), and 0
once stale. The score is read from the facts each time, never stored,
and a lead converted or lost is not scored at all.
"""

import datetime
from decimal import Decimal

from django.core import mail
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.history import RecordEvent

from .crm import Activity, ActivityKind, Lead, Opportunity
from .models import SalesRep
from .tests_crm import CrmTestCase


class ScoreTests(CrmTestCase):
    def test_read_from_the_facts_and_said_why(self):
        warm = Lead.objects.get(pk=self.lead(email="r.mehta@shree.example")["id"])
        self.assertEqual(warm.score(), 80)
        self.assertEqual([why for _, why in warm.score_reasons()],
                         ["from exhibition", "a phone number", "an email address", "a named contact", "said what they want",
                          "from a campaign", "a rep on it", "fresh: asked within a fortnight"])
        cold = Lead.objects.get(pk=self.lead(company_name="Nobody Much", contact_name="", phone="", city="", source="other",
                                             campaign=None, interest="", owner=None)["id"])
        # A limited rep's lead is theirs even when they name nobody: +5 for a rep on it.
        self.assertEqual(cold.score(), 15)
        Lead.objects.filter(pk=cold.pk).update(created_at=cold.created_at - datetime.timedelta(days=120))
        cold.refresh_from_db()
        self.assertEqual((cold.score(), cold.score_reasons()[-1]), (0, (-15, "stale: 120 days without becoming a customer")))
        Activity.objects.create(kind=ActivityKind.CALL, lead=warm, owner=self.dana_party, summary="Called", done_on=datetime.date(2026, 6, 1))
        self.assertEqual(warm.score(), 85)

    def test_the_office_reads_it_and_keeps_the_warm_ones(self):
        warm = self.lead()
        self.lead(company_name="Nobody Much", contact_name="", phone="", city="", source="other", campaign=None, interest="", owner=None)
        seen = self.dana.get(f"/api/sales/leads/{warm['id']}/").json()
        self.assertEqual((seen["score"], seen["score_summary"][:22]), (70, "from exhibition +20, a"))
        self.assertEqual([row["id"] for row in self.dana.get("/api/sales/leads/", {"min_score": "60"}).json()], [warm["id"]])
        self.assertEqual(len(self.dana.get("/api/sales/leads/").json()), 2)
        self.assertEqual(self.dana.get("/api/sales/leads/", {"min_score": "warm"}).status_code, 400)
        # Lost, it is no longer warm: not scored, and not among the hot ones.
        self.assertEqual(self.dana.post(f"/api/sales/leads/{warm['id']}/lose/", {"reason": "Went elsewhere"}).status_code, 200)
        gone = self.dana.get(f"/api/sales/leads/{warm['id']}/").json()
        self.assertEqual((gone["score"], gone["score_summary"]), (0, "not scored: lost"))
        self.assertEqual(self.dana.get("/api/sales/leads/", {"min_score": "60"}).json(), [])

    def test_a_page_of_leads_counts_their_calls_once(self):
        """The score reads what was done; a page asks the database once for all of them, not once a lead."""
        def queries():
            with CaptureQueriesContext(connection) as seen:
                rows = self.dana.get("/api/sales/leads/").json()
            return len(seen), len(rows)

        self.lead()
        with_one = queries()
        for n in range(4):
            Activity.objects.create(kind=ActivityKind.CALL, lead_id=self.lead(company_name=f"Lead {n}")["id"],
                                    owner=self.dana_party, summary="Called", done_on=datetime.date(2026, 6, 1))
        with_five = queries()
        self.assertEqual((with_one[1], with_five[1]), (1, 5))
        self.assertEqual(with_one[0], with_five[0])
        self.assertEqual([row["score"] for row in self.dana.get("/api/sales/leads/", {"ordering": "company_name"}).json()],
                         [75, 75, 75, 75, 70])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MailTests(CrmTestCase):
    def test_a_mail_goes_to_the_lead_and_is_written_down_twice(self):
        lead = self.lead(email="r.mehta@shree.example")
        sent = self.dana.post(f"/api/sales/leads/{lead['id']}/send/", {"subject": "Your sack enquiry",
                                                                       "body": "Thank you for visiting our stall."}, format="json")
        self.assertEqual((sent.status_code, sent.json()), (200, {"sent_to": "r.mehta@shree.example"}), sent.content)
        self.assertEqual((len(mail.outbox), mail.outbox[0].subject, mail.outbox[0].to), (1, "Your sack enquiry", ["r.mehta@shree.example"]))
        activity = Activity.objects.get(lead_id=lead["id"])
        self.assertEqual((activity.kind, activity.summary, activity.done_on, activity.owner), ("email", "Your sack enquiry", timezone.localdate(), self.dana_party))
        self.assertEqual([event.summary for event in RecordEvent.objects.filter(object_id=lead["id"], kind="mail")],
                         ["Mail 'Your sack enquiry' to r.mehta@shree.example"])
        no_address = self.lead(company_name="Quiet Co", email="")
        refused = self.dana.post(f"/api/sales/leads/{no_address['id']}/send/", {"subject": "Hello"}, format="json")
        self.assertEqual((refused.status_code, "no email address" in refused.content.decode()), (400, True), refused.content)
        blank = self.dana.post(f"/api/sales/leads/{lead['id']}/send/", {"subject": " ", "body": "x"}, format="json")
        self.assertEqual(blank.status_code, 400, blank.content)
        self.assertEqual(len(mail.outbox), 1)
        # The lead's rep has left: the manager is refused before anything goes, and nothing is written down.
        SalesRep.objects.filter(party=self.dana_party).update(is_active=False)
        refused = self.manager.post(f"/api/sales/leads/{lead['id']}/send/", {"subject": "Still there?", "body": "x"}, format="json")
        self.assertEqual((refused.status_code, "not an active sales rep" in refused.content.decode()), (400, True), refused.content)
        self.assertEqual((len(mail.outbox), Activity.objects.filter(lead_id=lead["id"]).count()), (1, 1))


class BoardTests(CrmTestCase):
    def test_open_opportunities_by_stage_as_the_rep_sees_them(self):
        mine = Opportunity.objects.create(customer=self.customer, title="20,000 sacks a month", owner=self.dana_party,
                                          value=Decimal("1200000"), expected_on=datetime.date(2026, 7, 1))
        quoted = Opportunity.objects.create(customer=self.customer, title="Liner bags", owner=self.dana_party, stage="quoted",
                                            value=Decimal("300000"))
        Opportunity.objects.create(customer=self.customer, title="Ravi's", owner=self.ravi_party, value=Decimal("5"))
        won = Opportunity.objects.create(customer=self.customer, title="Done", owner=self.dana_party, value=Decimal("9"))
        won.win()
        board = self.dana.get("/api/sales/opportunities/board/")
        self.assertEqual(board.status_code, 200, board.content)
        self.assertEqual([(column["stage"], [card["title"] for card in column["cards"]]) for column in board.json()],
                         [("new", ["20,000 sacks a month"]), ("qualified", []), ("quoted", ["Liner bags"])])
        card = board.json()[0]["cards"][0]
        self.assertEqual((card["customer"], card["value"], card["chance"], card["weighted"], card["expected_on"], card["owner"]),
                         (self.customer.name, "1200000.00", 10, "120000.00", "2026-07-01", self.dana_party.name))
        moved = self.dana.patch(f"/api/sales/opportunities/{mine.pk}/", {"stage": "qualified"}, format="json")
        self.assertEqual((moved.status_code, moved.json()["stage"]), (200, "qualified"), moved.content)
        everyone = self.manager.get("/api/sales/opportunities/board/").json()
        self.assertEqual([len(column["cards"]) for column in everyone], [1, 1, 1])
        self.assertEqual(quoted.stage, "quoted")
