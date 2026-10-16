"""g22 review_purch: the screens fix_purch added or changed, as the people who use them."""

import datetime
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    expect = None

from apps.accounting.models import Account, AccountType, TdsRate
from apps.purchasing.models import Bill, BillLine
from apps.purchasing.tests_tds import TdsTestCase

from .tests_browser import BrowserMixin

D = datetime.date


class ReviewPurchInTheBrowser(BrowserMixin, TdsTestCase, StaticLiveServerTestCase):
    def test_01_the_ap_manager_reads_a_pending_rate_and_cannot_confirm_the_controller_can(self):
        pending = TdsRate.objects.create(section=self.contract, applies_from=D(2026, 9, 1),
                                         rate_percent=Decimal("3"), no_pan_rate_percent=Decimal("25"))
        path = f"/app/settings/tds-rates/{pending.pk}"
        ap = self.sign_in(self.login_for("AP Manager"), path)
        expect(ap.locator("main")).to_contain_text("To confirm")
        expect(ap.get_by_role("button", name="Confirm")).to_have_count(0)
        controller = self.sign_in(self.login_for("Controller"), path, page=self.new_page())
        controller.get_by_role("button", name="Confirm").click()
        expect(controller.locator("main")).to_contain_text("Confirmed")
        pending.refresh_from_db()
        self.assertIsNotNone(pending.confirmed_on)
        self.assertEqual(self.problems, [])

    def test_02_the_ap_manager_keys_the_suppliers_tcs_on_a_new_bill_and_the_totals_carry_it(self):
        """Review F10: the supplier's TCS is keyed on the new-bill screen, and the posted bill shows it."""
        import re

        from apps.core.models import Company

        tcs = Account.objects.create(code="1461", name="TCS receivable", account_type=AccountType.ASSET)
        Company.objects.filter(pk=Company.get().pk).update(default_payable_account=self.payable)
        page = self.sign_in(self.login_for("AP Manager"), "/app/purchasing/bills/new")
        page.get_by_role("combobox", name="Vendor").fill("Supplier")
        page.get_by_role("option", name=re.compile("Supplier")).first.click()
        page.get_by_label("Date").fill("2026-06-30")
        page.get_by_label("Their invoice number").fill("TRK-9")
        page.get_by_label("TCS collected").fill("1000")
        page.get_by_label("TCS section").fill("206C(1F)")
        page.get_by_role("combobox", name="TCS receivable account").fill("1461")
        page.get_by_role("option", name=re.compile("1461 TCS receivable")).click()
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/purchasing/bills/\d+$"))
        bill = Bill.objects.get(reference="TRK-9")
        self.assertEqual((bill.tcs_amount, bill.tcs_section, bill.tcs_account_id), (Decimal("1000.00"), "206C(1F)", tcs.pk))
        BillLine.objects.create(bill=bill, description="Truck", quantity=Decimal("1"), unit_price=Decimal("100000"),
                                expense_account=self.expense)
        bill.post()
        page.goto(f"{self.live_server_url}/app/purchasing/bills/{bill.pk}")
        expect(page.locator("main")).to_contain_text("TCS collected (206C(1F))")
        expect(page.locator("main")).to_contain_text("1,01,000.00")
        self.assertEqual(self.problems, [])
