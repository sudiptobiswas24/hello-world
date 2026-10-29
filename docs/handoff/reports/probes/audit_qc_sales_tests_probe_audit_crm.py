"""
Audit probes, 9 October: the CRM (apps/sales/crm.py, crm_api.py). Each
test states one claim and fails with observed against expected. Figures
worked in scratchpad/calc/expected.py.
"""

from decimal import Decimal

from django.test import override_settings

from .crm import Activity, ActivityKind, Lead, Opportunity, pipeline
from .tests_crm import CrmTestCase


class RepScopeProbe(CrmTestCase):
    def test_probe_a_rep_who_converts_an_unowned_lead_can_see_its_opportunity(self):
        unowned = self.manager.post("/api/sales/leads/", {"company_name": "Open Field Fertilisers"},
                                    format="json").json()
        converted = self.ravi.post(f"/api/sales/leads/{unowned['id']}/convert/", {"code": "C-OPEN"},
                                   format="json")
        self.assertEqual(converted.status_code, 200, converted.content)
        opportunity = Opportunity.objects.get(pk=converted.json()["opportunity"])
        seen = self.ravi.get(f"/api/sales/opportunities/{opportunity.pk}/").status_code
        self.assertEqual(
            (opportunity.owner_id, seen), (self.ravi_party.pk, 200),
            f"Ravi converted the unowned lead and carries C-OPEN, but its opportunity "
            f"{opportunity.number} has owner {opportunity.owner_id} and Ravi reads it as {seen}; "
            f"expected owner {self.ravi_party.pk} and 200.",
        )

    def test_probe_a_rep_cannot_log_a_call_on_another_reps_lead(self):
        lead = self.lead()  # Dana's
        before = Lead.objects.get(pk=lead["id"]).score()
        made = self.ravi.post("/api/sales/activities/", {"lead": lead["id"], "kind": "call",
                                                         "summary": "Rang them"}, format="json")
        if made.status_code in (400, 403, 404):
            return
        self.ravi.post(f"/api/sales/activities/{made.json()['id']}/done/", {}, format="json")
        after = Lead.objects.get(pk=lead["id"]).score()
        self.fail(
            f"Ravi logged a call on Dana's lead ({made.status_code}), reading back "
            f"about={made.json().get('about')!r}; Dana's lead score went {before} -> {after}. "
            "Expected the activity refused and the score left at 70."
        )


class WonTwiceProbe(CrmTestCase):
    def test_probe_one_order_wins_one_opportunity(self):
        order = self.make_order("10", "100")
        first = Opportunity.objects.create(customer=self.customer, title="Cement sacks",
                                           value=Decimal("250000"))
        second = Opportunity.objects.create(customer=self.customer, title="Cement sacks again",
                                            value=Decimal("100000"))
        first.win(order)
        try:
            second.win(order)
        except Exception:
            return
        won = next(row for row in pipeline(self.manager.user) if row["stage"] == "won")
        self.fail(
            f"{order.number} won both {first.number} and {second.number}: won value "
            f"{won['value']} over {won['count']} opportunities; expected the second refused "
            "and 250000.00."
        )


class ScoreProbe(CrmTestCase):
    def test_probe_a_note_is_not_a_call_or_visit(self):
        lead = Lead.objects.create(company_name="Quiet Traders")
        note = Activity.objects.create(lead=lead, kind=ActivityKind.NOTE, summary="Found their website")
        note.done()
        lead = Lead.objects.get(pk=lead.pk)
        self.assertEqual(
            lead.score(), 10,
            f"Score {lead.score()} ({lead.score_summary()}): a note done counts as a call or "
            "visit made; expected 10 (fresh only).",
        )


class MailProbe(CrmTestCase):
    @override_settings(EMAIL_BACKEND="apps.core.mail.NotConfiguredBackend")
    def test_mail_with_no_server_writes_no_activity(self):
        lead = self.lead(email="buyer@example.com")
        sent = self.dana.post(f"/api/sales/leads/{lead['id']}/send/", {"subject": "Our rates", "body": "x"},
                              format="json")
        self.assertEqual(
            (sent.status_code, Activity.objects.filter(lead_id=lead["id"]).count()), (400, 0),
            sent.content,
        )
