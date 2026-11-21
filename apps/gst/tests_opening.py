"""
Opening balances brought in at go-live are supplies the old system
reported: no return here counts them, and none is registered for an
e-invoice again. Dated in the month the old system filed, compiled here
they read as supplies and purchases with no tax.
"""

from apps.purchasing.models import Bill, BillLine
from apps.sales.models import Invoice, InvoiceLine

from .einvoice import build
from .returns import gstr1, gstr3b
from .tests import DAY, SEPTEMBER, D, GstReturnTestCase, gstin


class OpeningBalancesAreNotSuppliesTests(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = self.party("B2B", gstin=gstin("27AAACB1234K1Z"), gst_state="27",
                                gst_registration="regular")
        self.vendor = self.party("VEN", role="vendor", gstin=gstin("27AAACV1234K1Z"), gst_state="27",
                                 gst_registration="regular")

    def opening_invoice(self):
        invoice = Invoice.objects.create(customer=self.buyer, invoice_date=DAY, reference="OLD/1",
                                         receivable_account=self.ar, currency=self.inr,
                                         is_opening_balance=True)
        InvoiceLine.objects.create(invoice=invoice, description="Opening balance: OLD/1",
                                   quantity=D("1"), unit_price=D("11800"), revenue_account=self.revenue)
        invoice.post()
        return invoice

    def test_an_opening_invoice_or_bill_is_in_no_return(self):
        self.opening_invoice()
        bill = Bill.objects.create(vendor=self.vendor, bill_date=DAY, reference="V/9",
                                   payable_account=self.ap, is_opening_balance=True)
        BillLine.objects.create(bill=bill, description="Opening balance: V/9", quantity=D("1"),
                                unit_price=D("5900"), expense_account=self.expense)
        bill.post()
        self.sell(self.buyer, "1000")  # a real supply this month, which is reported

        one = gstr1(*SEPTEMBER)
        self.assertEqual([entry["number"] for entry in one["b2b"]], [Invoice.objects.get(reference="").number])
        self.assertEqual(one["nil"], {})
        three = gstr3b(*SEPTEMBER)
        self.assertEqual((three["3.1a"]["taxable"], three["3.1c"]["taxable"]), (D("1000.00"), D("0")))
        self.assertEqual(three["5"], {"inter": D("0"), "intra": D("0")})

    def test_an_opening_invoice_is_not_registered_again(self):
        invoice = self.opening_invoice()
        with self.assertRaisesMessage(Exception, "opening balance from the old system"):
            build(invoice)
