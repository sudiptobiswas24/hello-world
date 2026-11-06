"""
Warehouse staff open today's pick list and find the draft delivery's
widgets on it; the delivery's own page shows the same route and offers
the paper.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.utils import timezone

from apps.sales.models import Delivery, DeliveryLine

from .tests_browser import BrowserTestCase


class PickingInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        order = self.make_order("10")
        self.delivery = Delivery.objects.create(sales_order=order, delivery_date=timezone.localdate())
        DeliveryLine.objects.create(delivery=self.delivery, order_line=order.lines.get(), warehouse=self.warehouse,
                                    quantity_shipped=Decimal("10"))

    def test_todays_route_and_the_deliverys_own(self):
        page = self.sign_in(self.person("Warehouse Staff"), "/app/stores/picking")
        expect(page.get_by_role("heading", name="Pick list")).to_be_visible()
        row = page.locator("tbody tr").first
        expect(row).to_contain_text("WDG-1")
        expect(row).to_contain_text("10")

        page.goto(f"{self.live_server_url}/app/sales/deliveries/{self.delivery.pk}")
        section = page.get_by_role("region", name="Pick list")
        expect(section).to_be_visible()
        expect(section.locator("tbody tr").first).to_contain_text("WDG-1")
        expect(section.get_by_role("link", name="Pick list PDF")).to_have_attribute("href", re.compile(r"/pick-list/pdf/$"))
        self.assertEqual(self.problems, [])
