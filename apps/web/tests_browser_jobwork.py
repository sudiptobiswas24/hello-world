"""
Fabric out to a job worker in the browser, by the stores manager: the
outside fixture's run has 1,000 kg to be coated; this challan sends 600
kg of it, and 4 kg is lost at the laminator. Each step is read back from
the database.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import PartyRole, PartyRoleAssignment
from apps.manufacturing.models import JobWorkChallan, JobWorkLoss
from apps.manufacturing.tests_jobwork import JobWorkTestCase

from .tests_browser import BrowserMixin


class JobWorkInTheBrowserTests(BrowserMixin, JobWorkTestCase, StaticLiveServerTestCase):
    def test_the_stores_manager_sends_fabric_out_and_records_a_loss(self):
        # A job worker bills for the work, so it is kept as a vendor.
        PartyRoleAssignment.objects.create(party=self.laminator, role=PartyRole.VENDOR)
        page = self.sign_in(self.person("Stores Manager"), "/app/stores/job-work/new")
        page.get_by_role("combobox", name="Job worker").fill("LAM")
        page.get_by_role("option", name=re.compile("Laminator")).click()
        page.get_by_label("Date").fill("2026-06-01")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/stores/job-work/\d+$"))
        challan = JobWorkChallan.objects.get(job_worker=self.laminator)

        page.get_by_role("button", name="Add goods").click()
        form = page.get_by_role("form", name="Add goods")
        form.get_by_role("combobox", name="For step").fill("Coat")
        page.get_by_role("option", name=re.compile("Coat")).click()
        form.get_by_label("Goods", exact=True).fill("Woven fabric for coating")
        form.get_by_label("HSN").fill("63053300")
        form.get_by_label("Quantity").fill("600")
        form.get_by_label("Value").fill("57000")
        form.get_by_label("GST rate %").fill("5")
        form.get_by_role("button", name="Add goods").click()
        expect(page.locator("section.related", has_text="Goods sent")).to_contain_text("Woven fabric for coating")
        line = challan.lines.get()
        self.assertEqual((line.operation, line.quantity, line.value), (self.coat, Decimal("600"), Decimal("57000")))

        page.get_by_role("button", name="Post").click()
        expect(page.locator(".pill", has_text="Posted")).to_be_visible()
        challan.refresh_from_db()
        self.assertTrue(challan.posted)

        page.get_by_role("button", name="Record a loss").click()
        form = page.get_by_role("form", name="Record a loss")
        form.get_by_label("Goods", exact=True).select_option(label="Woven fabric for coating")
        form.get_by_label("Date").fill("2026-06-05")
        form.get_by_label("Quantity").fill("4")
        form.get_by_role("button", name="Record a loss").click()
        expect(page.locator("section.related", has_text="Lost at the job worker")).to_contain_text("4")
        loss = JobWorkLoss.objects.get()
        self.assertEqual((loss.line, loss.quantity), (line, Decimal("4")))
        self.assertEqual(self.problems, [])
