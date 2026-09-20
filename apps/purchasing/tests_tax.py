"""
Tax on the way in.

Sales has run taxes through the customer's fiscal position since the
first audit pass. Purchasing charged none at all, which means input VAT
was never recorded and never reclaimed — the company simply paid it and
buried it in the expense.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalLine,
    PartyTaxProfile,
    Tax,
    TaxGroup,
    TaxScope,
)

from .models import Bill, BillLine, PurchaseOrderLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class PurchaseTaxTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        group = TaxGroup.objects.create(code="VAT", name="VAT")
        self.input_vat = Account.objects.create(
            code="1300", name="VAT Recoverable", account_type=AccountType.ASSET
        )
        self.output_vat = Account.objects.create(
            code="2100", name="VAT Payable", account_type=AccountType.LIABILITY
        )
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
            scope=TaxScope.BOTH,
            collected_account=self.output_vat, paid_account=self.input_vat,
        )

    def taxed_bill(self, quantity="10", price="5", taxes=None):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable,
        )
        line = BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal(quantity),
            unit_price=Decimal(price), expense_account=self.expense,
        )
        line.taxes.set(self.vat if taxes is None else taxes)
        return bill

    def balance(self, account):
        from django.db.models import Sum

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class BillTaxTests(PurchaseTaxTestCase):
    def test_tax_is_added_to_the_bill_total(self):
        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        self.assertEqual(bill.subtotal(), Decimal("50"))
        self.assertEqual(bill.tax_total(), Decimal("10"))
        self.assertEqual(bill.total(), Decimal("60"))

    def test_input_tax_is_an_asset_not_a_cost(self):
        """VAT paid to a vendor is reclaimable. Burying it in the expense
        overstates costs and loses the reclaim."""
        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        bill.post()

        self.assertEqual(self.balance(self.input_vat), Decimal("10"))
        self.assertEqual(self.balance(self.payable), Decimal("-60"))
        # Nothing was received against this bill, so the goods expense
        # rather than clearing an accrual that was never made.
        self.assertEqual(self.balance(self.expense), Decimal("50"))

    def test_the_entry_still_balances(self):
        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        bill.post()
        lines = bill.journal_entry.lines.all()
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )

    def test_a_sales_only_tax_is_refused_on_a_bill(self):
        sales_only = Tax.objects.create(
            code="ST", name="Sales only", rate=Decimal("5"),
            group=self.vat.group, scope=TaxScope.SALES,
            collected_account=self.output_vat,
        )
        bill = self.taxed_bill("10", "5", taxes=[sales_only])
        with self.assertRaisesMessage(ValidationError, "not configured for purchases"):
            bill.post()

    def test_a_debit_note_gives_the_tax_back(self):
        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        bill.post()
        bill.create_debit_note()

        self.assertEqual(self.balance(self.input_vat), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("0"))

    def test_a_discount_is_taxed_after_the_discount(self):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable,
        )
        line = BillLine.objects.create(
            bill=bill, item=self.item, quantity=Decimal("10"), unit_price=Decimal("5"),
            discount_percent=Decimal("10"), expense_account=self.expense,
        )
        line.taxes.set([self.vat])

        self.assertEqual(bill.subtotal(), Decimal("45"))
        self.assertEqual(bill.tax_total(), Decimal("9"))


class VendorFiscalPositionTests(PurchaseTaxTestCase):
    """The mirror of the Sales fix: configuration that nothing reads is
    configuration that quietly does nothing."""

    def zero_rate_the_vendor(self):
        position = FiscalPosition.objects.create(code="RC", name="Reverse charge")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=position, source_tax=self.vat, target_tax=None
        )
        PartyTaxProfile.objects.create(party=self.vendor, fiscal_position=position)

    def test_a_reverse_charge_vendor_is_not_charged_input_tax(self):
        self.zero_rate_the_vendor()
        bill = self.taxed_bill("10", "5", taxes=[self.vat])

        self.assertEqual(bill.tax_total(), Decimal("0"))
        self.assertEqual(bill.total(), Decimal("50"))

    def test_nothing_reaches_the_tax_account(self):
        self.zero_rate_the_vendor()
        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        bill.post()

        self.assertEqual(self.balance(self.input_vat), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-50"))

    def test_an_ordinary_vendor_is_unaffected(self):
        self.zero_rate_the_vendor()
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        other = Party.objects.create(
            code="V-2", name="Domestic", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.vendor = other

        bill = self.taxed_bill("10", "5", taxes=[self.vat])
        self.assertEqual(bill.tax_total(), Decimal("10"))


class PurchaseOrderTaxTests(PurchaseTaxTestCase):
    def test_an_order_totals_with_tax(self):
        order = self.make_order("10", "5", confirm=False)
        line = order.lines.first()
        line.taxes.set([self.vat])

        self.assertEqual(order.subtotal(), Decimal("50"))
        self.assertEqual(order.total(), Decimal("60"))

    def test_an_order_line_needs_an_explicit_price(self):
        """There is no purchase price list; the price is whatever the
        vendor quoted."""
        order = self.make_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "no price list to fall back on"):
            PurchaseOrderLine.objects.create(
                order=order, item=self.item, uom=self.uom,
                quantity=Decimal("1"), unit_price=None,
            )
