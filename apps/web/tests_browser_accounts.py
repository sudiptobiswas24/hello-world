"""
The books, in a browser, by the people who keep them: the bookkeeper
keys an entry, the controller posts it and reads it back through the
trial balance into the account's ledger. Payroll is worked out by one
person and posted by another. Each step is read back from the database.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import JournalEntry
from apps.hr.tests_payroll_api import PayrollApiTestCase

from .tests_browser import BrowserMixin, BrowserTestCase


class BooksInTheBrowserTests(BrowserTestCase):
    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def add_line(self, page, account, side, amount):
        page.get_by_role("combobox", name="Account").fill(account)
        page.get_by_role("option", name=re.compile(f"^{account}")).click()
        page.get_by_label("Debit or credit").select_option(side)
        page.get_by_label("Amount", exact=True).fill(amount)
        page.get_by_role("button", name="Add line").click()

    def test_an_entry_keyed_posted_and_read_back_through_the_ledger(self):
        keeper = self.sign_in(self.person("Bookkeeper"), "/app/accounts/journals/new")
        keeper.get_by_label("Reference").fill("ADJ-1")
        keeper.get_by_label("What it is for").fill("Cash sale banked")
        keeper.get_by_role("button", name="Start entry").click()
        keeper.wait_for_url(re.compile(r"/accounts/journals/\d+$"))
        self.add_line(keeper, "1010", "debit", "500")
        expect(keeper.locator(".lines tbody tr", has_text="1010")).to_have_count(1)
        self.add_line(keeper, "4000", "credit", "500")
        expect(keeper.locator(".lines tbody tr", has_text="4000")).to_have_count(1)
        expect(keeper.get_by_role("button", name="Post", exact=True)).to_have_count(0)  # not the keeper's to post
        entry = JournalEntry.objects.get(reference="ADJ-1")
        self.assertFalse(entry.posted)

        controller = self.new_page()
        self.sign_in(self.person("Controller"), f"/app/accounts/journals/{entry.pk}", page=controller)
        controller.get_by_role("button", name="Post", exact=True).click()
        expect(controller.locator(".pill", has_text="Posted")).to_be_visible()
        entry.refresh_from_db()
        self.assertTrue(entry.posted)
        self.assertEqual(self.balance(self.bank), Decimal("500.00"))

        controller.goto(self.url("/accounts/trial-balance"))
        expect(controller.locator(".pill", has_text="Balances")).to_be_visible()
        controller.locator("tbody tr", has_text="1010").get_by_role("link", name="1010").click()
        controller.wait_for_url(re.compile(r"/accounts/chart/\d+"))
        expect(controller.locator(".tile", has_text="Closed at")).to_contain_text("500.00")
        expect(controller.locator("tbody tr", has_text="Cash sale banked")).to_contain_text("500.00")
        self.assertEqual(self.problems, [])

    def test_an_invoices_entry_is_corrected_on_the_invoice_and_one_made_by_hand_here(self):
        import datetime

        invoice = self.invoice(self.customer, datetime.date(2026, 3, 1), "500")
        made = JournalEntry.objects.create(date="2026-03-02", reference="ADJ-3", memo="Accrued rent")
        made.lines.create(account=self.revenue, debit=Decimal("10"))
        made.lines.create(account=self.bank, credit=Decimal("10"))
        made.post()
        page = self.sign_in(self.person("Controller"), f"/app/accounts/journals/{invoice.journal_entry_id}")
        expect(page.locator("main")).to_contain_text(f"Invoice {invoice}")
        expect(page.get_by_role("button", name="Reverse")).to_have_count(0)
        page.goto(self.url(f"/accounts/journals/{made.pk}"))
        expect(page.get_by_role("button", name="Reverse")).to_be_visible()
        self.assertEqual(self.problems, [])

    def test_an_entry_out_of_balance_cannot_be_posted(self):
        entry = JournalEntry.objects.create(date="2026-03-01", reference="ADJ-2")
        entry.lines.create(account=self.bank, debit=Decimal("100"))
        entry.lines.create(account=self.revenue, credit=Decimal("90"))
        page = self.sign_in(self.person("Controller"), f"/app/accounts/journals/{entry.pk}")
        expect(page.get_by_role("button", name="Post", exact=True)).to_be_disabled()
        expect(page.locator(".totals")).to_contain_text("10.00")

    def test_the_books_are_not_a_reps_to_read(self):
        page = self.sign_in(self.person("Sales Rep"), "/app/")
        expect(page.get_by_role("link", name="Accounts")).to_have_count(0)
        page.goto(self.url(f"/accounts/chart/{self.bank.pk}"))
        expect(page.get_by_role("heading", name="Not allowed")).to_be_visible()
        self.problems.clear()  # the refusal is the point here

    def register_for_gst(self):
        from apps.accounting.gst import GstSettings
        from apps.accounting.models import FiscalPosition

        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)

    def test_returns_without_a_registration_say_so(self):
        page = self.sign_in(self.person("GST Officer"), "/app/accounts/gst")
        expect(page.get_by_text("GST is not set up").first).to_be_visible()
        self.problems.clear()  # the two refusals are the point here

    def test_every_role_opens_what_it_reads_without_being_refused(self):
        from django.contrib.auth.models import Permission

        self.register_for_gst()

        screens = [
            ("accounting.view_journalentry", "/accounts/chart", "Chart of accounts"),
            ("accounting.view_journalentry", f"/accounts/chart/{self.bank.pk}", "Closed at"),
            ("accounting.view_journalentry", "/accounts/journals", "Journal entries"),
            ("accounting.view_journalentry", "/accounts/trial-balance", "Trial balance"),
            ("accounting.view_journalentry", "/accounts/profit-and-loss", "Profit and loss"),
            ("accounting.view_journalentry", "/accounts/balance-sheet", "Balance sheet"),
            ("gst.compile_returns", "/accounts/gst", "GST returns"),
        ]
        for role in ["Bookkeeper", "Controller", "GST Officer", "AR Manager"]:
            with self.subTest(role=role):
                person = self.person(role)
                held = {f"{app}.{code}" for app, code in Permission.objects.filter(
                    group__user=person).values_list("content_type__app_label", "codename")}
                self.problems.clear()  # each role answers for itself
                page = self.new_page()
                self.sign_in(person, "/app/", page=page)
                for permission, path, heading in screens:
                    if permission not in held:
                        continue
                    page.goto(self.url(path))
                    expect(page.locator("main").first).to_contain_text(heading)
                    page.wait_for_load_state("networkidle")
                self.assertEqual(self.problems, [], role)


class PayrollInTheBrowserTests(BrowserMixin, PayrollApiTestCase, StaticLiveServerTestCase):
    def test_worked_out_by_one_posted_by_another(self):
        run = self.pay_run()
        officer = self.sign_in(self.login_for("Payroll Officer"), f"/app/payroll/runs/{run.pk}")
        officer.get_by_role("button", name="Work it out").click()
        expect(officer.locator("tbody tr", has_text="P1")).to_contain_text("3,750.00")
        expect(officer.get_by_role("button", name="Post", exact=True)).to_have_count(0)
        run.refresh_from_db()
        self.assertEqual(run.status, "calculated")

        controller = self.new_page()
        self.sign_in(self.login_for("Controller"), f"/app/payroll/runs/{run.pk}", page=controller)
        controller.get_by_role("button", name="Post", exact=True).click()
        expect(controller.locator(".pill", has_text="posted")).to_be_visible()
        run.refresh_from_db()
        self.assertEqual(run.status, "posted")
        self.assertEqual(self.problems, [])
