"""
The self-invoice and the payment voucher on paper (CGST Rules 46 and 52),
read back against what each was issued as. Freight of 10,000 from a
trucker not registered, IGST 5% under reverse charge: taxable 10,000.00,
tax 500.00; 4,000 of it paid, the voucher's share of the tax 200.00.
"""

import datetime
import re
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .reverse_charge import PaymentVoucher, SelfInvoice
from .tests import DAY
from .tests_rcm_cash import ReverseChargeCashTestCase


class ReverseChargeOnPaperTests(ReverseChargeCashTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def printed(self, client, url):
        # The page streams uncompressed, so each string drawn can be read back (as gst.tests_tax_invoice does).
        with mock.patch("reportlab.rl_config.pageCompression", 0):
            response = client.get(url)
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "application/pdf"), url)
        found = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", response.content)
        return " ".join(re.sub(rb"\\(.)", rb"\1", text).decode("cp1252", "replace") for text in found)

    def test_the_self_invoice_prints_what_it_was_issued_as_and_says_when_cancelled(self):
        bill = self.freight_bill()
        made = SelfInvoice.issue(bill, on_date=DAY)
        url = f"/api/gst/self-invoices/{made.pk}/pdf/"
        # Who may not read self-invoices may not print one.
        self.assertEqual(self.as_("Warehouse Staff").get(url).status_code, 403)
        officer = self.as_("GST Officer")
        text = self.printed(officer, url)
        for expected in ("Self-Invoice", made.number, bill.number, "section 31(3)(f)", made.supplier_name,
                         "Not registered under GST", "10,000.00", "500.00", "5%", "9965", "Freight", f"{DAY:%d %b %Y}",
                         "State code " + made.place_of_supply, "Tax payable by the recipient"):
            self.assertIn(expected, text)
        self.assertNotIn("cancelled", text)
        self.assertEqual((made.taxable, made.igst), (D("10000.00"), D("500.00")))
        # The reverse: cancelled, it still prints, and says so.
        made.cancel("typed twice", on_date=DAY + datetime.timedelta(days=1))
        text = self.printed(officer, url)
        self.assertIn("Self-Invoice (cancelled)", text)
        self.assertIn("typed twice", text)

    def test_the_payment_voucher_prints_its_share_of_the_tax_and_says_when_its_payment_is_voided(self):
        bill = self.freight_bill(vendor=self.gta)
        payment = self.paid_supplier(bill, "4000")
        [voucher] = PaymentVoucher.issue_for(payment)
        url = f"/api/gst/payment-vouchers/{voucher.pk}/pdf/"
        self.assertEqual(self.as_("Warehouse Staff").get(url).status_code, 403)
        officer = self.as_("GST Officer")
        text = self.printed(officer, url)
        for expected in ("Payment Voucher", voucher.number, bill.number, payment.number, "section 31(3)(g)",
                         "GSTIN " + bill.party_gstin, "4,000.00", "200.00", "Tax on what was paid", "15 Sep 2026"):
            self.assertIn(expected, text)
        self.assertEqual((voucher.amount, voucher.igst), (D("4000.00"), D("200.00")))
        payment.void(on_date=datetime.date(2026, 9, 18))
        text = self.printed(officer, url)
        self.assertIn("Payment Voucher (cancelled)", text)
        self.assertIn("18 Sep 2026", text)
