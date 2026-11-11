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

    def test_the_opening_kinds_ask_for_no_day_or_account_and_say_where_the_books_are_opened(self):
        """O529-O531: nothing typed beside the file; before the books open the screen says where to open them."""
        from apps.accounting.models import BooksOpening

        self.assertIsNone(BooksOpening.get())
        page = self.sign_in(self.person("Controller"), "/app/settings/import")
        page.get_by_label("Kind").select_option("opening_stock")
        expect(page.get_by_label("As at")).to_have_value("")
        page.get_by_label("Rows").fill("sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n")
        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text(
            "File, --date: Set the day the books opened (Settings, Books opening) first: opening stock is what "
            "was counted on it.")
        page.get_by_label("Kind").select_option("open_invoices")
        expect(page.get_by_label("Opening-balance account")).to_have_value("")
        page.get_by_label("Rows").fill("customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n")
        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text(
            "Row 2, --date: set the day the books opened (Settings, Books opening) first: what is still owed "
            "stands against the account set there.")
        self.assertEqual(self.problems, [])
