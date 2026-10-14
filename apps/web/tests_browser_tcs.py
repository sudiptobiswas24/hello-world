"""
TCS in the browser, as the accountant (the Controller) works it each month:
the rate entered and confirmed, a scrap invoice collecting 1,062 on
106,200.00, the month showing it due, a challan paying it over, and a
credit note for 500 kg giving 266 back (apps/sales/tests_tcs.py, T1 and T3).
A sales rep is offered none of it and refused it.
"""

import datetime
import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import TcsRate
from apps.sales.tcs import TcsChallan
from apps.sales.tests_tcs import TcsTestCase

from .tests_browser import BrowserMixin


class TcsInTheBrowserTests(BrowserMixin, TcsTestCase, StaticLiveServerTestCase):
    # The e-invoice fixtures' invoice(), not the browser mixin's order-driven one.
    invoice = TcsTestCase.invoice

    def setUp(self):
        super().setUp()
        TcsRate.objects.all().delete()  # entered and confirmed on the screen

    def test_the_accountant_confirms_the_rate_and_pays_the_month_over(self):
        page = self.sign_in(self.login_for("Controller"), "/app/settings/tcs-rates/new")
        page.get_by_label("Section").select_option(label="206C(1) · Scrap")
        page.get_by_label("Applies from").fill("2026-04-01")
        page.get_by_label("Rate %").fill("1")
        page.get_by_label("Without PAN %").fill("5")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/tcs-rates/\d+$"))
        page.get_by_role("button", name="Confirm", exact=True).click()
        expect(page.get_by_role("button", name="Confirm", exact=True)).to_have_count(0)
        self.assertIsNotNone(TcsRate.objects.get().confirmed_on)

        invoice = self.t1()  # posted by sales, as the day's scrap sale is
        page.goto(f"{self.live_server_url}/app/accounts/tcs-collected")
        row = page.locator("tbody tr", has_text=invoice.number)
        expect(row).to_contain_text("1,062.00")
        expect(row).to_contain_text("Due")

        page.goto(f"{self.live_server_url}/app/accounts/tcs-challans/new")
        page.get_by_label("Section").select_option(label="206C(1) · Scrap")
        page.get_by_label("Collected to").fill("2026-09-01")
        page.get_by_label("Paid on").fill("2026-10-05")
        page.get_by_role("combobox", name="From").fill("1010")
        page.get_by_role("option", name=re.compile("1010")).click()
        page.get_by_label("Challan number").fill("00123")
        page.get_by_label("BSR code").fill("0510308")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/tcs-challans/\d+$"))
        expect(page.locator("main")).to_contain_text("1,062.00")
        self.assertEqual(TcsChallan.objects.get().amount, Decimal("1062.00"))

        note = self.credit(invoice, "500", datetime.date(2026, 9, 20))
        page.goto(f"{self.live_server_url}/app/accounts/tcs-collected")
        given_back = page.locator("tbody tr", has_text=note.number)
        expect(given_back).to_contain_text("266.00")
        expect(given_back).to_contain_text("Given back")
        expect(given_back).to_contain_text("Due")
        expect(page.locator("tbody tr", has_text=invoice.number)).to_contain_text("Paid over")
        self.assertEqual(self.problems, [])

    def test_a_sales_rep_is_offered_none_of_it(self):
        rep = self.login_for("Sales Rep")
        page = self.sign_in(rep, "/app/")
        expect(page.get_by_role("link", name="Customers")).to_be_visible()
        expect(page.get_by_role("link", name="TCS collected")).to_have_count(0)
        expect(page.get_by_role("link", name="TCS challans")).to_have_count(0)
        client = Client()
        client.force_login(rep)
        self.assertEqual([client.get(url).status_code for url in (
            "/api/sales/tcs-collections/", "/api/sales/tcs-challans/", "/api/accounting/tcs-rates/")], [403] * 3)
        self.assertEqual(self.problems, [])
