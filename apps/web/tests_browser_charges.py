"""
O495: freight added to a document from its add row, as the people who do it.

The add row took an item only, so a charge (freight, packing, handling) reached a document through the API alone.
It now chooses an item or a charge; the charge's line is described by its name, one of it, at the price typed, and
names no taxes, so the server gives it the charge's (O488) through the party's fiscal position.

Numbers (worked in /home/user/work/calc_g33_charges/cases.py): freight 1,000.00 under CGST 9% + SGST 9%, to a
customer or from a vendor in the plant's own state (27, Maharashtra): 90.00 + 90.00, total 1,180.00.
"""

import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.accounting.gst import GstSettings, gstin_check_character
from apps.accounting.models import (
    Account, AccountType, ChargeType, FiscalPosition, FiscalPositionTaxMapping, PartyTaxProfile, Tax,
)
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import SalesOrder

from .tests_browser import BrowserTestCase


class AChargeAddedFromTheAddRowTests(BrowserTestCase):
    def setUp(self):
        super().setUp()

        def tax(code, rate, account_code):
            account = Account.objects.create(code=account_code, name=f"GST {code}", account_type=AccountType.LIABILITY)
            return Tax.objects.create(code=code, name=code, rate=Decimal(rate), gst_head=code[:4].lower(),
                                      collected_account=account, paid_account=account)

        cgst, sgst, igst = tax("CGST9", "9", "2201"), tax("SGST9", "9", "2202"), tax("IGST18", "18", "2203")
        interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        FiscalPositionTaxMapping.objects.create(fiscal_position=interstate, source_tax=cgst, target_tax=igst)
        FiscalPositionTaxMapping.objects.create(fiscal_position=interstate, source_tax=sgst, target_tax=None)
        GstSettings.objects.create(gstin="27AABCD1234E1Z8", interstate_position=interstate)
        PartyTaxProfile.objects.create(party=self.customer, gstin="27AABCD1234E2Z7")  # in the state

        self.freight = ChargeType.objects.create(
            code="FRT", name="Freight", hsn_code="9965",
            revenue_account=Account.objects.create(code="4190", name="Freight recharged", account_type=AccountType.INCOME),
            expense_account=Account.objects.create(code="5190", name="Freight inward", account_type=AccountType.EXPENSE))
        self.freight.taxes.set([cgst, sgst])
        # Kept for buying only: no revenue account, so a sale is not offered it.
        ChargeType.objects.create(
            code="FRI", name="Freight inward only",
            expense_account=Account.objects.get(code="5190"))

        payable = Account.objects.create(code="2000", name="Payables", account_type=AccountType.LIABILITY)
        company = Company.get()
        company.default_payable_account = payable
        company.save()
        self.vendor = Party.objects.create(code="V-1", name="Granule House", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        PartyTaxProfile.objects.create(party=self.vendor, gstin="27AABCG1234H1Z" + gstin_check_character("27AABCG1234H1Z"))  # Thane, in the state

    def add_freight(self, page):
        page.get_by_role("button", name="Charge", exact=True).click()
        page.get_by_role("combobox", name="Charge to add").fill("Fre")
        expect(page.get_by_role("option", name=re.compile("FRT · Freight"))).to_have_count(1)
        page.get_by_role("option", name=re.compile("FRT · Freight")).click()
        expect(page.get_by_label("Description to add")).to_have_value("Freight")
        expect(page.get_by_label("Quantity to add")).to_have_value("1")
        page.get_by_label("Unit price to add").fill("1000.00")
        page.get_by_role("button", name="Add line").click()

    def test_a_rep_adds_freight_to_an_order_and_it_takes_cgst_and_sgst(self):
        rep = self.sign_in(self.person("Sales Rep"), "/app/sales/orders/new")
        rep.get_by_role("combobox", name="Customer").fill("Acm")
        rep.get_by_role("option", name=re.compile("Acme")).click()
        rep.get_by_role("button", name="Create", exact=True).click()
        rep.wait_for_url(re.compile(r"/sales/orders/\d+$"))
        # A charge kept for buying only is not offered on a sale.
        rep.get_by_role("button", name="Charge", exact=True).click()
        rep.get_by_role("combobox", name="Charge to add").fill("Fr")
        expect(rep.get_by_role("option", name=re.compile("FRT · Freight"))).to_have_count(1)
        expect(rep.get_by_role("option", name=re.compile("FRI"))).to_have_count(0)
        rep.get_by_role("button", name="Item", exact=True).click()
        self.add_freight(rep)
        expect(rep.locator(".lines tbody tr", has_text="Freight")).to_have_count(1)
        expect(rep.locator(".lines tbody tr", has_text="Freight")).to_contain_text("CGST9")
        expect(rep.locator("main")).to_contain_text("1,180.00")
        line = SalesOrder.objects.get().lines.get()
        self.assertEqual((line.charge, line.item, line.description, line.quantity, line.unit_price),
                         (self.freight, None, "Freight", Decimal("1"), Decimal("1000.00")))
        self.assertEqual([(tax.code, amount) for tax, amount in line.tax_amounts()],
                         [("CGST9", Decimal("90.00")), ("SGST9", Decimal("90.00"))])
        self.assertEqual(line.total(), Decimal("1180.00"))
        self.assertEqual(self.problems, [])

    def test_a_buyer_adds_freight_to_a_new_purchase_order(self):
        buyer = self.sign_in(self.person("Purchasing Clerk"), "/app/purchasing/orders/new")
        buyer.get_by_role("combobox", name="Vendor").fill("Granule")
        buyer.get_by_role("option", name=re.compile("Granule House")).click()
        self.add_freight(buyer)
        expect(buyer.locator(".lines tbody tr", has_text="Freight")).to_have_count(1)
        self.assertEqual(PurchaseOrder.objects.count(), 0)  # nothing written until it is created
        buyer.get_by_role("button", name="Create", exact=True).click()
        buyer.wait_for_url(re.compile(r"/purchasing/orders/\d+$"))
        expect(buyer.locator("main")).to_contain_text("1,180.00")
        line = PurchaseOrder.objects.get().lines.get()
        self.assertEqual((line.charge, line.item, line.description, line.unit_price, line.warehouse_id),
                         (self.freight, None, "Freight", Decimal("1000.00"), None))
        self.assertEqual(line.expense_account.code, "5190")
        self.assertEqual([(tax.code, amount) for tax, amount in line.tax_amounts()],
                         [("CGST9", Decimal("90.00")), ("SGST9", Decimal("90.00"))])
        self.assertEqual(line.total(), Decimal("1180.00"))
        self.assertEqual(self.problems, [])
