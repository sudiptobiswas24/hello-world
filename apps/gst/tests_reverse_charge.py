"""
Freight from a goods transport agency, under reverse charge: the agency
charges no GST, and the company owes it and claims it back.

  bill 10,000 for freight; reverse charge IGST 5% = 500
  vendor owed 10,000; input credit 500; reverse charge payable 500
  GSTR-3B: 3.1(d) 10,000 and IGST 500; 4(A)(3) IGST 500; 4(A)(5) untouched
  half of it debited back: 3.1(d) 5,000 and 250; 4(A)(3) 250
  from an unregistered agency the credit is still claimed: it rests on
  the company's payment, not the supplier's registration
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, Tax, TaxScope
from apps.core.models import PartyRole
from apps.purchasing.models import Bill, BillLine

from .returns import gstr3b
from .tests import DAY, GstReturnTestCase, gstin

SEPTEMBER = (DAY.replace(day=1), DAY.replace(day=30))


class ReverseChargeTests(GstReturnTestCase):
    def setUp(self):
        super().setUp()
        self.rcm_payable = Account.objects.create(code="2290", name="GST payable on reverse charge",
                                                  account_type=AccountType.LIABILITY)
        self.itc = Account.objects.create(code="1390", name="GST input on reverse charge",
                                          account_type=AccountType.ASSET)
        self.gta_igst = Tax.objects.create(
            code="IGST5RC", name="IGST 5% reverse charge", rate=D("5"), gst_head="igst", scope=TaxScope.PURCHASE,
            paid_account=self.itc, reverse_charge=True, reverse_charge_account=self.rcm_payable)
        self.agency = self.party("GTA", role=PartyRole.VENDOR, gstin=gstin("29AABCV4444D1Z"))

    def freight_bill(self, vendor=None, amount="10000"):
        bill = Bill.objects.create(vendor=vendor or self.agency, bill_date=DAY, payable_account=self.ap)
        line = BillLine.objects.create(bill=bill, charge=self.freight, quantity=D("1"), unit_price=D(amount),
                                       expense_account=self.expense)
        line.taxes.set([self.gta_igst])
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def test_owed_by_the_company_and_claimed_back(self):
        bill = self.freight_bill()
        self.assertEqual((bill.total(), bill.amount_due()), (D("10000.00"), D("10000.00")))
        self.assertEqual((self.ledger("2000"), self.ledger("2290"), self.ledger("1390")),
                         (D("10000.00"), D("500.00"), D("-500.00")))
        result = gstr3b(*SEPTEMBER)
        self.assertEqual(result["3.1d"], {"taxable": D("10000.00"), "igst": D("500.00"), "cgst": D("0"),
                                          "sgst": D("0"), "cess": D("0")})
        self.assertEqual(result["4A3"], {"igst": D("500.00"), "cgst": D("0"), "sgst": D("0"), "cess": D("0")})
        self.assertEqual(result["4A5"]["igst"], D("0"))
        self.assertNotIn("3.1(d) and 4(A)(3): reverse charge", result["not_built"])

    def test_half_debited_back(self):
        bill = self.freight_bill()
        note = bill.create_debit_note(quantities={bill.lines.get(): D("0.5")})
        Bill.objects.filter(pk=note.pk).update(bill_date=DAY)
        result = gstr3b(*SEPTEMBER)
        self.assertEqual((result["3.1d"]["taxable"], result["3.1d"]["igst"], result["4A3"]["igst"]),
                         (D("5000.00"), D("250.00"), D("250.00")))
        self.assertEqual((self.ledger("2290"), self.ledger("2000")), (D("250.00"), D("5000.00")))

    def test_from_an_unregistered_agency_the_credit_still_stands(self):
        backyard = self.party("TRUCKER", role=PartyRole.VENDOR, gst_state="27", gst_registration="unregistered")
        self.freight_bill(vendor=backyard)
        result = gstr3b(*SEPTEMBER)
        self.assertEqual((result["3.1d"]["igst"], result["4A3"]["igst"]), (D("500.00"), D("500.00")))
        self.assertEqual(result["warnings"], [])

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "scope is purchases"):
            Tax.objects.create(code="X1", name="x", rate=D("5"), gst_head="igst", scope=TaxScope.BOTH,
                               collected_account=self.rcm_payable, paid_account=self.itc, reverse_charge=True,
                               reverse_charge_account=self.rcm_payable)
        with self.assertRaisesMessage(ValidationError, "where the tax the company owes"):
            Tax.objects.create(code="X2", name="x", rate=D("5"), gst_head="igst", scope=TaxScope.PURCHASE,
                               paid_account=self.itc, reverse_charge=True)
        self.freight_bill()
        self.gta_igst.reverse_charge = False
        with self.assertRaisesMessage(ValidationError, "a new tax"):
            self.gta_igst.save()
