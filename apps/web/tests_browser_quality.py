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
