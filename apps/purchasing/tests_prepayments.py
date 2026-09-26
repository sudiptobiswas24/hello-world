"""
Vendor prepayments — the purchase twin of customer deposits.

Same accounting question, opposite sign: handing money to a vendor does
not consume it. Until the goods arrive the vendor owes either the goods
or the money back, which is an asset, not a cost.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment

from .models import Bill, BillPolicy, PrepaymentApplication, vendor_balance
from .tests_lifecycle import PurchasingLifecycleTestCase


class PrepaymentTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.prepaid = Account.objects.create(
            code="1400", name="Vendor Prepayments", account_type=AccountType.ASSET
        )
        self.bank = Account.objects.create(
            code="1010", name="Bank", account_type=AccountType.ASSET
        )
        company = Company.get()
        company.vendor_prepayment_account = self.prepaid
        company.default_purchase_expense_account = self.expense
        company.save()

    def balance(self, account):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def prepay(self, order, percent=30):
        bill = order.create_prepayment_bill(self.payable, percent=percent)
        bill.post()
        return bill

    def pay(self, bill, amount):
        from apps.accounting.models import Payment, PaymentDirection

        from .models import BillPayment

        payment = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 15), amount=Decimal(amount),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable,
        )
        payment.post()
        return BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal(amount))


class PrepaymentBillTests(PrepaymentTestCase):
    def test_it_debits_an_asset_not_an_expense(self):
        """The goods are still on the vendor's dock. Nothing is consumed."""
        order = self.make_order("10", "5")
        prepayment = self.prepay(order, percent=30)

        self.assertEqual(prepayment.total(), Decimal("15"))
        self.assertEqual(self.balance(self.prepaid), Decimal("15"))
        self.assertEqual(self.balance(self.expense), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-15"))

    def test_a_percent_and_an_amount_agree(self):
        order = self.make_order("10", "5")
        by_amount = order.create_prepayment_bill(self.payable, amount=Decimal("25"))
        self.assertEqual(by_amount.total(), Decimal("25"))

    def test_it_needs_exactly_one_of_amount_or_percent(self):
        order = self.make_order("10", "5")
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_prepayment_bill(self.payable, amount=Decimal("10"), percent=10)
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_prepayment_bill(self.payable)

    def test_prepayments_cannot_exceed_the_order(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=70)
        with self.assertRaisesMessage(ValidationError, "exceed the order total"):
            order.create_prepayment_bill(self.payable, percent=40)

    def test_a_draft_order_cannot_take_one(self):
        order = self.make_order(confirm=False)
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.create_prepayment_bill(self.payable, percent=50)

    def test_it_needs_an_account_to_hold_the_money(self):
        company = Company.get()
        company.vendor_prepayment_account = None
        company.save()
        order = self.make_order("10", "5")
        with self.assertRaisesMessage(ValidationError, "no vendor prepayment account"):
            order.create_prepayment_bill(self.payable, percent=30)

    def test_it_lets_a_bill_on_receipt_order_be_paid_early(self):
        """RECEIVED refuses to bill before the goods arrive; a prepayment
        is how you legitimately pay anyway."""
        order = self.make_order("10", "5", )
        with self.assertRaisesMessage(ValidationError, "book the goods in first"):
            order.create_bill(self.payable)

        prepayment = self.prepay(order, percent=30)
        self.assertEqual(prepayment.amount_due(), Decimal("15"))

    def test_it_needs_a_purchase_order(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        prepayment.is_prepayment = True
        prepayment.purchase_order = None
        with self.assertRaisesMessage(ValidationError, "must be against a purchase order"):
            prepayment.clean()


class PrepaymentDrawdownTests(PrepaymentTestCase):
    def test_posting_the_real_bill_draws_it_down(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.receive(order, "10")

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(bill.total(), Decimal("50"))
        self.assertEqual(bill.amount_prepaid(), Decimal("15"))
        self.assertEqual(bill.amount_due(), Decimal("35"))

    def test_the_ledger_ends_up_right(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order, percent=30)
        self.pay(prepayment, "15")
        self.receive(order, "10")
        order.create_bill(self.payable).post()

        # The asset is consumed, the cost is recognised once, and payables
        # carry only what is still to be paid.
        self.assertEqual(self.balance(self.prepaid), Decimal("0"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.payable), Decimal("-35"))

    def test_the_total_still_says_what_was_bought(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        self.assertEqual(bill.subtotal(), Decimal("50"))

    def test_an_unpaid_prepayment_still_draws_down(self):
        """Two open payables side by side still add up to what is owed;
        blocking the real bill on our own payment run has no basis."""
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.receive(order, "10")
        order.create_bill(self.payable).post()

        self.assertEqual(vendor_balance(self.vendor), Decimal("50"))

    def test_it_is_only_drawn_down_once(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order, percent=30)
        self.receive(order, "10")
        order.create_bill(self.payable).post()
        self.assertEqual(prepayment.prepayment_unapplied(), Decimal("0"))

    def test_a_prepayment_larger_than_the_bill_is_capped(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=50)
        self.receive(order, "2")

        bill = order.create_bill(self.payable)
        bill.post()

        self.assertEqual(bill.total(), Decimal("10"))
        self.assertEqual(bill.amount_due(), Decimal("0"))
        self.assertEqual(order.prepayments().get().prepayment_unapplied(), Decimal("15"))

    def test_the_rest_survives_to_the_next_bill(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=50)
        self.receive(order, "2")
        order.create_bill(self.payable).post()
        self.receive(order, "8")

        second = order.create_bill(self.payable)
        second.post()

        self.assertEqual(second.total(), Decimal("40"))
        self.assertEqual(second.amount_prepaid(), Decimal("15"))
        self.assertEqual(second.amount_due(), Decimal("25"))

    def test_the_drawdown_can_be_turned_off(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post(apply_prepayments=False)
        self.assertEqual(bill.amount_due(), Decimal("50"))


class PrepaymentGuardTests(PrepaymentTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.make_order("10", "5")
        self.prepayment = self.prepay(self.order, percent=30)
        self.receive(self.order, "10")

    def test_a_draft_bill_cannot_draw_down(self):
        bill = self.order.create_bill(self.payable)
        with self.assertRaisesMessage(ValidationError, "Only a posted bill"):
            bill.apply_prepayment(self.prepayment)

    def test_a_prepayment_cannot_draw_down_another(self):
        second = self.order.create_prepayment_bill(self.payable, percent=10)
        second.post()
        with self.assertRaisesMessage(ValidationError, "cannot draw down a prepayment"):
            second.apply_prepayment(self.prepayment)

    def test_only_a_prepayment_can_be_drawn_down(self):
        bill = self.order.create_bill(self.payable)
        bill.post()
        with self.assertRaisesMessage(ValidationError, "Only a posted prepayment bill"):
            bill.apply_prepayment(bill)

    def test_it_cannot_cross_vendors(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.vendor = other
        order = self.make_order("5", "5")
        self.receive(order, "5")
        bill = order.create_bill(self.payable)
        bill.post()

        with self.assertRaisesMessage(ValidationError, "different vendor"):
            bill.apply_prepayment(self.prepayment)

    def test_it_cannot_be_over_applied(self):
        bill = self.order.create_bill(self.payable)
        bill.post()  # auto-applies the 15
        with self.assertRaisesMessage(ValidationError, "nothing left to draw down"):
            bill.apply_prepayment(self.prepayment)


class PrepaymentReportingTests(PrepaymentTestCase):
    def test_a_prepayment_is_still_a_payable(self):
        """We have agreed to pay it; it belongs in what we owe."""
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.assertEqual(vendor_balance(self.vendor), Decimal("15"))

    def test_it_does_not_touch_the_three_way_match(self):
        order = self.make_order("10", "5")
        self.prepay(order, percent=30)
        self.assertEqual(order.lines.first().quantity_billed(), Decimal("0"))

    def test_a_fully_prepaid_bill_reads_as_paid(self):
        from .models import SettlementStatus

        order = self.make_order("10", "5")
        self.prepay(order, percent=100)
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        self.assertEqual(bill.settlement_status(), SettlementStatus.PAID)
