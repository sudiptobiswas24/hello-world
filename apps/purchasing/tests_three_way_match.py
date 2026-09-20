"""
Three-way match: order, receipt, bill.

Bills had no link to the purchase order line, so a vendor could bill the
same delivery three times and nothing in the system would notice. This
is the identical defect found in Sales, where one order for 1,000 was
invoiced for 3,000 because invoice lines had no link back to the order
line they billed.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Company

from .models import Bill, BillLine, BillPolicy, FulfilmentStatus, PurchaseOrder
from .tests_lifecycle import PurchasingLifecycleTestCase


class MatchTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        company = Company.get()
        company.default_purchase_expense_account = self.expense
        company.save()

    def bill_from(self, order, date=datetime.date(2026, 1, 10)):
        bill = order.create_bill(self.payable, bill_date=date)
        bill.post()
        return bill


class DrawdownTests(MatchTestCase):
    def test_billing_twice_bills_the_remainder_not_the_order_again(self):
        order = self.make_order("10", "5")
        order.bill_policy = BillPolicy.ORDERED
        order.save()

        first = self.bill_from(order)
        self.assertEqual(first.total(), Decimal("50"))
        with self.assertRaisesMessage(ValidationError, "already fully billed"):
            order.create_bill(self.payable)

    def test_a_partial_receipt_bills_only_what_arrived(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")

        bill = self.bill_from(order)

        self.assertEqual(bill.total(), Decimal("20"))
        self.assertEqual(order.lines.first().quantity_billed(), Decimal("4"))
        self.assertEqual(order.bill_status(), FulfilmentStatus.PARTIAL)

    def test_the_rest_becomes_billable_once_it_arrives(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        self.bill_from(order)
        self.receive(order, "6")

        second = self.bill_from(order)

        self.assertEqual(second.total(), Decimal("30"))
        self.assertEqual(order.bill_status(), FulfilmentStatus.FULL)

    def test_nothing_received_means_nothing_to_bill(self):
        order = self.make_order("10", "5")
        with self.assertRaisesMessage(ValidationError, "book the goods in first"):
            order.create_bill(self.payable)

    def test_an_ordered_policy_bills_without_waiting(self):
        order = self.make_order("10", "5")
        order.bill_policy = BillPolicy.ORDERED
        order.save()
        self.assertEqual(self.bill_from(order).total(), Decimal("50"))

    def test_a_draft_order_cannot_be_billed(self):
        order = self.make_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.create_bill(self.payable)

    def test_a_debit_note_releases_the_quantity_again(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = self.bill_from(order)
        self.assertEqual(order.lines.first().quantity_billed(), Decimal("10"))

        bill.create_debit_note()

        self.assertEqual(order.lines.first().quantity_billed(), Decimal("0"))
        self.assertEqual(order.bill_status(), FulfilmentStatus.NONE)


class OverBillingTests(MatchTestCase):
    """The guard that catches a hand-entered bill, not just a generated one."""

    def hand_bill(self, order, quantity, price="5"):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            purchase_order=order, payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, order_line=order.lines.first(), item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            expense_account=self.expense,
        )
        return bill

    def test_billing_more_than_ordered_is_refused(self):
        order = self.make_order("10", "5")
        order.bill_policy = BillPolicy.ORDERED
        order.save()
        with self.assertRaisesMessage(ValidationError, "exceed the ordered quantity"):
            self.hand_bill(order, "12").post()

    def test_billing_the_same_delivery_twice_is_refused(self):
        """The Sales defect, on the purchase side."""
        order = self.make_order("10", "5")
        self.receive(order, "10")
        self.hand_bill(order, "10").post()

        with self.assertRaisesMessage(ValidationError, "exceed the ordered quantity"):
            self.hand_bill(order, "10").post()

    def test_billing_more_than_received_is_refused(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        with self.assertRaisesMessage(ValidationError, "billed on receipt"):
            self.hand_bill(order, "10").post()

    def test_a_bill_with_no_order_is_unaffected(self):
        """Expenses arrive without a purchase order all the time."""
        bill = self.make_bill("3", "9")
        bill.post()
        self.assertEqual(bill.total(), Decimal("27"))


class PriceVarianceTests(MatchTestCase):
    def hand_bill(self, order, price, quantity="10"):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            purchase_order=order, payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, order_line=order.lines.first(), item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            expense_account=self.expense,
        )
        return bill

    def ready_order(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        return order

    def test_billing_above_the_agreed_price_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "beyond the 0% tolerance"):
            self.hand_bill(self.ready_order(), "6").post()

    def test_billing_at_the_agreed_price_passes(self):
        bill = self.hand_bill(self.ready_order(), "5")
        bill.post()
        self.assertEqual(bill.total(), Decimal("50"))

    def test_billing_below_the_agreed_price_is_always_fine(self):
        """A vendor charging less than agreed is not a control failure."""
        bill = self.hand_bill(self.ready_order(), "4")
        bill.post()
        self.assertEqual(bill.total(), Decimal("40"))

    def test_a_tolerance_lets_small_overruns_through(self):
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("5")
        company.save()

        bill = self.hand_bill(self.ready_order(), "5.25")
        bill.post()
        self.assertEqual(bill.total(), Decimal("52.50"))

    def test_the_tolerance_still_has_an_edge(self):
        company = Company.get()
        company.purchase_price_tolerance_percent = Decimal("5")
        company.save()

        with self.assertRaisesMessage(ValidationError, "beyond the 5.00% tolerance"):
            self.hand_bill(self.ready_order(), "5.50").post()


class MatchReportTests(MatchTestCase):
    def test_it_lines_up_all_three_documents(self):
        order = self.make_order("10", "5")
        self.receive(order, "6")
        bill = order.create_bill(self.payable)

        row = bill.match_report()[0]

        self.assertTrue(row["matched"])
        self.assertEqual(row["quantity_ordered"], Decimal("10"))
        self.assertEqual(row["quantity_received"], Decimal("6"))
        self.assertEqual(row["quantity_billed"], Decimal("6"))
        self.assertEqual(row["price_variance"], Decimal("0"))

    def test_an_unmatched_line_says_so(self):
        bill = self.make_bill("3", "9")
        row = bill.match_report()[0]
        self.assertFalse(row["matched"])
        self.assertIsNone(row["quantity_ordered"])


class BillCarryOverTests(MatchTestCase):
    def test_the_bill_inherits_price_discount_and_taxes(self):
        from apps.accounting.models import Account, AccountType, Tax, TaxGroup, TaxScope

        vat = Tax.objects.create(
            code="VAT20", name="VAT 20%", rate=Decimal("20"),
            group=TaxGroup.objects.create(code="VAT", name="VAT"), scope=TaxScope.PURCHASE,
            paid_account=Account.objects.create(
                code="1300", name="VAT In", account_type=AccountType.ASSET
            ),
        )
        order = self.make_order("10", "5", confirm=False)
        line = order.lines.first()
        line.discount_percent = Decimal("10")
        line.save()
        line.taxes.set([vat])
        order.confirm()
        self.receive(order, "10")

        bill = order.create_bill(self.payable)

        self.assertEqual(bill.subtotal(), Decimal("45"))
        self.assertEqual(bill.tax_total(), Decimal("9"))
        self.assertEqual(bill.currency, order.currency)
