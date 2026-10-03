"""
Charges from a vendor: freight, handling, surcharges.

The same ChargeType the sales side uses, read from the other direction.
One row serves both because it is one concept — a carrier charges the
company freight, and the company recharges freight to its customers.
Two models would mean two code lists to keep aligned and two places to
get the tax treatment wrong.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType, ChargeType, Tax, TaxGroup, TaxScope

from .models import (
    Bill,
    BillPolicy,
    FulfilmentStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrderLine,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class PurchaseChargeTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        from apps.core.models import Company

        self.freight_expense = Account.objects.create(
            code="5300", name="Inbound Freight", account_type=AccountType.EXPENSE
        )
        self.freight = ChargeType.objects.create(
            code="FRT", name="Inbound shipping", expense_account=self.freight_expense
        )
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()

    def balance(self, account):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class PurchaseChargeBasicsTests(PurchaseChargeTestCase):
    def test_a_charge_bills_to_its_own_expense_account(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(bill.total(), Decimal("80"))
        self.assertEqual(self.balance(self.freight_expense), Decimal("30"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))

    def test_a_charge_needs_no_item_or_uom(self):
        order = self.make_order("10", "5")
        line = order.add_charge(self.freight, Decimal("30"))
        self.assertIsNone(line.item_id)
        self.assertIsNone(line.uom_id)
        self.assertTrue(line.is_charge())

    def test_a_line_is_an_item_or_a_charge_never_both(self):
        order = self.make_order("10", "5")
        with self.assertRaises(Exception):
            PurchaseOrderLine.objects.create(
                order=order, item=self.item, charge=self.freight, uom=self.uom,
                quantity=Decimal("1"), unit_price=Decimal("30"),
            )

    def test_a_charge_needs_an_explicit_amount(self):
        order = self.make_order("10", "5")
        with self.assertRaisesMessage(ValidationError, "unit price"):
            PurchaseOrderLine.objects.create(
                order=order, charge=self.freight, quantity=Decimal("1"), unit_price=None
            )

    def test_the_charge_type_supplies_the_account(self):
        order = self.make_order("10", "5")
        line = PurchaseOrderLine.objects.create(
            order=order, charge=self.freight, quantity=Decimal("1"),
            unit_price=Decimal("30"),
        )
        self.assertEqual(line.expense_account, self.freight_expense)

    def test_a_charge_with_no_expense_account_is_refused(self):
        sales_only = ChargeType.objects.create(
            code="SO", name="Sales only",
            revenue_account=Account.objects.create(
                code="4100", name="Freight Recharged", account_type=AccountType.INCOME
            ),
        )
        order = self.make_order("10", "5")
        with self.assertRaisesMessage(ValidationError, "no expense account"):
            order.add_charge(sales_only, Decimal("30"))


class PurchaseChargeTaxTests(PurchaseChargeTestCase):
    def setUp(self):
        super().setUp()
        self.input_vat = Account.objects.create(
            code="1300", name="VAT In", account_type=AccountType.ASSET
        )
        self.vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            group=TaxGroup.objects.create(code="VAT", name="VAT"),
            scope=TaxScope.PURCHASE, paid_account=self.input_vat,
        )
        self.freight.taxes.set([self.vat])

    def test_the_charge_brings_its_taxes_with_it(self):
        order = self.make_order("10", "5")
        line = order.add_charge(self.freight, Decimal("30"))
        self.assertEqual(line.tax_total(), Decimal("6"))

    def test_the_tax_reaches_the_bill_and_the_ledger(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(bill.total(), Decimal("86"))
        self.assertEqual(self.balance(self.input_vat), Decimal("6"))


class PurchaseChargeFulfilmentTests(PurchaseChargeTestCase):
    def test_a_charge_cannot_be_received(self):
        order = self.make_order("10", "5")
        charge_line = order.add_charge(self.freight, Decimal("30"))
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 5)
        )
        with self.assertRaisesMessage(ValidationError, "nothing arrives for it"):
            GoodsReceiptLine.objects.create(
                receipt=receipt, order_line=charge_line,
                warehouse=self.warehouse, quantity_received=Decimal("1"),
            )

    def test_a_charge_does_not_hold_the_order_at_partly_received(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        self.assertEqual(order.receipt_status(), FulfilmentStatus.FULL)

    def test_a_charge_is_billable_without_waiting_for_a_receipt(self):
        """Nothing will ever arrive for it, so a bill-on-receipt policy
        would otherwise strand the freight on the order."""
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(bill.total(), Decimal("30"))

    def test_a_charge_only_bills_once(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        order.create_bill(self.payable).post()
        self.receive(order, "10")

        second = order.create_bill(self.payable)
        second.post()

        self.assertEqual(second.total(), Decimal("50"))

    def test_a_debit_note_gives_the_charge_back(self):
        order = self.make_order("10", "5")
        order.add_charge(self.freight, Decimal("30"))
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()

        bill.create_debit_note()

        self.assertEqual(self.balance(self.freight_expense), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("0"))


class SharedChargeTypeTests(PurchaseChargeTestCase):
    def test_one_charge_type_serves_both_directions(self):
        """A carrier charges us freight; we recharge freight to customers.
        Two models would be two code lists to keep aligned."""
        recharged = Account.objects.create(
            code="4100", name="Freight Recharged", account_type=AccountType.INCOME
        )
        self.freight.revenue_account = recharged
        self.freight.save()

        self.assertEqual(self.freight.account_for(is_sale=False), self.freight_expense)
        self.assertEqual(self.freight.account_for(is_sale=True), recharged)
