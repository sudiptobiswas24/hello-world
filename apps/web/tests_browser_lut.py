"""
The Letter of Undertaking and what setup seeded, kept from the office as the controller does it (O239).
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.gst import GstSettings, LetterOfUndertaking
from apps.accounting.models import FiscalPosition
from apps.accounting.tcs import SeededDefault

from .tests_browser import BrowserTestCase


class LutAndSeededDefaultsInTheBrowserTests(BrowserTestCase):
    def test_the_lut_is_kept_and_what_was_seeded_is_reviewed(self):
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)
        controller = self.person("Controller")
        page = self.sign_in(controller, "/app/settings/luts/new")
        page.get_by_label("ARN").fill("AD190426000123X")
        page.get_by_label("Financial year").fill("2026-27")
        page.get_by_label("Filed on").fill("2026-03-25")
        page.get_by_label("Valid from").fill("2026-04-01")
        page.get_by_label("Valid to").fill("2027-03-31")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/luts/\d+$"))
        lut = LetterOfUndertaking.objects.get()
        self.assertEqual((lut.arn, lut.valid_to), ("AD190426000123X", datetime.date(2027, 3, 31)))

        seeded = SeededDefault.objects.create(model="accounting.account", object_id=self.bank.pk, label="1010 Bank")
        page.goto(f"{self.live_server_url}/app/settings/seeded-defaults")
        expect(page.locator("main")).to_contain_text("To review")
        page.goto(f"{self.live_server_url}/app/settings/seeded-defaults/{seeded.pk}")
        page.get_by_role("button", name="Mark reviewed").click()
        expect(page.locator("main")).to_contain_text("Reviewed")
        seeded.refresh_from_db()
        self.assertEqual((seeded.reviewed_by, seeded.reviewed_on is not None), (controller, True))
        self.assertEqual(self.problems, [])
