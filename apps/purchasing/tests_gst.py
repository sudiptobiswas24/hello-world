from decimal import Decimal

from apps.accounting.gst import GstSettings
from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
)

from .tests_tax import PurchaseTaxTestCase


class GstBillTests(PurchaseTaxTestCase):
    """Input tax follows the same state line as output tax."""

    def setUp(self):
        super().setUp()

        def tax(code, rate, account_code):
            account = Account.objects.create(
                code=account_code, name=f"Input {code}", account_type=AccountType.ASSET
            )
            return Tax.objects.create(
                code=code, name=code, rate=Decimal(rate),
                collected_account=account, paid_account=account,
            )

        self.cgst = tax("CGST9", "9", "1311")
        self.sgst = tax("SGST9", "9", "1312")
        self.igst = tax("IGST18", "18", "1313")
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.cgst, target_tax=self.igst
        )
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=interstate, source_tax=self.sgst, target_tax=None
        )
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)

    def test_a_vendor_across_a_state_line_charges_integrated_tax(self):
        PartyTaxProfile.objects.create(party=self.vendor, gstin="29AABCE5678F1ZD")
        bill = self.taxed_bill("10", "100", taxes=[self.cgst, self.sgst])
        bill.post()

        self.assertEqual(self.balance(self.igst.paid_account), Decimal("180"))
        self.assertEqual(self.balance(self.cgst.paid_account), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-1180"))

    def test_a_vendor_in_the_state_charges_the_pair(self):
        PartyTaxProfile.objects.create(party=self.vendor, gstin="27AABCD1234E2Z7")
        bill = self.taxed_bill("10", "100", taxes=[self.cgst, self.sgst])
        bill.post()

        self.assertEqual(self.balance(self.cgst.paid_account), Decimal("90"))
        self.assertEqual(self.balance(self.sgst.paid_account), Decimal("90"))
        self.assertEqual(self.balance(self.igst.paid_account), Decimal("0"))
