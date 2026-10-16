"""
Material to a run, in the browser, by the supervisor: 600 kg of virgin
polymer at ₹100 a kg to a released run is ₹60,000 into its work in
progress. Read back from the database.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.manufacturing.orders import MaterialIssue
from apps.manufacturing.tests_conversion import ConversionTestCase

from .tests_browser import BrowserMixin


class FloorInTheBrowserTests(BrowserMixin, ConversionTestCase, StaticLiveServerTestCase):
    def test_the_supervisor_issues_material_to_a_run(self):
        run = self.routed()
        page = self.sign_in(self.person("Production Supervisor"), "/app/production/issues/new")
        page.get_by_role("combobox", name="Run").fill(run.number)
        page.get_by_role("option", name=re.compile(run.number)).click()
        page.get_by_label("Date").fill("2026-06-01")
        page.get_by_label("Store").select_option(label="Plant")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/production/issues/\d+$"))
        issue = MaterialIssue.objects.get()

        page.get_by_role("button", name="Add material").click()
        form = page.get_by_role("form", name="Add material")
        form.get_by_role("combobox", name="Item").fill("PP-RAFFIA")
        page.get_by_role("option", name=re.compile("PP-RAFFIA")).click()
        form.get_by_label("Quantity").fill("600")
        form.get_by_label("Unit").select_option(label="kg")
        form.get_by_role("button", name="Add material").click()
        expect(page.locator("section.related", has_text="Material")).to_contain_text("PP homopolymer")

        page.get_by_role("button", name="Post", exact=True).click()
        expect(page.locator(".toast", has_text="Posted").first).to_be_visible()
        issue.refresh_from_db()
        self.assertEqual((issue.posted, issue.posted_value), (True, Decimal("60000")))
        expect(page.get_by_role("button", name="Add material")).to_have_count(0)
        self.assertEqual(self.problems, [])
