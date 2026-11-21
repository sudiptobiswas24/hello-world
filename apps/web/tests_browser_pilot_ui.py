"""
The pilot's office screens, as the people who use them (g28):
  O251 the accountant sets the day the books opened, on a screen of its own;
  O418 an action's questions are a dialog of labelled fields, found by label;
  O401 a new order is made with its lines in one save, and one left half-way leaves nothing.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType, BooksOpening, JournalEntry, JournalLine
from apps.core.models import Company
from apps.sales.models import SalesOrder, SalesOrderLine

from .tests_browser import BrowserTestCase


class PilotScreensInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_revenue_account = self.revenue
        company.default_receivable_account = self.ar
        company.default_bank_account = self.bank
        company.save()

    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def test_the_accountant_sets_the_day_the_books_opened(self):
        equity = Account.objects.create(code="3900", name="Opening balances", account_type=AccountType.EQUITY)
        page = self.sign_in(self.person("Controller"), "/app/settings/books-opening")
        expect(page.locator("main")).to_contain_text("Not set yet")
        page.goto(self.url("/settings/books-opening/new"))
        page.get_by_label("Books opened on").fill("2026-03-31")
        page.get_by_label("Opening balances against").select_option(label="3900 Opening balances")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/books-opening/\d+$"))
        books = BooksOpening.get()
        self.assertEqual((books.opened_on, books.opening_account), (datetime.date(2026, 3, 31), equity))
        page.goto(self.url("/settings/books-opening"))
        expect(page.locator("main")).to_contain_text("Opening balances")
        self.assertEqual(self.problems, [])

    def test_an_actions_questions_are_found_by_their_labels(self):
        # Void a receipt: why, and on which day, in one dialog.
        invoice = self.bill(self.make_order(quantity="2", price="100"))
        payment = self.receipt("50")
        from django.contrib.auth.models import Group, Permission

        clerk = self.person("AR Manager")
        clerk.user_permissions.add(Permission.objects.get(codename="write_off_invoice"))  # whoever writes off bad debt
        accounts = self.sign_in(clerk, f"/app/sales/receipts/{payment.pk}")
        accounts.get_by_role("button", name="Void", exact=True).click()
        dialog = accounts.get_by_role("dialog", name="Void the payment")
        dialog.get_by_label("Why it is void").fill("Cheque bounced")
        dialog.get_by_label("Void on").fill("2026-03-12")
        dialog.get_by_role("button", name="Void", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(accounts.locator(".pill", has_text="Void")).to_be_visible()
        payment.refresh_from_db()
        self.assertIsNotNone(payment.voided_entry_id)
        self.assertEqual(payment.voided_entry.date, datetime.date(2026, 3, 12))

        # Write off an invoice: how much, and why. Said not now, nothing is written off.
        accounts.goto(self.url(f"/sales/invoices/{invoice.pk}"))
        accounts.get_by_role("button", name="Write off", exact=True).click()
        dialog = accounts.get_by_role("dialog", name=f"Write off {invoice.number}")
        expect(dialog.get_by_label("Amount to write off")).to_be_visible()
        dialog.get_by_label("Why it will not be paid").fill("Customer closed")
        dialog.get_by_role("button", name="Not now").click()
        expect(dialog).to_have_count(0)
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due(), Decimal("200.00"))

        # Reverse a journal entry: why.
        cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 3, 5), memo="Petty cash sale")
        JournalLine.objects.create(entry=entry, account=cash, debit=Decimal("75"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("75"))
        entry.post()
        clerk.groups.add(Group.objects.get(name="Controller"))  # the accountant, who reverses an entry
        books = accounts
        books.goto(self.url(f"/accounts/journals/{entry.pk}"))
        books.get_by_role("button", name="Reverse", exact=True).click()
        dialog = books.get_by_role("dialog", name="Reverse the entry")
        dialog.get_by_label("Why it is reversed").fill("Posted twice")
        dialog.get_by_role("button", name="Reverse", exact=True).click()
        books.wait_for_url(re.compile(rf"/accounts/journals/(?!{entry.pk}$)\d+$"))
        self.assertEqual(JournalEntry.objects.get(reverses=entry).memo.count("Posted twice"), 1)
        self.assertEqual(self.problems, [])

    def test_a_rep_makes_an_order_with_two_lines_in_one_save(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders/new")
        rep.get_by_role("combobox", name="Customer").fill("Acm")
        rep.get_by_role("option", name=re.compile("Acme")).click()
        for quantity, price in (("3", ""), ("2", "50")):
            # The same search twice, typed again inside the close a blur schedules (O463): the list stays open.
            expect(rep.get_by_role("combobox", name="Item to add")).to_have_value("")
            rep.get_by_role("combobox", name="Item to add").fill("WDG")
            rep.get_by_role("option", name=re.compile("WDG-1")).click()
            rep.get_by_label("Quantity to add").fill(quantity)
            rep.get_by_label("Unit price to add").fill(price)
            rep.get_by_role("button", name="Add line").click()
        expect(rep.locator(".lines tbody tr", has_text="Widget")).to_have_count(2)
        self.assertEqual(SalesOrder.objects.count(), 0)  # nothing written until it is created
        rep.get_by_role("button", name="Create", exact=True).click()
        rep.wait_for_url(re.compile(r"/sales/orders/\d+$"))
        order = SalesOrder.objects.get()
        self.assertEqual(sorted((line.quantity, line.unit_price) for line in order.lines.all()),
                         [(Decimal("2"), Decimal("50")), (Decimal("3"), self.item.sale_price)])  # left empty: the list price
        expect(rep.locator(".lines tbody tr", has_text="Widget")).to_have_count(2)
        self.assertEqual(self.problems, [])

    def test_an_order_left_half_way_leaves_nothing(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders/new")
        rep.get_by_role("combobox", name="Customer").fill("Acm")
        rep.get_by_role("option", name=re.compile("Acme")).click()
        rep.get_by_role("combobox", name="Item to add").fill("WDG")
        rep.get_by_role("option", name=re.compile("WDG-1")).click()
        rep.get_by_role("button", name="Add line").click()
        expect(rep.locator(".lines tbody tr", has_text="Widget")).to_have_count(1)
        # Leaving asks first; told yes, it goes, and no draft is left behind.
        with self.answering(rep, "") as asked:
            rep.locator(".doc-crumbs").get_by_role("link", name="Orders").click()
            rep.wait_for_url(re.compile(r"/sales/orders$"))
        self.assertEqual(asked, ["You have changes that are not saved. Leave and lose them?"])
        self.assertEqual((SalesOrder.objects.count(), SalesOrderLine.objects.count()), (0, 0))
        self.assertEqual(self.problems, [])
