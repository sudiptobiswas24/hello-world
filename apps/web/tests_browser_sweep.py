"""
Every role, every screen it is offered, in a real browser.

The other browser tests walk a flow, and their role checks open screens
from a list somebody wrote. A screen added to the navigation and left
off that list was never opened as anyone. This walks what the
navigation itself offers each role: every module, every tab, the first
record of each list and the New form where the role may make one. Any
refusal (4xx), server fault (5xx), error panel, console error or
unexpected question fails it, naming the role and the screen.

Run over three fixtures between them holding a record of every kind a
screen lists: sales and purchasing with the books, planning and the
floor, and payroll.
"""

import datetime
import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from apps.core.management.commands.setup_roles import ROLES
from apps.hr.tests_payroll_api import PayrollApiTestCase
from apps.planning.tests_mrp import PlanningTestCase

from .tests_browser import BrowserMixin, BrowserTestCase


class SweepMixin:
    # What a fixture legitimately answers with a refusal, as (status, URL
    # fragment). Anything else refused is a fault.
    expected_refusals = ()

    def sweep(self, roles=None):
        """{role: [paths opened]}, failing on the first role with a problem."""
        from apps.accounting.gst import GstSettings
        from apps.accounting.models import FiscalPosition

        # Unregistered, the returns answer 400 "GST is not set up", which
        # is right (tests_browser_accounts holds it) and opens nothing.
        if not GstSettings.objects.exists():
            GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=FiscalPosition.objects.create(
                code="INTER", name="Inter-state"))
        opened = {}
        for role in roles or list(ROLES):
            with self.subTest(role=role):
                self.problems.clear()
                page = self.new_page()
                page.on("response", lambda response: self._note_failure(response))
                self.sign_in(self.login_for(role), "/app/", page=page)
                page.wait_for_load_state("networkidle")
                opened[role] = self._walk(role, page)
                # Closed now, not at the end of the test: the browser keeps
                # its sockets to the live server open, each holding a database
                # connection, and sixteen roles' worth ran PostgreSQL out of
                # them when the suite runs four at once.
                page.context.close()
                self.assertEqual(self.problems, [], role)
        return opened

    def _note_failure(self, response):
        # 403 and 404 are already noted by BrowserMixin; the rest here.
        if response.status >= 400 and response.status not in (403, 404):
            if not any(status == response.status and part in response.url
                       for status, part in self.expected_refusals):
                self.problems.append(f"{response.status} {response.url}")

    def _check(self, role, page, where):
        page.wait_for_load_state("networkidle")
        panels = page.locator(".error-panel")
        if panels.count():
            self.problems.append(f"{where}: {panels.first.inner_text()[:200]}")

    def _walk(self, role, page):
        base = self.live_server_url
        modules = [href for href in (link.get_attribute("href") for link in
                                     page.locator("nav.rail a.rail-item").all()) if href and href.rstrip("/") != "/app"]
        screens = []
        for module in modules:
            page.goto(base + module)
            page.wait_for_load_state("networkidle")
            for link in page.locator("nav.tabs a").all():
                href = link.get_attribute("href")
                if href and href not in screens:
                    screens.append(href)
        for href in screens:
            page.goto(base + href)
            self._check(role, page, href)
            if page.locator("a.btn.primary", has_text=re.compile(r"^New$")).count():
                page.goto(f"{base}{href}/new")
                self._check(role, page, f"{href}/new")
                page.goto(base + href)
                page.wait_for_load_state("networkidle")
            row = page.locator("main tbody tr.link").first
            if row.count():
                before = page.url
                row.click()
                page.wait_for_url(lambda url: url != before, timeout=5000)
                self._check(role, page, page.url)
        return screens


class SalesBuyingAndBooksSweepTests(SweepMixin, BrowserTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import (
            Account,
            AccountType,
            JournalEntry,
            JournalLine,
            Payment,
            PaymentDirection,
        )
        from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
        from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
        from apps.sales.models import Quotation, QuotationLine

        payable = Account.objects.create(code="2000", name="Payables", account_type=AccountType.LIABILITY)
        company = Company.get()
        company.default_payable_account = payable
        company.default_bank_account = self.bank
        company.save()

        order = self.make_order(quantity="10", price="100")
        self.ship(order, "10")
        invoice = self.bill(order)
        self.allocate(self.receipt("600"), invoice, "600")
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             currency=self.usd)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("5"),
                                     unit_price=Decimal("90"), revenue_account=self.revenue)

        vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        purchase = PurchaseOrder.objects.create(vendor=vendor, order_date=datetime.date(2026, 3, 1),
                                                currency=self.usd)
        PurchaseOrderLine.objects.create(order=purchase, item=self.item, uom=self.uom, quantity=Decimal("50"),
                                         unit_price=Decimal("4"))
        purchase.confirm()
        purchase.create_receipt(warehouse=self.warehouse).post()
        bill = purchase.create_bill(payable)
        bill.post()
        Payment.objects.create(
            party=vendor, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 3, 12),
            amount=Decimal("100"), currency=self.usd, bank_account=self.bank, counterpart_account=payable,
        ).post()

        entry = JournalEntry.objects.create(date=datetime.date(2026, 3, 5), memo="Bank charges")
        JournalLine.objects.create(entry=entry, account=self.discount_account, debit=Decimal("5"), credit=0)
        JournalLine.objects.create(entry=entry, account=self.bank, debit=0, credit=Decimal("5"))
        entry.post()

    def test_every_role_opens_every_screen_it_is_offered(self):
        opened = self.sweep()
        # The sweep is only worth its silence if it went somewhere.
        self.assertIn("/app/sales/invoices", opened["AR Manager"])
        self.assertIn("/app/accounts/gst", opened["GST Officer"])
        self.assertIn("/app/purchasing/bills", opened["AP Manager"])


class PlanningAndTheFloorSweepTests(SweepMixin, BrowserMixin, PlanningTestCase, StaticLiveServerTestCase):
    def test_every_role_opens_every_screen_it_is_offered(self):
        self.stock(self.tape, "2000")
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        {o.item.sku: o for o in run.orders.all()}["FAB-10X10"].firm().release()
        opened = self.sweep()
        self.assertIn("/app/production/work-orders", opened["Production Supervisor"])
        self.assertIn("/app/production/plan", opened["Production Planner"])


class PayrollSweepTests(SweepMixin, BrowserMixin, PayrollApiTestCase, StaticLiveServerTestCase):
    def test_every_role_opens_every_screen_it_is_offered(self):
        self.pay_run().calculate()
        opened = self.sweep()
        self.assertIn("/app/payroll/runs", opened["Payroll Officer"])
