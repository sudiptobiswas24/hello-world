"""
The plant's own screens, by the people who use them: the floor hands a
breakdown to maintenance, a fitter books time on it and closes it, and a
service that fell due is put on the board. Each step is read back from
the database.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.manufacturing.maintenance import MaintenanceJob
from apps.manufacturing.tests_breakdowns import BreakdownTestCase
from apps.manufacturing.tests_orders import TODAY

from .tests_browser import BrowserMixin


class PlantInTheBrowserTests(BrowserMixin, BreakdownTestCase, StaticLiveServerTestCase):
    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def test_a_breakdown_from_the_floor_to_a_machine_running_again(self):
        stoppage = self.stopped("90")
        floor = self.sign_in(self.person("Production Supervisor"), f"/app/plant/stoppages/{stoppage.pk}")
        floor.get_by_role("button", name="Raise a repair").click()
        form = floor.get_by_role("form", name="Raise a repair")
        form.get_by_label("What failed").fill("Screen blocked")
        form.get_by_role("button", name="Raise a repair").click()
        floor.wait_for_url(re.compile(r"/plant/jobs/\d+$"))
        job = MaintenanceJob.objects.get(downtime=stoppage)
        self.assertEqual((job.is_breakdown, job.fault), (True, "Screen blocked"))

        fitter = self.sign_in(self.person("Maintenance"), f"/app/plant/jobs/{job.pk}", page=self.new_page())
        fitter.get_by_role("button", name="Book a fitter's time").click()
        form = fitter.get_by_role("form", name="Book a fitter's time")
        form.get_by_role("combobox", name="Fitter").fill("Fitter")
        fitter.get_by_role("option", name=re.compile("EMP-0301")).click()
        form.get_by_label("Minutes").fill("45")
        form.get_by_role("button", name="Book a fitter's time").click()
        self.toast(fitter, "Time booked")
        self.assertEqual(job.labour_minutes(), Decimal("45"))
        expect(fitter.locator("section.related", has_text="Fitters' time")).to_contain_text("45")

        fitter.get_by_role("button", name="Complete").click()
        form = fitter.get_by_role("form", name="Complete")
        form.get_by_label("Cause").fill("Melt filter clogged")
        form.get_by_label("What was done").fill("Screen changed")
        form.get_by_role("button", name="Complete").click()
        self.toast(fitter, "Completed")
        job.refresh_from_db()
        self.assertIsNotNone(job.done_on)
        self.assertEqual((job.cause, job.action_taken), ("Melt filter clogged", "Screen changed"))
        expect(fitter.get_by_role("button", name="Complete")).to_have_count(0)
        self.assertEqual(self.problems, [])

    def test_a_service_that_fell_due_is_put_on_the_board(self):
        schedule = self.schedule(days=30)  # never done: due now
        fitter = self.sign_in(self.person("Maintenance"), "/app/plant/due")
        row = fitter.locator("tbody tr", has_text="Gearbox service")
        row.get_by_role("button", name="Raise the job").click()
        self.toast(fitter, "Job raised")
        self.assertTrue(MaintenanceJob.objects.filter(schedule=schedule).exists())
        expect(fitter.locator("tbody tr", has_text="Gearbox service")).to_have_count(0)
        self.assertEqual(self.problems, [])

    def test_where_the_hours_went(self):
        self.stopped("90")
        self.stopped("60", reason=self.changeover)
        day = TODAY.isoformat()  # the fixture's day, outside the screen's last thirty
        page = self.sign_in(self.person("Production Supervisor"), f"/app/plant/hours-lost?start={day}&end={day}")
        rows = page.locator("tbody tr")
        expect(rows.first).to_contain_text("Breakdown")
        expect(rows.first).to_contain_text("60.0%")
        expect(page.locator("tfoot")).to_contain_text("2.5")
        self.assertEqual(self.problems, [])
