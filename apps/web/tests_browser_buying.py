"""
Buying before an order, in the browser, as the buyer: a request for
quotation put to two vendors, their quotes entered and compared, and the
cheaper one awarded, which raises the order. Each step is read back from
the database.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import PurchaseOrder, RequestForQuotation

from .tests_browser import BrowserTestCase


class RfqInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        for code, name in (("V-1", "Granule House"), ("V-2", "Beta Supplies")):
            vendor = Party.objects.create(code=code, name=name, default_currency=self.usd)
            PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)

    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def panel(self, page, title):
        return page.locator("section.related", has_text=title)

    def adder(self, page, label):
        page.get_by_role("button", name=label).click()
        return page.get_by_role("form", name=label)

    def test_put_out_quoted_compared_and_awarded(self):
        page = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/rfqs/new")
        page.get_by_label("For").fill("Widgets for the March run")
        page.get_by_label("Out on").fill("2026-01-01")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/purchasing/rfqs/\d+$"))
        rfq = RequestForQuotation.objects.get()

        form = self.adder(page, "Add a line")
        form.get_by_role("combobox", name="Item").fill("WDG")
        page.get_by_role("option", name=re.compile("WDG-1")).click()
        form.get_by_label("Unit").select_option(label="each")
        form.get_by_label("Quantity").fill("100")
        form.get_by_role("button", name="Add a line").click()
        expect(self.panel(page, "What is asked for").locator("tbody")).to_contain_text("WDG-1")

        for search, name in (("Granule", "Granule House"), ("Beta", "Beta Supplies")):
            form = self.adder(page, "Ask a vendor")
            form.get_by_role("combobox", name="Vendor").fill(search)
            page.get_by_role("option", name=re.compile(name)).click()
            form.get_by_role("button", name="Ask a vendor").click()
            expect(self.panel(page, "Vendors asked").locator("tbody")).to_contain_text(name)

        page.get_by_role("button", name="Send it out").click()
        expect(page.locator("main")).to_contain_text("Sent")
        rfq.refresh_from_db()
        self.assertEqual(rfq.status, "sent")

        for name, price in (("Granule House", "5.00"), ("Beta Supplies", "4.50")):
            form = self.adder(page, "Enter a quote")
            form.get_by_label("Vendor").select_option(label=name)
            form.get_by_label("Item").select_option(label="WDG-1 · Widget")
            form.get_by_label("Each").fill(price)
            form.get_by_label("Days to deliver").fill("7")
            form.get_by_role("button", name="Enter a quote").click()
            expect(self.panel(page, "Quotes").locator("tbody")).to_contain_text(name)

        expect(self.panel(page, "Side by side")).to_contain_text("450.00")
        beta = self.panel(page, "Vendors asked").locator("tr", has_text="Beta Supplies")
        with self.answering(page, ""):
            beta.get_by_role("button", name="Award").click()
            expect(page.locator("main")).to_contain_text("Awarded")
        order = PurchaseOrder.objects.get()
        self.assertEqual((order.vendor.name, order.lines.get().unit_price), ("Beta Supplies", Decimal("4.50")))
        rfq.refresh_from_db()
        self.assertEqual(rfq.status, "awarded")
        self.assertEqual(self.problems, [])
