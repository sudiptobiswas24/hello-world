"""
The office application in a real browser against a running server: sign
in, see only what the role opens, find an invoice, and have the session
end mid-search.

Needs the built application (npm run build in frontend/) and a Chromium
Playwright can launch; skipped, saying which, where either is missing.
"""

import contextlib
import datetime
import os
import pickle
import sqlite3
import threading
import time
import unittest
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabase
from django.db.backends.sqlite3.base import SQLiteCursorWrapper

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase, carries_every_customer

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

PASSWORD = "plant-ledger-42"


class OneCallAtATime(SQLiteCursorWrapper):
    """
    On SQLite the live server's threads share one in-memory connection
    (and the test, which Playwright's greenlet hands a connection of its
    own, shares its cache). Two calls into it at once can deadlock: one
    holds SQLite's lock while it calls back into Python (Django's date
    functions) and waits for the GIL, while the other holds the GIL and
    waits for SQLite's lock. The stock valuation screen, asking two
    things at once, hung a run that way. One call at a time, whichever
    thread or connection makes it. PostgreSQL is left alone.

    The two connections also lock each other out by the table: one still
    reading auth_user for a request in flight blocks the other's write
    into it, and shared-cache SQLite refuses at once ("database table is
    locked") rather than waiting as it would for a busy file. The write
    waits here, for the request to end (tests_sqlite_locks.py).
    """

    lock = threading.RLock()
    waits_for = 15  # seconds, before a lock that never lifts is reported

    def _waiting(self, call, *args, **kwargs):
        deadline = time.monotonic() + self.waits_for
        while True:
            with self.lock:
                try:
                    return call(*args, **kwargs)
                except sqlite3.OperationalError as error:
                    if "is locked" not in str(error) or time.monotonic() >= deadline:
                        raise
            time.sleep(0.01)

    def execute(self, *args, **kwargs):
        return self._waiting(super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._waiting(super().executemany, *args, **kwargs)

    def fetchone(self):
        with self.lock:
            return super().fetchone()

    def fetchmany(self, *args, **kwargs):
        with self.lock:
            return super().fetchmany(*args, **kwargs)

    def fetchall(self):
        with self.lock:
            return super().fetchall()

    def __next__(self):
        with self.lock:
            return super().__next__()

    def close(self):
        with self.lock:
            return super().close()


_PLAIN_CURSOR = SQLiteDatabase.create_cursor


def _one_call_at_a_time(on):
    if any(connection.vendor == "sqlite" and connection.is_in_memory_db() for connection in connections.all()):
        SQLiteDatabase.create_cursor = (
            (lambda self, name=None: self.connection.cursor(factory=OneCallAtATime)) if on else _PLAIN_CURSOR)


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
@unittest.skipIf(not finders.find("web/index.html"), "The office application is not built (npm run build)")
class BrowserMixin:
    """
    A browser on the running server, and people to sign in as. Put in
    front of whichever fixture a test needs, then StaticLiveServerTestCase.
    """

    def __getstate__(self):
        # A failed subtest is sent back to the parallel runner with its test
        # attached, and the page and browser context cannot be pickled: the
        # runner died reporting nothing for the whole lane. What can travel does.
        state = {}
        for key, value in self.__dict__.items():
            try:
                pickle.dumps(value)
            except Exception:  # noqa: BLE001 - whatever cannot be pickled stays behind
                continue
            state[key] = value
        return state

    @classmethod
    def _databases_support_transactions(cls):
        # The live server's thread shares the in-memory database and
        # cannot see inside a test's transaction; flush between instead.
        return False

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        _one_call_at_a_time(True)
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # Playwright's own five seconds for an expectation is a guess about
        # an idle machine. The gate runs its three suites side by side, and
        # a page rendering slowly under that load is not a defect; one that
        # never shows what it should still fails, fifteen seconds later.
        expect.set_options(timeout=15_000)
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        try:
            cls.browser = cls.playwright.chromium.launch(executable_path=executable)
        except Exception as error:  # noqa: BLE001 - any launch failure means no browser
            cls.playwright.stop()
            super().tearDownClass()
            _one_call_at_a_time(False)
            raise unittest.SkipTest(f"No usable Chromium: {str(error).splitlines()[0]}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()
        _one_call_at_a_time(False)
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.problems = []
        self.contexts = []
        self.page = self.new_page()

    def tearDown(self):
        # Let every page's last requests finish before the database is
        # flushed. A write still inside its transaction when the flush's
        # TRUNCATE arrives deadlocks on PostgreSQL, and the next test's
        # setUp fails on the wreck: two of a gate's browser tests, both
        # ending on an action whose answer refetches the whole screen.
        for context in self.contexts:
            for page in context.pages:
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:  # noqa: BLE001 - a page already gone, or one that never settles
                    pass
        super().tearDown()

    def new_page(self):
        """A browser of its own: a second person signs in beside the first."""
        context = self.browser.new_context(viewport={"width": 1366, "height": 860})
        self.addCleanup(context.close)
        self.contexts.append(context)
        self.context = context
        page = context.new_page()
        page.on("console", lambda message: message.type == "error" and self.problems.append(message.text))
        page.on("pageerror", lambda error: self.problems.append(str(error)))
        # A refusal, a missing page or a server fault, with its address:
        # the console says only "status of 500", not which request.
        page.on("response", lambda response: (response.status in (403, 404) or response.status >= 500)
                and self.problems.append(f"{response.status} {response.url}"))
        # A question nobody expected (leave and lose your changes?) is a
        # fault; a test that means to answer one says so with answering().
        def unexpected(dialog):
            self.problems.append(f"dialog: {dialog.message}")
            dialog.dismiss()

        page.on("dialog", unexpected)
        page._unexpected_dialog = unexpected
        return page

    @contextlib.contextmanager
    def answering(self, page, *replies):
        """The questions the next step asks, answered in turn with `replies`; any other is still a fault."""
        asked = []

        def answer(dialog):
            if len(asked) == len(replies):
                page._unexpected_dialog(dialog)
                return
            asked.append(dialog.message)
            dialog.accept(replies[len(asked) - 1])

        page.remove_listener("dialog", page._unexpected_dialog)
        page.on("dialog", answer)
        try:
            yield asked
        finally:
            page.remove_listener("dialog", answer)
            page.on("dialog", page._unexpected_dialog)
        self.assertEqual(len(asked), len(replies), "the step asked fewer questions than were answered")

    def invoice(self, customer, day, price, post=True):
        invoice = Invoice.objects.create(customer=customer, invoice_date=day,
                                         receivable_account=self.ar, currency=self.usd)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=Decimal("1"),
                                   unit_price=Decimal(price), revenue_account=self.revenue)
        if post:
            invoice.post()
        return invoice

    def person(self, role):
        return self.login_for(role)

    def login_for(self, role):
        """person() by a name no fixture uses: payroll's keeps an employee in self.person."""
        user = User.objects.create_user(role.replace(" ", "_").lower(), password=PASSWORD, first_name="Asha")
        user.groups.add(Group.objects.get(name=role))
        if role == "Sales Rep":
            carries_every_customer(user)
        return user

    def sign_in(self, user, path="/app/", page=None):
        page = page or self.page
        page.goto(f"{self.live_server_url}{path}")
        page.fill("#id_username", user.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        return page

    def rows(self):
        return self.page.locator("tbody tr:not(.skeleton)")

    def toast(self, page, text):
        expect(page.locator(".toast", has_text=text).first).to_be_visible()


class BrowserTestCase(BrowserMixin, SalesTestCase, StaticLiveServerTestCase):
    """The browser over the sales fixture: a customer, an item, a shelf."""


class OfficeApplicationTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.bolt = Party.objects.create(code="C-2", name="Bolt Traders", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.bolt, role=PartyRole.CUSTOMER)
        self.invoices = [
            self.invoice(self.customer, datetime.date(2026, 3, 1), "1250.50"),
            self.invoice(self.bolt, datetime.date(2026, 4, 1), "200"),
            self.invoice(self.customer, datetime.date(2026, 5, 1), "75"),
            self.invoice(self.bolt, datetime.date(2026, 5, 2), "80", post=False),
        ]

    def test_sign_in_returns_to_the_screen_asked_for(self):
        page = self.sign_in(self.person("AR Manager"), "/app/sales/invoices")
        expect(page.get_by_role("heading", name="Invoices")).to_be_visible()
        self.assertTrue(page.url.endswith("/app/sales/invoices"))
        expect(page.locator(".pager .count")).to_have_text("1–4 of 4")
        self.assertEqual(self.problems, [])

    def test_finding_an_invoice(self):
        page = self.sign_in(self.person("AR Manager"), "/app/sales/invoices")
        expect(self.rows()).to_have_count(4)

        page.keyboard.press("/")
        page.keyboard.type("bolt")
        expect(page.locator(".pager .count")).to_have_text("1–2 of 2")
        expect(self.rows()).to_have_count(2)
        self.assertIn("q=bolt", page.url)

        page.get_by_role("button", name="Drafts").click()
        expect(self.rows()).to_have_count(1)
        expect(self.rows().first).to_contain_text("Draft")

        page.get_by_role("button", name="Clear").click()
        expect(self.rows()).to_have_count(4)

        # Money as the exact figure, grouped the Indian way.
        expect(page.locator("td.k-money", has_text="1,250.50").first).to_be_visible()

        page.go_back()
        expect(self.rows()).to_have_count(1)
        self.assertEqual(self.problems, [])

    def test_the_palette_goes_where_it_is_told(self):
        page = self.sign_in(self.person("AR Manager"))
        expect(page.get_by_role("heading", name="Good", exact=False)).to_be_visible()
        page.keyboard.press("Control+k")
        page.keyboard.type("cust")
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="Customers")).to_be_visible()
        expect(self.rows()).to_have_count(2)

    def test_only_what_the_role_opens_is_offered(self):
        """The store dispatches against orders, so it reads orders and
        customers; what was billed is not its business."""
        page = self.sign_in(self.person("Warehouse Staff"))
        expect(page.get_by_role("link", name="Orders", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Deliveries", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Customers")).to_be_visible()
        expect(page.get_by_role("link", name="Invoices")).to_have_count(0)

        # Typed into the address bar anyway: the server refuses it.
        page.goto(f"{self.live_server_url}/app/sales/invoices")
        expect(page.get_by_role("heading", name="Not allowed")).to_be_visible()
        self.assertEqual(self.rows().count(), 0)

    def test_a_login_with_no_role_is_told_why_there_is_nothing(self):
        user = User.objects.create_user("newcomer", password=PASSWORD)
        page = self.sign_in(user)
        expect(page.get_by_text("Your account has no role yet")).to_be_visible()
        expect(page.locator(".rail-item")).to_have_count(1)  # Home only

    def test_a_session_that_ends_mid_search_goes_back_to_sign_in(self):
        page = self.sign_in(self.person("AR Manager"), "/app/sales/invoices")
        expect(self.rows()).to_have_count(4)
        self.context.clear_cookies()
        page.locator("input[type=search]").fill("bolt")
        page.wait_for_url("**/accounts/login/**")
        self.assertIn("next=%2Fapp%2Fsales%2Finvoices", page.url)

    def test_signing_out(self):
        page = self.sign_in(self.person("AR Manager"))
        page.locator(".avatar").click()
        page.get_by_role("menuitem", name="Sign out").click()
        page.wait_for_url("**/accounts/login/**")
        page.goto(f"{self.live_server_url}/app/")
        page.wait_for_url("**/accounts/login/**")

    def test_an_unknown_screen_says_so(self):
        page = self.sign_in(self.person("AR Manager"), "/app/nowhere")
        expect(page.get_by_role("heading", name="No such screen")).to_be_visible()
