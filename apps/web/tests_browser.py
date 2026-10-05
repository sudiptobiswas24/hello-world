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
import unittest
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

PASSWORD = "plant-ledger-42"


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
@unittest.skipIf(not finders.find("web/index.html"), "The office application is not built (npm run build)")
class BrowserMixin:
    """
    A browser on the running server, and people to sign in as. Put in
    front of whichever fixture a test needs, then StaticLiveServerTestCase.
    """

    @classmethod
    def _databases_support_transactions(cls):
        # The live server's thread shares the in-memory database and
        # cannot see inside a test's transaction; flush between instead.
        return False

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        try:
            cls.browser = cls.playwright.chromium.launch(executable_path=executable)
        except Exception as error:  # noqa: BLE001 - any launch failure means no browser
            cls.playwright.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"No usable Chromium: {str(error).splitlines()[0]}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)

    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.problems = []
        self.page = self.new_page()

    def new_page(self):
        """A browser of its own: a second person signs in beside the first."""
        context = self.browser.new_context(viewport={"width": 1366, "height": 860})
        self.addCleanup(context.close)
        self.context = context
        page = context.new_page()
        page.on("console", lambda message: message.type == "error" and self.problems.append(message.text))
        page.on("pageerror", lambda error: self.problems.append(str(error)))
        page.on("response", lambda response: response.status in (403, 404) and self.problems.append(
            f"{response.status} {response.url}"))
        # A question nobody expected (leave and lose your changes?) is a
        # fault; a test that means to answer one says so with answering().
        def unexpected(dialog):
            self.problems.append(f"dialog: {dialog.message}")
            dialog.dismiss()

        page.on("dialog", unexpected)
        page._unexpected_dialog = unexpected
        return page

    @contextlib.contextmanager
    def answering(self, page, reply):
        """The one question the next step asks, answered with `reply`; any other is still a fault."""
        asked = []

        def answer(dialog):
            asked.append(dialog.message)
            dialog.accept(reply)

        page.remove_listener("dialog", page._unexpected_dialog)
        page.once("dialog", answer)
        try:
            yield asked
        finally:
            page.on("dialog", page._unexpected_dialog)
        self.assertEqual(len(asked), 1, "the step asked nothing")

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
