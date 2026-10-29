"""
The rep emails a confirmed order's proforma from the order's page and
sees who it went to; the acknowledgement is offered beside it.
"""

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.core import mail

from .tests_browser import BrowserTestCase


class PapersInTheBrowserTests(BrowserTestCase):
    def test_the_proforma_goes_from_the_order(self):
        order = self.make_order()
        page = self.sign_in(self.person("AR Manager"), f"/app/sales/orders/{order.pk}")
        expect(page.get_by_role("link", name="Acknowledgement PDF")).to_be_visible()
        expect(page.get_by_role("link", name="Proforma PDF")).to_have_attribute(
            "href", f"/api/sales/sales-orders/{order.pk}/pdf/?kind=proforma")
        page.get_by_role("button", name="Email proforma").click()
        self.toast(page, "Proforma sent")
        self.assertEqual([message.to for message in mail.outbox], [["ap@acme.example"]])
        self.assertEqual(mail.outbox[0].attachments[0][0], f"Proforma invoice {order.number}.pdf")
        self.assertEqual(self.problems, [])
