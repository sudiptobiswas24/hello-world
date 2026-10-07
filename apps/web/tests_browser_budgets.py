"""
The controller in the browser: a quarter's budget made, a line added to
it from the page, read against the books; and April closed from the
periods screen, with the note that says who signed it off.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.budgets import Budget, BudgetLine
from apps.accounting.models import Account, AccountingPeriod, AccountType, JournalEntry, JournalLine

from .tests_browser import BrowserTestCase


class BudgetsInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.power = Account.objects.create(code="5300", name="Power", account_type=AccountType.EXPENSE)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 4, 10), memo="April's power")
        JournalLine.objects.create(entry=entry, account=self.power, debit=Decimal("1200"))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal("1200"))
        entry.post()

    def test_a_budget_is_made_lined_and_read_against_the_books(self):
        page = self.sign_in(self.person("Controller"), "/app/accounts/budgets/new")
        page.get_by_label("Code").fill("Q1FY27")
        page.get_by_label("Name").fill("April to June 2026")
        page.get_by_label("From").fill("2026-04-01")
        page.get_by_label("To", exact=True).fill("2026-06-30")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/budgets/\d+$"))
        budget = Budget.objects.get()

        page.get_by_role("button", name="Add a line").click()
        form = page.get_by_role("form", name="Add a line")
        form.get_by_role("combobox", name="Account").fill("5300")
        page.get_by_role("option", name=re.compile("5300")).click()
        form.get_by_label("Amount").fill("9100")
        form.get_by_role("button", name="Add a line").click()
        lines = page.locator("section.related", has_text="Lines")
        expect(lines.locator("tbody")).to_contain_text("Power")
        expect(lines.locator("tbody")).to_contain_text("Whole account")
        self.assertEqual([(line.account, line.cost_centre, line.amount) for line in BudgetLine.objects.all()],
                         [(self.power, None, Decimal("9100.00"))])

        page.get_by_role("link", name="Against the books", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/budget-report\?budget=\d+"))
        page.get_by_label("As of").fill("2026-04-30")
        # Thirty of ninety-one days: 9,100 × 30 / 91 = 3,000.00 to date; 1,200 posted.
        row = page.locator("tbody tr", has_text="Power")
        expect(row).to_contain_text("9,100.00")
        expect(row).to_contain_text("3,000.00")
        expect(row).to_contain_text("1,200.00")
        expect(row).to_contain_text("13.2%")
        self.assertEqual(budget.lines.count(), 1)
        self.assertEqual(self.problems, [])

    def test_april_is_closed_from_the_periods_screen(self):
        page = self.sign_in(self.person("Controller"), "/app/accounts/periods/new")
        page.get_by_label("Name").fill("Apr 2026")
        page.get_by_label("From").fill("2026-04-01")
        page.get_by_label("To", exact=True).fill("2026-04-30")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/periods/\d+$"))
        page.get_by_role("button", name="Close the period").click()
        form = page.get_by_role("form", name="Close the period")
        form.get_by_label("Note").fill("Signed off by the auditors")
        form.get_by_role("button", name="Close the period").click()
        expect(page.locator("main")).to_contain_text("Closed")
        period = AccountingPeriod.objects.get()
        self.assertEqual((period.closed, period.note, period.closed_by.username.startswith("controller")),
                         (True, "Signed off by the auditors", True))
        self.assertEqual(self.problems, [])
