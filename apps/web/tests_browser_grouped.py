"""The AR manager in the browser groups the invoice list by customer and reads the count and the total."""

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from .tests_browser import BrowserTestCase


class GroupedListInTheBrowserTests(BrowserTestCase):
    def test_invoices_grouped_by_customer(self):
        for quantity in ("10", "3"):
            self.make_order(quantity=quantity).create_invoice(self.ar).post()
        page = self.sign_in(self.person("AR Manager"), "/app/sales/invoices")
        page.get_by_role("button", name="Group", exact=True).click()
        page.get_by_label("Group by").select_option(label="Customer")
        grouped = page.get_by_role("region", name="Invoices, grouped")
        expect(grouped.locator("tbody")).to_contain_text("Acme")
        expect(grouped.locator("tbody")).to_contain_text("2")
        expect(grouped.locator("tbody")).to_contain_text("1,300.00")
        self.assertEqual(self.problems, [])
