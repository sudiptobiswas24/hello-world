"""
The early-settlement discount taken on the receipt that earned it, as the AR manager does it (O109).
"""

import datetime
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None


from .tests_browser import BrowserTestCase


class SettlementDiscountInTheBrowserTests(BrowserTestCase):
    def test_the_discount_is_taken_on_the_receipt_that_earned_it(self):
        # O109: 1,000 on 2/10 net 30 invoiced 1 March, 980 received 8 March and applied: 20 discount.
        from apps.sales.models import Invoice, InvoicePayment

        invoice = self.bill(self.make_order("10", "100"))
        receipt = self.receipt("980", on=datetime.date(2026, 3, 8))
        InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=Decimal("980"))
        page = self.sign_in(self.person("AR Manager"), f"/app/sales/receipts/{receipt.pk}")
        page.get_by_role("button", name="Take the discount").click()
        expect(page.locator(".toast", has_text="Settlement discount taken").first).to_be_visible()
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.settlement_discount_amount, invoice.amount_due()), (Decimal("20.00"), Decimal("0.00")))
        self.assertEqual(self.problems, [])
