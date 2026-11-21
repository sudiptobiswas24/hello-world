"""
A month reconciled in the browser, as the two people who do it: the
bookkeeper keys the statement in and matches the receipt; the controller
posts the bank's charge and signs the month off. Each step is read back
from the database.

  books: receipt 1,000 (10 Mar). bank: +1,000 (10 Mar), -50 charges (31 Mar).
  closing 950 = 1,000 - 50; nothing left unexplained once the charge is posted.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType, BankCutOffItem, BankStatement, BankStatementLine

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

    def test_a_cheque_the_bank_returned_is_voided_on_its_day_and_matched(self):
        """
        books: receipt 1,000 (10 Mar). bank: +1,000 (10 Mar), -1,000 returned unpaid (14 Mar); closing 0.
        The controller voids the receipt on the 14th, the day the bank returned it; the bookkeeper
        matches the return, then the cheque, and the month closes with nothing posted.
        """
        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 3, 1), end_date=datetime.date(2026, 3, 31),
            opening_balance=Decimal("0"), closing_balance=Decimal("0"))
        for day, words, amount in ((10, "Cheque Acme", "1000"), (14, "Cheque returned unpaid", "-1000")):
            BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 3, day), description=words,
                                             amount=Decimal(amount))
        controller = self.sign_in(self.person("Controller"), f"/app/sales/receipts/{self.received.pk}")
        controller.get_by_role("button", name="Void").click()
        dialog = controller.get_by_role("dialog", name="Void the payment")
        dialog.get_by_label("Why it is void").fill("Returned unpaid")
        dialog.get_by_label("Void on").fill("2026-03-14")
        dialog.get_by_role("button", name="Void").click()
        expect(controller.locator(".toast", has_text="Voided").first).to_be_visible()
        self.received.refresh_from_db()
        self.assertEqual(self.received.voided_entry.date, datetime.date(2026, 3, 14))

        page = self.sign_in(self.person("Bookkeeper"), f"/app/accounts/bank-statements/{statement.pk}", page=self.new_page())
        for line, shown in (("2026-03-14 · Cheque returned unpaid · -1000.00", f"Returned: payment {self.received.number}"),
                            ("2026-03-10 · Cheque Acme · 1000.00", f"Payment {self.received.number}")):
            form = self.open_form(page, "Match a line")
            form.get_by_label("Line").select_option(label=line)
            form.get_by_role("combobox", name="Payment").fill(self.received.number)
            page.get_by_role("option", name=re.compile(self.received.number)).click()
            form.get_by_role("button", name="Match a line").click()
            expect(self.panel(page, "Lines")).to_contain_text(shown)
            # The panel reads the match before the form closes: the next line waits for it.
            expect(form).to_have_count(0)

        controller.goto(f"{self.live_server_url}/app/accounts/bank-statements/{statement.pk}")
        controller.get_by_role("button", name="Close", exact=True).click()
        expect(controller.locator("main")).to_contain_text("Closed")
        statement.refresh_from_db()
        self.assertTrue(statement.closed)
        self.assertEqual(self.problems, [])


class ACutOffInTheBrowserTests(BrowserTestCase):
    """
    O615 in the browser, as the people who do it. books: receipt 1,000 (10 Mar), cleared before the cut-off of
    1 April. Cheque 000101 for 3,000 from the old books, unpaid at the cut-off, so the bank held 4,000 then; it
    paid the cheque on 5 April and closes April at 1,000. The bookkeeper sets the day on the bank's account,
    lists the cheque and matches it; the controller closes the month.
    """

    def panel(self, page, title):
        return page.locator("section.related").filter(has=page.get_by_role("heading", name=title, exact=True))

    def test_the_day_set_the_cheque_listed_matched_and_the_month_closed(self):
        self.receipt("1000")
        page = self.sign_in(self.person("Bookkeeper"), f"/app/accounts/chart/{self.bank.pk}/edit")
        page.get_by_label("Reconciled from").fill("2026-04-01")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_role("button", name="Save", exact=True)).to_have_count(0)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.reconciled_from, datetime.date(2026, 4, 1))

        page.goto(f"{self.live_server_url}/app/accounts/bank-cut-off/new")
        page.get_by_role("combobox", name="Bank account").fill("1010")
        page.get_by_role("option", name=re.compile("1010")).click()
        page.get_by_label("Written").fill("2026-02-20")
        page.get_by_label("Reference").fill("000101")
        page.get_by_label("Amount").fill("-3000")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/bank-cut-off/\d+$"))
        item = BankCutOffItem.objects.get()
        self.assertEqual((item.date, item.amount), (datetime.date(2026, 2, 20), Decimal("-3000")))

        statement = BankStatement.objects.create(
            bank_account=self.bank, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2026, 4, 30),
            opening_balance=Decimal("4000"), closing_balance=Decimal("1000"))
        BankStatementLine.objects.create(statement=statement, date=datetime.date(2026, 4, 5), description="Chq 000101",
                                         amount=Decimal("-3000"))
        page.goto(f"{self.live_server_url}/app/accounts/bank-statements/{statement.pk}")
        page.get_by_role("button", name="Match an outstanding cheque").click()
        form = page.get_by_role("form", name="Match an outstanding cheque")
        form.get_by_label("Line").select_option(label="2026-04-05 · Chq 000101 · -3000.00")
        form.get_by_label("Outstanding at the cut-off").select_option(label="000101 · 2026-02-20 · -3000.00")
        form.get_by_role("button", name="Match an outstanding cheque").click()
        expect(self.panel(page, "Lines")).to_contain_text("Outstanding at the cut-off: 000101")
        self.assertEqual(BankStatementLine.objects.get().cut_off_item, item)
        expect(page.get_by_role("button", name="Close", exact=True)).to_have_count(0)

        controller = self.sign_in(self.person("Controller"), f"/app/accounts/bank-statements/{statement.pk}",
                                  page=self.new_page())
        controller.get_by_role("button", name="Close", exact=True).click()
        expect(controller.locator("main")).to_contain_text("Closed")
        statement.refresh_from_db()
        self.assertTrue(statement.closed)
        controller.goto(f"{self.live_server_url}/app/accounts/bank-cut-off")
        expect(controller.locator("main")).to_contain_text("Shown on 1010 to 30 Apr 2026")
        self.assertEqual(self.problems, [])
