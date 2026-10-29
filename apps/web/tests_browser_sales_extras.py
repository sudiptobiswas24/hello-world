"""
The sales extras in the browser, as the people who use them: accounts
make a price list and give an item its price from a quantity; a rep books
the customer's call-off against a confirmed line. Each step is read back
from the database.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.sales.call_offs import CallOff
from apps.sales.models import PriceList, SalesOrder, SalesOrderLine

from .tests_browser import BrowserTestCase


class SalesExtrasInTheBrowserTests(BrowserTestCase):
    def test_accounts_price_an_item_from_a_quantity(self):
        page = self.sign_in(self.person("AR Manager"), "/app/sales/price-lists/new")
        page.get_by_label("Code").fill("STD")
        page.get_by_label("Name").fill("Standard")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/sales/price-lists/\d+$"))
        price_list = PriceList.objects.get(code="STD")

        page.get_by_role("button", name="Add a price").click()
        form = page.get_by_role("form", name="Add a price")
        form.get_by_role("combobox", name="Item").fill(self.item.sku)
        page.get_by_role("option", name=re.compile(self.item.sku)).click()
        form.get_by_label("From quantity").fill("100")
        form.get_by_label("Unit price").fill("9.50")
        form.get_by_role("button", name="Add a price").click()
        expect(page.locator("section.related", has_text="Prices")).to_contain_text("9.50")
        entry = price_list.entries.get()
        self.assertEqual((entry.item, entry.min_quantity, entry.unit_price), (self.item, Decimal("100"), Decimal("9.50")))
        self.assertEqual(self.problems, [])

    def test_a_rep_books_a_call_off(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date="2026-09-01")
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.item.uom, quantity=Decimal("1000"),
                                             unit_price=Decimal("10"), warehouse=self.warehouse)
        order.confirm()
        order.refresh_from_db()
        page = self.sign_in(self.person("Sales Rep"), "/app/sales/call-offs/new")
        page.get_by_role("combobox", name="Order line").fill(order.number)
        page.get_by_role("option", name=re.compile(order.number)).click()
        page.get_by_label("Due").fill("2026-10-15")
        page.get_by_label("Quantity").fill("300")
        page.get_by_label("Their reference").fill("REL-7")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/sales/call-offs/\d+$"))
        call_off = CallOff.objects.get(line=line)
        self.assertEqual((call_off.quantity, call_off.reference), (Decimal("300"), "REL-7"))
        self.assertEqual(self.problems, [])
