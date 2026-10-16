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

    def test_02_a_bill_with_tcs_shows_it_in_the_totals_but_the_new_bill_screen_has_no_place_to_key_it(self):
        tcs = Account.objects.create(code="1461", name="TCS receivable", account_type=AccountType.ASSET)
        bill = Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 6, 30), payable_account=self.payable,
                                   tcs_amount=Decimal("1000"), tcs_account=tcs, tcs_section="206C(1F)")
        BillLine.objects.create(bill=bill, description="Truck", quantity=Decimal("1"), unit_price=Decimal("100000"),
                                expense_account=self.expense)
        bill.post()
        page = self.sign_in(self.login_for("AP Manager"), f"/app/purchasing/bills/{bill.pk}")
        expect(page.locator("main")).to_contain_text("TCS collected (206C(1F))")
        expect(page.locator("main")).to_contain_text("1,01,000.00")
        page.goto(f"{self.live_server_url}/app/purchasing/bills/new")
        expect(page.get_by_label("Vendor").first).to_be_visible()
        self.assertEqual(page.get_by_label("TCS").count(), 0, "TCS can be keyed on the screen")
        self.assertEqual(self.problems, [])
