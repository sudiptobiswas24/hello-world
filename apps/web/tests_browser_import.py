"""
The controller in the browser brings a parties file in: chooses the
kind, pastes the rows, checks the file and is told what it would make,
keeps it, and the party is there, stamped as theirs.
"""

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party

from .tests_browser import BrowserTestCase

PARTIES = "code,name,roles,currency\nC-700,Shree Cement,customer,USD\n"


class ImportInTheBrowserTests(BrowserTestCase):
    def test_checked_then_kept_from_the_screen(self):
        controller = self.person("Controller")
        page = self.sign_in(controller, "/app/settings/import")
        page.get_by_label("Kind").select_option("parties")
        expect(page.get_by_role("link", name="Download parties.csv")).to_be_visible()
        page.get_by_label("Rows").fill(PARTIES)
        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text("1 row(s), 1 would be made; not kept yet.")
        self.assertFalse(Party.objects.filter(code="C-700").exists())

        with self.answering(page, "") as asked:
            page.get_by_role("button", name="Keep").click()
            expect(page.locator("main")).to_contain_text("1 row(s), 1 would be made and were kept.")
        self.assertIn("Keep this parties file?", asked[0])
        party = Party.objects.get(code="C-700")
        self.assertEqual((party.name, party.created_by), ("Shree Cement", controller))

        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text("Row 2, code: 'C-700' already exists.")
        self.assertEqual(self.problems, [])
