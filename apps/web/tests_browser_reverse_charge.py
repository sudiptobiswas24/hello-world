"""
Reverse charge in the browser, as the people who handle it (setup_roles): the GST officer issues a
self-invoice from a bill and cancels it; the AP manager issues the payment voucher with the payment, pays
the month's tax in cash by challan and voids it; the GST officer reads the cash ledger. Figures are
gst.tests_rcm_cash's (freight 10,000, IGST 5% = 500; 4,000 of it paid: voucher IGST 200).
"""

import datetime
import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.gst.reverse_charge import GstCashPayment, PaymentVoucher, SelfInvoice
from apps.gst.tests_rcm_cash import ReverseChargeCashTestCase

from .tests_browser import BrowserMixin


class ReverseChargeInTheBrowserTests(BrowserMixin, ReverseChargeCashTestCase, StaticLiveServerTestCase):
    def pick(self, page, label, text):
        page.get_by_role("combobox", name=label).fill(text)
        page.get_by_role("option", name=re.compile(re.escape(text))).click()

    def test_the_officer_issues_and_cancels_a_self_invoice(self):
        bill = self.freight_bill()
        page = self.sign_in(self.login_for("GST Officer"), "/app/accounts/self-invoices/new")
        self.pick(page, "From bill", bill.number)
        page.get_by_label("Issued on").fill("2026-09-10")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/self-invoices/\d+$"))
        expect(page.locator("main")).to_contain_text("500.00")
        made = SelfInvoice.objects.get()
        self.assertEqual((made.number, made.igst), ("SI-2026-00001", Decimal("500.00")))
        page.get_by_role("button", name="Cancel", exact=True).click()
        form = page.get_by_role("form", name="Cancel")
        form.get_by_label("Why").fill("typed against the wrong bill")
        form.get_by_label("Cancelled on").fill("2026-09-11")
        form.get_by_role("button", name="Cancel").click()
        expect(page.locator("main")).to_contain_text("Cancelled")
        self.assertEqual(SelfInvoice.objects.get().cancelled_on, datetime.date(2026, 9, 11))
        page.goto(f"{self.live_server_url}/app/accounts/self-invoices")
        expect(page.locator("tbody")).to_contain_text("SI-2026-00001")
        self.assertEqual(self.problems, [])

    def test_the_ap_manager_vouchers_the_payment_and_pays_the_tax_in_cash(self):
        bill = self.freight_bill(vendor=self.gta)
        payment = self.paid_supplier(bill, "4000")
        page = self.sign_in(self.login_for("AP Manager"), "/app/accounts/payment-vouchers/new")
        self.pick(page, "For payment", payment.number)
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/payment-vouchers/\d+$"))
        expect(page.locator("main")).to_contain_text("200.00")
        self.assertEqual(PaymentVoucher.objects.get().igst, Decimal("200.00"))

        page.goto(f"{self.live_server_url}/app/accounts/gst-challans/new")
        page.get_by_label("Tax of").fill("2026-09-01")
        page.get_by_label("Paid on").fill("2026-10-15")
        self.pick(page, "From", "1010")
        self.pick(page, "Cash ledger account", "1395")
        page.get_by_label("CPIN").fill("12345678901234")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/gst-challans/\d+$"))
        expect(page.locator("main")).to_contain_text("500.00")
        self.assertEqual(GstCashPayment.objects.get().igst, Decimal("500.00"))
        page.get_by_role("button", name="Void", exact=True).click()
        expect(page.locator("main")).to_contain_text("Voided")
        self.assertIsNotNone(GstCashPayment.objects.get().voided_entry_id)
        self.assertEqual(self.problems, [])

    def test_the_officer_reads_the_cash_ledger(self):
        self.freight_bill()
        self.pay()
        page = self.sign_in(self.login_for("GST Officer"), "/app/accounts/gst-cash-ledger")
        expect(page.locator("tbody")).to_contain_text("IGST")
        expect(page.locator("tbody")).to_contain_text("500.00")
        page.goto(f"{self.live_server_url}/app/accounts/gst-challans")
        expect(page.locator("tbody")).to_contain_text("12345678901234")
        expect(page.get_by_role("link", name=re.compile("New"))).to_have_count(0)
        self.assertEqual(self.problems, [])
