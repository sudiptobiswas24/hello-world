"""
The GST officer keeps September's 2B from the portal's file and reads
which bills it answers for and which still wait on their suppliers.
"""

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.gst.tests_gstr2b import SEPTEMBER_2B, Gstr2bTestCase

from .tests_browser import BrowserMixin


class Gstr2bInTheBrowserTests(BrowserMixin, Gstr2bTestCase, StaticLiveServerTestCase):
    def test_kept_and_matched(self):
        page = self.sign_in(self.person("GST Officer"), "/app/accounts/gstr2b?period=2026-09")
        expect(page.locator("main")).to_contain_text("No GSTR-2B kept for 2026-09")
        page.get_by_label("The file").set_input_files({"name": "GSTR2B_092026.json", "mimeType": "application/json",
                                                       "buffer": SEPTEMBER_2B.encode()})
        page.get_by_role("button", name="Keep this 2B").click()
        self.toast(page, "2026-09's GSTR-2B kept")
        expect(page.locator("main")).to_contain_text("2026-09's 2B is kept: 5 lines")
        matched = page.locator("section.statement-section", has_text="Matched").first
        expect(matched.locator("tbody")).to_contain_text("INV/27/0012")
        waiting = page.locator("section.statement-section", has_text="Waiting on suppliers")
        expect(waiting.locator("tbody")).to_contain_text("W-1")
        expect(page.locator(".tile", has_text="Waiting on suppliers")).to_contain_text("900.00")
        self.assertEqual(self.problems, [])
