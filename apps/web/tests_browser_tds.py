"""
TDS in the browser, as the people who handle it: the AP manager deducts
on a bill and pays the month over by challan; the AR manager records what
a customer deducted, and the controller confirms it against Form 26AS.
Each step is read back from the database; figures are the scenario
table's (tests_tds in purchasing and sales).
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.models import Account, AccountType, TdsSection
from apps.purchasing.models import Bill, TdsChallan
from apps.purchasing.tests_tds import TdsTestCase
from apps.sales.models import CustomerTds, Invoice

from .tests_browser import BrowserMixin, BrowserTestCase


class VendorTdsInTheBrowserTests(BrowserMixin, TdsTestCase, StaticLiveServerTestCase):
    def test_deducted_on_the_bill_and_paid_over(self):
        party = self.contractor(pan="AAAPL1234C")
        bill = self.bill("35000", vendor=party)
        page = self.sign_in(self.login_for("AP Manager"), f"/app/purchasing/bills/{bill.pk}")
        with self.answering(page, ""):
            page.get_by_role("button", name="Deduct TDS").click()
            expect(page.locator("main")).to_contain_text("34,300.00")
        self.assertEqual(Bill.objects.get(pk=bill.pk).amount_tds(), Decimal("700.00"))

        page.goto(f"{self.live_server_url}/app/accounts/tds-challans/new")
        page.get_by_label("Section").select_option(label="194C · Contractors")
        page.get_by_label("Deducted in").fill("2026-06-01")
        page.get_by_label("Paid on").fill("2026-07-07")
        page.get_by_role("combobox", name="From").fill("1010")
        page.get_by_role("option", name=re.compile("1010")).click()
        page.get_by_label("Challan number").fill("00042")
        page.get_by_label("BSR code").fill("0510308")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/tds-challans/\d+$"))
        expect(page.locator("main")).to_contain_text("700.00")
        self.assertEqual(TdsChallan.objects.get().amount, Decimal("700.00"))
        self.assertEqual(self.problems, [])


class CustomerTdsInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        receivable = Account.objects.create(code="1450", name="TDS receivable", account_type=AccountType.ASSET)
        TdsSection.objects.create(code="194Q", name="Purchase of goods", rate_percent=Decimal("0.1"),
                                  no_pan_rate_percent=Decimal("5"), mode="excess",
                                  annual_threshold=Decimal("5000000"), receivable_account=receivable)
        self.invoice = self.bill(self.make_order("1", "118000"))

    def test_recorded_from_the_remittance_and_confirmed(self):
        page = self.sign_in(self.person("AR Manager"), "/app/accounts/customer-tds/new")
        page.get_by_role("combobox", name="Invoice").fill(self.invoice.number)
        page.get_by_role("option", name=re.compile(re.escape(self.invoice.number))).click()
        page.get_by_label("Section").select_option(label="194Q · Purchase of goods")
        page.get_by_label("Deducted").fill("100")
        page.get_by_label("On", exact=True).fill("2026-03-20")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/customer-tds/\d+$"))
        self.assertEqual(Invoice.objects.get(pk=self.invoice.pk).amount_due(), Decimal("117900.00"))
        expect(page.get_by_role("button", name="Found in 26AS")).to_have_count(0)

        tds = CustomerTds.objects.get()
        controller = self.sign_in(self.person("Controller"), f"/app/accounts/customer-tds/{tds.pk}", page=self.new_page())
        controller.get_by_role("button", name="Found in 26AS").click()
        form = controller.get_by_role("form", name="Found in 26AS")
        form.get_by_label("On").fill("2026-07-15")
        form.get_by_label("Form 16A").fill("16A-77")
        form.get_by_role("button", name="Found in 26AS").click()
        expect(controller.locator("main")).to_contain_text("In 26AS")
        tds.refresh_from_db()
        self.assertEqual((str(tds.confirmed_on), tds.certificate), ("2026-07-15", "16A-77"))
        self.assertEqual(self.problems, [])


class ClaimInTheBrowserTests(BrowserTestCase):
    """A cement buyer short-paid for torn bags, credited from the claims screen by the AR manager."""

    def test_a_claim_credited_and_reported(self):
        invoice = self.bill(self.make_order("10", "1000"))
        page = self.sign_in(self.person("AR Manager"), "/app/sales/claims")
        page.get_by_role("link", name="New claim").click()
        page.get_by_role("combobox", name="On invoice").fill(invoice.number)
        page.get_by_role("option", name=re.compile(re.escape(invoice.number))).click()
        page.get_by_label("For", exact=True).select_option(label="Torn or damaged bags")
        page.get_by_label("Given back, before tax").fill("500")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/sales/invoices/\d+$"))
        note = Invoice.objects.get(claim_reason="torn")
        self.assertEqual((note.credits_id, note.subtotal()), (invoice.pk, Decimal("500.00")))
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).amount_due(), Decimal("9500.00"))
        page.goto(f"{self.live_server_url}/app/sales/claims")
        expect(page.locator("tbody")).to_contain_text("Torn or damaged bags")
        self.assertEqual(self.problems, [])
