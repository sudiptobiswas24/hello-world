"""
The Purchasing Clerk opens "To quote", finds the approved requisition's
widgets with Granule House in mind, ticks them, says when answers are
due and asks: the request for quotation opens with the line on it and
Granule House asked. The vendor scorecard shows Granule House's year.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    RequestForQuotation,
)

from .tests_browser import BrowserTestCase


class ToQuoteInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        dana = Party.objects.create(code="DANA", name="Dana")
        PartyRoleAssignment.objects.create(party=dana, role=PartyRole.EMPLOYEE)
        requisition = PurchaseRequisition.objects.create(requested_by=dana, request_date=datetime.date(2026, 3, 1),
                                                         needed_by=datetime.date(2026, 3, 20))
        self.line = PurchaseRequisitionLine.objects.create(requisition=requisition, item=self.item, uom=self.uom,
                                                           quantity=Decimal("10"), estimated_price=Decimal("5"),
                                                           suggested_vendor=self.vendor)
        requisition.submit()
        requisition.approve(by=get_user_model().objects.create_user("budget"))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=timezone.localdate())
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("100"),
                                         unit_price=Decimal("5"))

    def test_asking_for_quotes_and_reading_the_scorecard(self):
        page = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/to-quote")
        expect(page.get_by_role("heading", name="To quote")).to_be_visible()
        page.get_by_label(re.compile("Ask about WDG-1")).check()
        page.get_by_label("Answers by").fill("2026-03-15")
        page.get_by_role("button", name="Ask for quotes").click()
        page.wait_for_url(re.compile(r"/purchasing/rfqs/\d+$"))
        rfq = RequestForQuotation.objects.get()
        self.assertEqual([(line.requisition_line, line.quantity) for line in rfq.lines.all()], [(self.line, Decimal("10"))])
        self.assertEqual((rfq.response_due, [row.vendor for row in rfq.invited.all()]), (datetime.date(2026, 3, 15), [self.vendor]))
        expect(page.get_by_text("Granule House")).to_be_visible()

        page.goto(f"{self.live_server_url}/app/purchasing/vendor-scorecard")
        expect(page.get_by_role("heading", name="Vendor scorecard")).to_be_visible()
        row = page.locator("tbody tr").first
        expect(row).to_contain_text("Granule House")
        expect(row).to_contain_text("100")
        self.assertEqual(self.problems, [])
