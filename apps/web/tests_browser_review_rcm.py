"""Review probes (review_rcm): the reverse-charge screens as people without the right."""

import re

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    expect = None

from apps.gst.reverse_charge import SelfInvoice
from apps.gst.tests import DAY
from apps.gst.tests_rcm_cash import ReverseChargeCashTestCase

from .tests_browser import BrowserMixin


class ReverseChargeScreensWithoutTheRight(BrowserMixin, ReverseChargeCashTestCase, StaticLiveServerTestCase):
    def buttons(self, page, path):
        page.goto(f"{self.live_server_url}/app/accounts/{path}")
        page.wait_for_timeout(700)
        return {b.inner_text() for b in page.get_by_role("button").all()}

    def test_a_clerk_is_offered_no_button_on_the_screens(self):
        made = SelfInvoice.issue(self.freight_bill(), on_date=DAY)
        challan = self.pay()
        page = self.sign_in(self.login_for("Purchasing Clerk"), "/app/")
        for path in ("self-invoices/new", "payment-vouchers/new", "gst-challans/new", f"self-invoices/{made.pk}",
                     f"gst-challans/{challan.pk}"):
            self.assertFalse({"Create", "Cancel", "Void"} & self.buttons(page, path), path)

    def test_the_officer_has_no_void_and_no_pay(self):
        self.freight_bill()
        challan = self.pay()
        page = self.sign_in(self.login_for("GST Officer"), "/app/")
        self.assertFalse({"Create", "Void"} & self.buttons(page, f"gst-challans/{challan.pk}"))
        self.assertFalse({"Create"} & self.buttons(page, "gst-challans/new"))

    def test_the_ap_manager_has_no_cancel_on_a_self_invoice(self):
        made = SelfInvoice.issue(self.freight_bill(), on_date=DAY)
        page = self.sign_in(self.login_for("AP Manager"), "/app/")
        self.assertFalse({"Cancel", "Create"} & self.buttons(page, f"self-invoices/{made.pk}"))
