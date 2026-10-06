"""
A month reconciled in the browser, as the two people who do it: the
bookkeeper keys the statement in and matches the receipt; the controller
posts the bank's charge and signs the month off. Each step is read back
from the database.

  books: receipt 1,000 (10 Mar). bank: +1,000 (10 Mar), -50 charges (31 Mar).
  closing 950 = 1,000 - 50; nothing left unexplained once the charge is posted.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType, BankStatement, BankStatementLine

from .tests_browser import BrowserTestCase


class ReconciliationInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.charges = Account.objects.create(code="5800", name="Bank charges", account_type=AccountType.EXPENSE)
        self.received = self.receipt("1000")

    def panel(self, page, title):
        return page.locator("section.related").filter(has=page.get_by_role("heading", name=title, exact=True))

    def open_form(self, page, label):
        page.get_by_role("button", name=label).click()
        return page.get_by_role("form", name=label)

    def test_keyed_matched_posted_and_closed(self):
        page = self.sign_in(self.person("Bookkeeper"), "/app/accounts/bank-statements/new")
        page.get_by_role("combobox", name="Bank account").fill("1010")
        page.get_by_role("option", name=re.compile("1010")).click()
        page.get_by_label("From").fill("2026-03-01")
        page.get_by_label("To", exact=True).fill("2026-03-31")
        page.get_by_label("Opening balance").fill("0")
        page.get_by_label("Closing balance").fill("950")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/bank-statements/\d+$"))
        statement = BankStatement.objects.get()

        for day, words, amount in (("2026-03-10", "NEFT Acme", "1000"), ("2026-03-31", "Service charge", "-50")):
            form = self.open_form(page, "Add a line")
            form.get_by_label("Date").fill(day)
            form.get_by_label("Description").fill(words)
            form.get_by_label("Amount").fill(amount)
            form.get_by_role("button", name="Add a line").click()
            expect(self.panel(page, "Lines").locator("tbody")).to_contain_text(words)

        page.get_by_role("button", name="Match the obvious").click()
        expect(self.panel(page, "Lines")).to_contain_text(f"Payment {self.received.number}")
        self.assertEqual(BankStatementLine.objects.get(amount=Decimal("1000")).payment, self.received)
        expect(page.get_by_role("button", name="Post a line")).to_have_count(0)
        expect(page.get_by_role("button", name="Close", exact=True)).to_have_count(0)

        controller = self.sign_in(self.person("Controller"), f"/app/accounts/bank-statements/{statement.pk}",
                                  page=self.new_page())
        expect(self.panel(controller, "Books to bank")).to_contain_text("50.00")
        form = self.open_form(controller, "Post a line")
        form.get_by_label("Line").select_option(label="2026-03-31 · Service charge · -50.00")
        form.get_by_role("combobox", name="To account").fill("5800")
        controller.get_by_role("option", name=re.compile("5800")).click()
        form.get_by_role("button", name="Post a line").click()
        expect(self.panel(controller, "Lines")).to_contain_text("Posted, JE-")
        self.assertEqual(BankStatementLine.objects.get(amount=Decimal("-50")).journal_entry.lines.get(
            account=self.charges).debit, Decimal("50.00"))

        controller.get_by_role("button", name="Close", exact=True).click()
        expect(controller.locator("main")).to_contain_text("Closed")
        statement.refresh_from_db()
        self.assertTrue(statement.closed)
        self.assertEqual(self.problems, [])
