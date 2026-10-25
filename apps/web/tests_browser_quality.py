"""
An inspection in the browser, by the person who takes it: the roll
R-001 against a plan of 83.125 to 91.875 gsm on the mean of three.
Readings of 87, 88 and 89 average 88: the roll passes and is released.
"""

import re

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import PartyRole, PartyRoleAssignment
from apps.quality.models import Inspection
from apps.quality.release import release_status
from apps.quality.tests import QualityTestCase

from .tests_browser import BrowserMixin


class QualityInTheBrowserTests(BrowserMixin, QualityTestCase, StaticLiveServerTestCase):
    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def test_the_inspector_reads_a_roll_and_releases_it(self):
        self.plan()
        PartyRoleAssignment.objects.create(party=self.inspector, role=PartyRole.EMPLOYEE)
        page = self.sign_in(self.person("Quality Inspector"), "/app/quality/inspections/new")
        page.get_by_role("combobox", name="Batch").fill("R-001")
        page.get_by_role("option", name=re.compile("R-001")).click()
        page.get_by_label("Plan").select_option(index=1)
        page.get_by_label("Inspected on").fill("2026-06-01")
        page.get_by_role("combobox", name="By").fill("Meera")
        page.get_by_role("option", name=re.compile("Meera")).click()
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/quality/inspections/\d+$"))
        inspection = Inspection.objects.get()

        for index, value in enumerate(("87", "88", "89"), start=1):
            page.get_by_role("button", name="Add a reading").click()
            form = page.get_by_role("form", name="Add a reading")
            form.get_by_label("What").select_option(label="GSM · Grammes per square metre")
            form.get_by_label("Reading").fill(value)
            form.get_by_label("Sample").fill(f"S{index}")
            form.get_by_role("button", name="Add a reading").click()
            expect(page.locator("section.related", has_text="Readings").locator("tbody tr")).to_have_count(index)
        self.assertEqual(inspection.readings.count(), 3)

        page.get_by_role("button", name="Post", exact=True).click()
        expect(page.locator(".toast", has_text="Posted").first).to_be_visible()
        inspection.refresh_from_db()
        self.assertEqual((inspection.posted, inspection.result), (True, "pass"))
        self.assertEqual(release_status(self.roll), "released")
        expect(page.get_by_role("button", name="Add a reading")).to_have_count(0)
        self.assertEqual(self.problems, [])


class AnAdvisoryFailureInTheBrowserTests(BrowserMixin, QualityTestCase, StaticLiveServerTestCase):
    """
    RISKS.md, decision 22, driven as the people who do it: Meera's readings
    of 92, 92 and 92 average 92 against 83.125 to 91.875 under an advisory
    plan. Posted, R-001 waits held for the Quality Manager; the Inspector
    reads why and who was told, and the server refuses her a decision. The
    Quality Manager finds it on her list, overdue (posted Monday 1 June,
    due Tuesday 2 June), and rejects it from its page.
    """

    def test_the_inspector_is_warned_and_the_manager_decides(self):
        import datetime

        from django.utils import timezone

        from apps.core.chatter import FollowUp
        from apps.quality.models import Disposition

        plan = self.plan(mandatory=False)
        inspection = self.inspect(plan, [92, 92, 92])
        inspector = self.person("Quality Inspector")
        manager = self.login_for("Quality Manager")
        manager.username = "asha_qm"
        manager.save()
        self.login_for("Production Supervisor")

        page = self.sign_in(inspector, f"/app/quality/inspections/{inspection.pk}")
        page.get_by_role("button", name="Post", exact=True).click()
        held = page.get_by_role("note", name="Held for the Quality Manager")
        expect(held).to_contain_text("The batch is HELD")
        expect(held).to_contain_text("Told to decide, by a to-do due")
        expect(held).to_contain_text("asha_qm")
        expect(held).to_contain_text("Told for information: production_supervisor")
        expect(page.get_by_role("button", name="Reject")).to_have_count(0)
        inspection.refresh_from_db()
        self.assertEqual((inspection.result, inspection.disposition), ("fail", Disposition.AWAITING))
        self.assertEqual(release_status(self.roll), "held")
        before = len(self.problems)
        status = page.evaluate("""async (id) => {
            const token = document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/)[1];
            const reply = await fetch(`/api/quality/inspections/${id}/decide/`, {method: "POST",
                headers: {"Content-Type": "application/json", "X-CSRFToken": token},
                body: JSON.stringify({disposition: "reject", note: "I say so"})});
            return reply.status;
        }""", inspection.pk)
        self.assertEqual(status, 403)
        # That refusal was the point (its response, and the console's line for it); any other is a fault.
        page.wait_for_timeout(200)
        self.assertEqual(sorted(self.problems[before:]), sorted([
            "Failed to load resource: the server responded with a status of 403 (Forbidden)",
            f"403 {self.live_server_url}/api/quality/inspections/{inspection.pk}/decide/"]))
        del self.problems[before:]

        from apps.quality.models import Inspection as Kept

        Kept.objects.filter(pk=inspection.pk).update(
            posted_at=timezone.make_aware(datetime.datetime(2026, 6, 1, 10)))
        page = self.sign_in(manager, "/app/quality/awaiting-decision", page=self.new_page())
        row = page.locator("tbody tr", has_text=inspection.number)
        expect(row).to_contain_text("Overdue")
        expect(row).to_contain_text("02 Jun 2026")
        row.get_by_role("link", name=inspection.number).click()
        page.wait_for_url(re.compile(rf"/quality/inspections/{inspection.pk}$"))
        for label in ("Use as is (concession)", "Rework", "Reject"):
            expect(page.get_by_role("button", name=label, exact=True)).to_be_visible()
        page.get_by_role("button", name="Reject", exact=True).click()
        form = page.get_by_role("form", name="Reject")
        form.get_by_label("Why").fill("Too heavy for the customer's filling line.")
        form.get_by_role("button", name="Reject").click()
        expect(page.locator(".toast", has_text="Rejected").first).to_be_visible()
        inspection.refresh_from_db()
        self.assertEqual((inspection.disposition, inspection.decision_login), (Disposition.REJECT, manager))
        self.assertEqual(release_status(self.roll), "held")
        self.assertEqual(FollowUp.objects.filter(object_id=inspection.pk, done_on__isnull=True).count(), 0)
        expect(page.get_by_role("button", name="Reject", exact=True)).to_have_count(0)
        self.assertEqual(self.problems, [])


class OnlyTheQualityManagerLiftsAHoldInTheBrowserTests(BrowserMixin, QualityTestCase, StaticLiveServerTestCase):
    """
    Review F2: R-001 rejected under its mandatory plan (readings of 92, 92, 92 against 83.125 to
    91.875). The Inspector is offered no Void on it, and the server refuses her one; the Quality
    Manager is offered it. A pass the Inspector may still void: that leaves the roll uninspected.
    """

    def test_void_of_a_rejection_is_offered_to_the_quality_manager_only(self):
        from apps.quality.models import Disposition

        plan = self.plan(mandatory=True)
        rejection = self.inspect(plan, [92, 92, 92])
        rejection.post()
        self.assertEqual(rejection.disposition, Disposition.REJECT)
        inspector = self.person("Quality Inspector")
        manager = self.login_for("Quality Manager")
        page = self.sign_in(inspector, f"/app/quality/inspections/{rejection.pk}")
        expect(page.get_by_text(rejection.number).first).to_be_visible()
        expect(page.get_by_role("button", name="Void")).to_have_count(0)
        before = len(self.problems)
        status = page.evaluate("""async (id) => {
            const token = document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/)[1];
            const reply = await fetch(`/api/quality/inspections/${id}/void/`, {method: "POST",
                headers: {"Content-Type": "application/json", "X-CSRFToken": token},
                body: JSON.stringify({reason: "typo"})});
            return reply.status;
        }""", rejection.pk)
        self.assertEqual(status, 400)
        page.wait_for_timeout(200)
        del self.problems[before:]
        page = self.sign_in(manager, f"/app/quality/inspections/{rejection.pk}", page=self.new_page())
        expect(page.get_by_role("button", name="Void")).to_have_count(1)
        self.assertEqual(self.problems, [])


class TheDecisionOfferedIsTheServersTests(BrowserMixin, QualityTestCase, StaticLiveServerTestCase):
    """
    O241: the inspection screen offered a Quality Inspector a concession, and a
    re-inspection of a rejected batch posted as a release, both of which the server
    refuses to anyone without quality.decide_inspection. R-001 rejected under its
    mandatory plan (92, 92, 92); a second draft reads 87, 87, 87.
    """

    def decisions(self, person, inspection):
        page = self.sign_in(person, f"/app/quality/inspections/{inspection.pk}", page=self.new_page())
        box = page.get_by_label("Decision", exact=True)
        expect(box).to_be_visible()
        offered = [text.strip() for text in box.locator("option").all_inner_texts()]
        # Closed before the test deletes what it showed: a page left open went on asking for the deleted
        # inspection's attachments, a 404 that only the machine's speed decided (the g23b gate).
        page.context.close()
        return offered

    def test_each_is_offered_what_the_server_allows(self):
        plan = self.plan(mandatory=True)
        first = self.inspect(plan, [87, 87, 87])
        inspector = self.person("Quality Inspector")
        manager = self.login_for("Quality Manager")
        self.assertEqual(self.decisions(inspector, first),
                         ["Not decided", "Accepted", "Held for rework", "Rejected"])
        Inspection.objects.filter(pk=first.pk).delete()
        rejection = self.inspect(plan, [92, 92, 92])
        rejection.post()
        again = self.inspect(plan, [87, 87, 87])
        self.assertEqual(self.decisions(inspector, again), ["Held for rework", "Rejected"])
        self.assertEqual(self.decisions(manager, again),
                         ["Not decided", "Accepted", "Accepted by concession", "Held for rework", "Rejected"])
        self.assertEqual(self.problems, [])
