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

    def test_an_invoice_raised_after_the_books_opened_is_refused_on_the_screen_and_a_bad_day_beside_its_box(self):
        """O541 and O543, as the controller in the browser."""
        import datetime

        from apps.accounting.models import Account, AccountType, BooksOpening
        from apps.core.models import Company
        from apps.sales.models import Invoice

        opening = Account.objects.create(code="3900", name="Opening balances", account_type=AccountType.EQUITY)
        BooksOpening.objects.create(opened_on=datetime.date(2026, 10, 1), opening_account=opening)
        company = Company.get()
        company.default_receivable_account = self.ar
        company.save()
        page = self.sign_in(self.person("Controller"), "/app/settings/import")
        page.get_by_label("Kind").select_option("open_invoices")
        page.get_by_label("Rows").fill(f"customer,reference,date,amount\n{self.customer.code},NEW/0001,2026-10-20,590\n")
        page.get_by_role("button", name="Check").click()
        expect(page.locator("main")).to_contain_text(
            "Row 2, date: 2026-10-20 is after the day the books opened (2026-10-01): an invoice raised since is a "
            "sale in the new books, not part of the opening position.")
        page.get_by_label("Rows").fill(f"customer,reference,date,amount\n{self.customer.code},OLD/0500,2026-10-01,2360\n")
        with self.answering(page, "") as asked:
            page.get_by_role("button", name="Keep").click()
            expect(page.locator("main")).to_contain_text("1 row(s), 1 would be made and were kept.")
        self.assertIn("Keep this open_invoices file?", asked[0])
        self.assertEqual(str(Invoice.objects.get(reference="OLD/0500").invoice_date), "2026-10-01")
        self.assertEqual(self.problems, [])

        # A day that is none (a date box takes only a date, so the box is made a plain one to type it) is
        # refused beside the box, not above the file.
        page.get_by_label("Kind").select_option("opening_stock")
        page.get_by_label("As at").evaluate("(box) => { box.type = 'text'; }")
        page.get_by_label("As at").fill("10 Oct")
        page.get_by_label("Rows").fill("sku,warehouse,quantity,unit_cost\nWIDGET-1,WH1,1,1\n")
        page.get_by_role("button", name="Check").click()
        beside = page.locator(".field", has=page.get_by_label("As at")).locator(".field-error")
        expect(beside).to_have_text("'10 Oct' is not a date. Write it as 2026-03-31.")
        self.assertEqual([problem for problem in self.problems if "400" not in problem], [])
