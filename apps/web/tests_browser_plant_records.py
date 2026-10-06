"""
Two plant records in the browser, as the people who keep them: the
supervisor writes down what the tape line ran at and the quality manager
reads it on the complaint; the personnel office renews a licence before
it lapses. Each step is read back from the database.
"""

import datetime
import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.licences import Licence, LicenceKind
from apps.manufacturing.tape_settings import TapeRunSetting
from apps.manufacturing.tests_tape_settings import TapeSettingsTestCase

from .tests_browser import BrowserMixin, BrowserTestCase


class TapeSettingsInTheBrowserTests(BrowserMixin, TapeSettingsTestCase, StaticLiveServerTestCase):
    def test_recorded_on_the_run_and_read_on_the_complaint(self):
        page = self.sign_in(self.login_for("Production Supervisor"), f"/app/production/work-orders/{self.run_.pk}")
        page.get_by_role("button", name="Record the settings").click()
        form = page.get_by_role("form", name="Record the settings")
        form.get_by_label("Machine").select_option(label="EXT-1A · Extrusion line 1")
        form.get_by_label("Draw ratio").fill("6.2")
        form.get_by_label("Quench bath °C").fill("28.5")
        form.get_by_role("button", name="Record the settings").click()
        panel = page.locator("section.related", has_text="Tape line settings")
        expect(panel.locator("tbody")).to_contain_text("EXT-1A")
        setting = TapeRunSetting.objects.get()
        self.assertEqual((setting.machine, setting.draw_ratio, setting.quench_temperature_c),
                         (self.line, Decimal("6.20"), Decimal("28.5")))
        self.assertEqual(self.problems, [])

        complaint = self.complaint()
        complaint.add_lot(self.tape_lot)
        page = self.sign_in(self.login_for("Quality Manager"), f"/app/quality/complaints/{complaint.pk}",
                            page=self.new_page())
        settings = page.locator("section.related", has_text="Tape line settings on those runs")
        # A quantity reads at the places it has: 6.20 shows as 6.2.
        expect(settings.get_by_role("cell", name="6.2", exact=True)).to_be_visible()
        expect(page.locator("section.related", has_text="Made from").locator("tbody")).to_contain_text("PP-2609")
        self.assertEqual(self.problems, [])


class LicenceInTheBrowserTests(BrowserTestCase):
    def test_renewed_before_it_lapses(self):
        today = timezone.localdate()
        licence = Licence.objects.create(kind=LicenceKind.FIRE, licence_number="FIRE/NOC/77",
                                         valid_from=today - datetime.timedelta(days=300),
                                         valid_to=today + datetime.timedelta(days=30))
        page = self.sign_in(self.person("HR Admin"), "/app/settings/licences")
        page.get_by_text("FIRE/NOC/77").click()
        page.wait_for_url(re.compile(rf"/settings/licences/{licence.pk}$"))
        expect(page.locator("main")).to_contain_text("due")
        page.get_by_role("button", name="Renew").click()
        form = page.get_by_role("form", name="Renew")
        form.get_by_label("New number").fill("FIRE/NOC/91")
        form.get_by_label("Valid from").fill(str(today + datetime.timedelta(days=31)))
        form.get_by_label("Valid to").fill(str(today + datetime.timedelta(days=396)))
        form.get_by_role("button", name="Renew").click()
        # The renewal opens: a route change in the page, not a load.
        expect(page).to_have_url(re.compile(rf"/settings/licences/(?!{licence.pk}$)\d+$"))
        expect(page.locator("main")).to_contain_text("FIRE/NOC/91")
        renewal = Licence.objects.get(licence_number="FIRE/NOC/91")
        licence.refresh_from_db()
        self.assertEqual(licence.renewed_by, renewal)
        self.assertEqual(renewal.valid_from, today + datetime.timedelta(days=31))
        self.assertEqual(self.problems, [])
