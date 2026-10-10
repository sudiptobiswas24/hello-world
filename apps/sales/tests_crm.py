"""
Dana, a rep, takes a lead from the March exhibition: Shree Cement asked
for 20,000 sacks a month. She logs the call with a follow-up for
tomorrow, converts the lead to customer C-SHREE (hers to carry) and its
first opportunity, values it at 250,000 and quotes it (chance 60%:
weighted 150,000.00), then wins it. The campaign brought one lead, one
customer and 250,000.00 won.

Ravi, another rep, sees none of Dana's; a lead nobody owns he may take.
Converted and lost stay as they were.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .crm import Activity, Campaign, Lead, Opportunity, follow_ups_due
from .models import CustomerProfile, Quotation
from .tests_base import SalesTestCase, carries_every_customer

TODAY = timezone.localdate()
TOMORROW = TODAY + datetime.timedelta(days=1)


class CrmTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.expo = Campaign.objects.create(code="EXPO26", name="PlastIndia March 2026", channel="exhibition",
                                            starts_on=datetime.date(2026, 3, 1), ends_on=datetime.date(2026, 3, 5),
                                            budget=Decimal("150000"))
        self.dana, self.dana_party = self.rep_login("dana")
        self.ravi, self.ravi_party = self.rep_login("ravi")
        self.manager = self.as_("AR Manager")

    def as_(self, role, username=None):
        user = User.objects.create_user(username or role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        client.user = user
        return client

    def rep_login(self, name):
        client = self.as_("Sales Rep", name)
        carries_every_customer(client.user)
        from apps.core.scoping import party_of

        return client, party_of(client.user)

    def lead(self, client=None, **extra):
        values = {"company_name": "Shree Cement", "contact_name": "R. Mehta", "phone": "98200 11111",
                  "city": "Pune", "source": "exhibition", "campaign": self.expo.pk,
                  "interest": "20,000 cement sacks a month"}
        values.update(extra)
        made = (client or self.dana).post("/api/sales/leads/", values, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        return made.json()


class LeadTests(CrmTestCase):
    def test_a_reps_lead_is_theirs_and_numbered(self):
        lead = self.lead()
        self.assertTrue(lead["number"].startswith("LD-"))
        self.assertEqual((lead["owner"], lead["status"]), (self.dana_party.pk, "new"))
        self.assertEqual([row["number"] for row in self.ravi.get("/api/sales/leads/").json()], [])
        self.assertEqual(len(self.manager.get("/api/sales/leads/").json()), 1)
        self.assertEqual(self.ravi.get(f"/api/sales/leads/{lead['id']}/").status_code, 404)

    def test_a_rep_sets_no_other_owner_and_the_manager_may(self):
        refused = self.dana.post("/api/sales/leads/", {"company_name": "Nowhere Ltd", "owner": self.ravi_party.pk},
                                 format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("the owner is you", refused.content.decode())
        given = self.manager.post("/api/sales/leads/", {"company_name": "Nowhere Ltd", "owner": self.ravi_party.pk},
                                  format="json")
        self.assertEqual(given.status_code, 201, given.content)
        self.assertEqual(given.json()["owner"], self.ravi_party.pk)
        not_a_rep = self.manager.post("/api/sales/leads/", {"company_name": "Nowhere Ltd", "owner": self.customer.pk},
                                      format="json")
        self.assertEqual(not_a_rep.status_code, 400)

    def test_a_lead_nobody_owns_is_taken_by_the_rep_who_will_carry_it(self):
        unowned = self.manager.post("/api/sales/leads/", {"company_name": "Open Field Fertilisers"}, format="json").json()
        self.assertIsNone(unowned["owner"])
        self.assertEqual([row["id"] for row in self.ravi.get("/api/sales/leads/").json()], [unowned["id"]])
        taken = self.ravi.post(f"/api/sales/leads/{unowned['id']}/take/")
        self.assertEqual(taken.status_code, 200, taken.content)
        self.assertEqual(taken.json()["owner"], self.ravi_party.pk)
        self.assertEqual(self.dana.get("/api/sales/leads/").json(), [])
        self.assertEqual(self.dana.post(f"/api/sales/leads/{unowned['id']}/take/").status_code, 404)
        self.assertEqual(self.manager.post(f"/api/sales/leads/{unowned['id']}/take/").status_code, 400)

    def test_lost_with_a_reason_and_then_as_it_was(self):
        lead = self.lead()
        self.assertEqual(self.dana.post(f"/api/sales/leads/{lead['id']}/lose/", {"reason": ""}, format="json").status_code, 400)
        lost = self.dana.post(f"/api/sales/leads/{lead['id']}/lose/", {"reason": "Went with a local supplier"},
                              format="json")
        self.assertEqual((lost.status_code, lost.json()["status"], lost.json()["lost_reason"]),
                         (200, "lost", "Went with a local supplier"))
        self.assertEqual(self.dana.patch(f"/api/sales/leads/{lead['id']}/", {"city": "Mumbai"}, format="json").status_code, 400)
        self.assertEqual(self.dana.delete(f"/api/sales/leads/{lead['id']}/").status_code, 400)
        self.assertEqual(self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-X"}, format="json").status_code, 400)


class ConversionTests(CrmTestCase):
    def test_converted_to_a_customer_the_rep_carries_and_its_first_opportunity(self):
        lead = self.lead()
        taken = self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-SHREE"}, format="json")
        self.assertEqual(taken.status_code, 200, taken.content)
        party = Party.objects.get(code="C-SHREE")
        self.assertEqual((party.name, party.phone), ("Shree Cement", "98200 11111"))
        self.assertTrue(PartyRoleAssignment.objects.filter(party=party, role=PartyRole.CUSTOMER).exists())
        self.assertEqual(CustomerProfile.objects.get(party=party).sales_rep, self.dana_party)
        opportunity = Opportunity.objects.get(lead_id=lead["id"])
        self.assertEqual((opportunity.customer, opportunity.owner, opportunity.campaign, opportunity.stage,
                          opportunity.title), (party, self.dana_party, self.expo, "new", "20,000 cement sacks a month"))
        self.assertTrue(opportunity.number.startswith("OP-"))
        found = Lead.objects.get(pk=lead["id"])
        self.assertEqual((found.status, found.converted_party, found.converted_on), ("converted", party, TODAY))
        # Closed: converted twice, lost after, edited after, all refused.
        self.assertEqual(self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-SHREE2"}, format="json").status_code, 400)
        self.assertEqual(self.dana.post(f"/api/sales/leads/{lead['id']}/lose/", {"reason": "x"}, format="json").status_code, 400)
        self.assertEqual(self.dana.patch(f"/api/sales/leads/{lead['id']}/", {"city": "Mumbai"}, format="json").status_code, 400)

    def test_a_code_in_use_and_no_code_are_refused_before_anything_is_made(self):
        lead = self.lead()
        taken = self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": self.customer.code}, format="json")
        self.assertEqual(taken.status_code, 400)
        self.assertIn("already a party", taken.content.decode())
        self.assertEqual(self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {}, format="json").status_code, 400)
        self.assertEqual((Opportunity.objects.count(), Lead.objects.get(pk=lead["id"]).status), (0, "new"))

    def test_whoever_may_not_make_a_customer_may_not_convert(self):
        lead = self.lead()
        bookkeeper = self.as_("Bookkeeper")
        self.assertEqual(bookkeeper.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-X"}, format="json").status_code, 403)


class OpportunityTests(CrmTestCase):
    def opportunity(self):
        lead = self.lead()
        self.dana.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-SHREE"}, format="json")
        return Opportunity.objects.get(lead_id=lead["id"])

    def test_valued_quoted_weighted_and_won(self):
        opportunity = self.opportunity()
        url = f"/api/sales/opportunities/{opportunity.pk}/"
        valued = self.dana.patch(url, {"value": "250000", "expected_on": "2026-06-30"}, format="json")
        self.assertEqual(valued.status_code, 200, valued.content)
        self.assertEqual((valued.json()["chance"], valued.json()["weighted_value"]), (10, "25000.00"))
        quoted = self.dana.post(url + "quote/", {"quotation_date": str(TODAY), "valid_until": str(TODAY + datetime.timedelta(days=30))},
                                format="json")
        self.assertEqual(quoted.status_code, 201, quoted.content)
        quotation = Quotation.objects.get(pk=quoted.json()["quotation"])
        self.assertEqual((quotation.customer, quotation.sales_rep, quotation.reference),
                         (opportunity.customer, self.dana_party, opportunity.number))
        self.assertEqual((quoted.json()["opportunity"]["stage"], quoted.json()["opportunity"]["weighted_value"]),
                         ("quoted", "150000.00"))
        self.assertEqual(self.dana.post(url + "quote/", {"quotation_date": str(TODAY)}, format="json").status_code, 400)
        [row] = [row for row in self.dana.get("/api/sales/opportunities/pipeline/").json() if row["stage"] == "quoted"]
        self.assertEqual((row["count"], row["value"], row["weighted"]), (1, "250000.00", "150000.00"))
        self.assertEqual([row["count"] for row in self.ravi.get("/api/sales/opportunities/pipeline/").json()], [0, 0, 0, 0, 0])

        won = self.dana.post(url + "win/", {}, format="json")
        self.assertEqual((won.status_code, won.json()["stage"], won.json()["closed_on"], won.json()["chance"]),
                         (200, "won", str(TODAY), 100))
        self.assertEqual(self.dana.post(url + "win/", {}, format="json").status_code, 400)
        self.assertEqual(self.dana.post(url + "lose/", {"reason": "x"}, format="json").status_code, 400)
        self.assertEqual(self.dana.patch(url, {"value": "1"}, format="json").status_code, 400)
        self.assertEqual(self.dana.delete(url).status_code, 400)
        results = self.manager.get(f"/api/sales/campaigns/{self.expo.pk}/results/").json()
        self.assertEqual((results["leads"], results["converted"], results["opportunities"], results["won"],
                          results["won_value"], results["open_value"]), (1, 1, 1, 1, "250000.00", "0.00"))

    def test_lost_says_why_and_a_stage_is_not_closed_by_hand(self):
        opportunity = self.opportunity()
        url = f"/api/sales/opportunities/{opportunity.pk}/"
        self.assertEqual(self.dana.patch(url, {"stage": "won"}, format="json").status_code, 400)
        self.assertEqual(self.dana.post(url + "lose/", {"reason": " "}, format="json").status_code, 400)
        lost = self.dana.post(url + "lose/", {"reason": "Price"}, format="json")
        self.assertEqual((lost.status_code, lost.json()["stage"], lost.json()["lost_reason"]), (200, "lost", "Price"))

    def test_only_on_a_customer_the_rep_carries_and_only_the_reps_own_order(self):
        other = Party.objects.create(code="C-OTHER", name="Not Dana's")
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        refused = self.dana.post("/api/sales/opportunities/", {"customer": other.pk, "title": "Liners"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Not a customer you carry", refused.content.decode())
        made = self.manager.post("/api/sales/opportunities/", {"customer": other.pk, "title": "Liners"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        with self.assertRaisesMessage(ValidationError, "is not a customer"):
            Opportunity.objects.create(customer=self.rep_party_of_nobody(), title="x")

    def rep_party_of_nobody(self):
        return Party.objects.create(code="NOT-A-CUSTOMER", name="Granules Ltd")


class ActivityTests(CrmTestCase):
    def test_logged_against_one_thing_and_followed_up(self):
        lead = self.lead()
        call = self.dana.post("/api/sales/activities/", {
            "kind": "call", "lead": lead["id"], "summary": "Spoke to Mehta; wants samples", "due_on": str(TOMORROW)},
            format="json")
        self.assertEqual(call.status_code, 201, call.content)
        self.assertEqual((call.json()["owner"], call.json()["about"]), (self.dana_party.pk, f"{lead['number']} Shree Cement"))
        two_things = self.dana.post("/api/sales/activities/", {
            "kind": "note", "lead": lead["id"], "party": self.customer.pk, "summary": "x"}, format="json")
        self.assertEqual(two_things.status_code, 400)
        self.assertEqual(self.ravi.get("/api/sales/activities/").json(), [])
        self.assertEqual((follow_ups_due(self.dana.user, TODAY), follow_ups_due(self.dana.user, TOMORROW),
                          follow_ups_due(self.ravi.user, TOMORROW)), (0, 1, 0))
        self.assertEqual(self.dana.get("/api/sales/activities/due/").json(), {"due": 0})
        done = self.dana.post(f"/api/sales/activities/{call.json()['id']}/done/", {}, format="json")
        self.assertEqual((done.status_code, done.json()["done_on"]), (200, str(TODAY)))
        self.assertEqual(self.dana.post(f"/api/sales/activities/{call.json()['id']}/done/", {}, format="json").status_code, 400)
        self.assertEqual(follow_ups_due(self.dana.user, TOMORROW), 0)

    def test_in_the_morning_inbox_as_the_reps_own(self):
        from apps.web.checks import inbox

        lead = self.lead()
        Activity.objects.create(kind="follow_up", lead_id=lead["id"], owner=self.dana_party, summary="Send samples",
                                due_on=TODAY - datetime.timedelta(days=1))
        dana = {row["key"]: row["count"] for row in inbox(self.dana.user)}
        self.assertEqual(dana.get("follow_ups_due"), 1)
        self.assertNotIn("follow_ups_due", {row["key"] for row in inbox(self.ravi.user)})


class TheScoreCountsCallsAndVisitsTests(CrmTestCase):
    """
    Audit, 9 October: every activity done counted as "a call or visit made",
    a note that found their website among them. A lead from nowhere in
    particular, asked today: fresh +10. A call and a visit done add 5 each;
    a note and a mail done add nothing: 20.
    """

    def test_a_note_is_not_a_call_or_visit(self):
        lead = Lead.objects.create(company_name="Quiet Traders")
        Activity.objects.create(lead=lead, kind="note", summary="Found their website").done()
        self.assertEqual(Lead.objects.get(pk=lead.pk).score(), 10)

    def test_only_calls_and_visits_count_and_the_list_says_the_same(self):
        lead = Lead.objects.create(company_name="Quiet Traders")
        for kind in ("call", "visit", "note", "email"):
            Activity.objects.create(lead=lead, kind=kind, summary=f"A {kind}").done()
        Activity.objects.create(lead=lead, kind="call", summary="Not yet rung")
        self.assertEqual(Lead.objects.get(pk=lead.pk).score(), 20)
        [row] = [row for row in self.manager.get("/api/sales/leads/").json() if row["id"] == lead.pk]
        self.assertEqual((row["score"], row["score_summary"]),
                         (20, "from other +0, 2 calls or visits made +10, fresh: asked within a fortnight +10"))


class CampaignTests(CrmTestCase):
    def test_kept_by_the_manager_and_read_by_the_rep(self):
        refused = self.manager.post("/api/sales/campaigns/", {
            "code": "DIG26", "name": "Digital", "starts_on": "2026-04-01", "ends_on": "2026-03-01"}, format="json")
        self.assertEqual(refused.status_code, 400)
        made = self.manager.post("/api/sales/campaigns/", {
            "code": "DIG26", "name": "Digital", "channel": "digital", "starts_on": "2026-04-01", "budget": "20000"},
            format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(self.dana.get("/api/sales/campaigns/").status_code, 200)
        self.assertEqual(self.dana.post("/api/sales/campaigns/", {"code": "X", "name": "x", "starts_on": "2026-04-01"},
                                        format="json").status_code, 403)


class RepScopeTests(CrmTestCase):
    """
    O119: a rep who converted an unowned lead could not see the opportunity
    it made (no owner). O120: a rep logged calls on another rep's lead,
    raising its score. One scope: what the rep may read is what they may
    write about.
    """

    def test_a_rep_who_converts_an_unowned_lead_owns_its_opportunity(self):
        unowned = self.manager.post("/api/sales/leads/", {"company_name": "Open Field Fertilisers"},
                                    format="json").json()
        converted = self.ravi.post(f"/api/sales/leads/{unowned['id']}/convert/", {"code": "C-OPEN"}, format="json")
        self.assertEqual(converted.status_code, 200, converted.content)
        opportunity = Opportunity.objects.get(pk=converted.json()["opportunity"])
        seen = self.ravi.get(f"/api/sales/opportunities/{opportunity.pk}/").status_code
        self.assertEqual((opportunity.owner_id, seen), (self.ravi_party.pk, 200))

    def test_an_owned_lead_keeps_its_owner_whoever_converts_it(self):
        lead = self.lead()  # Dana's
        converted = self.manager.post(f"/api/sales/leads/{lead['id']}/convert/", {"code": "C-DANA"}, format="json")
        self.assertEqual(converted.status_code, 200, converted.content)
        self.assertEqual(Opportunity.objects.get(pk=converted.json()["opportunity"]).owner_id, self.dana_party.pk)

    def test_a_rep_cannot_log_a_call_on_another_reps_lead(self):
        lead = self.lead()  # Dana's
        before = Lead.objects.get(pk=lead["id"]).score()
        self.assertEqual(before, 70)
        made = self.ravi.post("/api/sales/activities/", {"lead": lead["id"], "kind": "call", "summary": "Rang them"},
                              format="json")
        self.assertEqual((made.status_code, Activity.objects.filter(lead_id=lead["id"]).count()), (400, 0),
                         made.content)
        self.assertEqual(Lead.objects.get(pk=lead["id"]).score(), before)

    def test_nor_on_another_reps_opportunity_but_on_their_own(self):
        theirs = Opportunity.objects.create(customer=self.customer, title="Dana's", owner=self.dana_party)
        refused = self.ravi.post("/api/sales/activities/", {"opportunity": theirs.pk, "kind": "call",
                                                             "summary": "Rang them"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        made = self.dana.post("/api/sales/activities/", {"opportunity": theirs.pk, "kind": "call",
                                                          "summary": "Rang them"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        moved = self.ravi.patch(f"/api/sales/activities/{made.json()['id']}/", {"summary": "x"}, format="json")
        self.assertEqual(moved.status_code, 404)
