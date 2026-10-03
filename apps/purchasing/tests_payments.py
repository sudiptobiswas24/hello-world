"""
Vendor payments and AP aging.

Accounting has supported disbursements since the ledger was written, and
nothing on the purchase side ever allocated one. Money could leave the
bank but no bill was ever marked paid, so every posted bill stayed
outstanding forever and there was no way to tell what the company owed
or when.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Payment, PaymentDirection
from apps.core.models import Currency, ExchangeRate, Party, PartyRole, PartyRoleAssignment

from .models import (
    BillPayment,
    SettlementStatus,
    ap_aging,
    payment_run,
    vendor_balance,
)
from .tests_lifecycle import PurchasingLifecycleTestCase


class PaymentTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType

        self.bank = Account.objects.create(
            code="1010", name="Bank", account_type=AccountType.ASSET
        )

    def disbursement(self, amount, on=datetime.date(2026, 1, 20), currency=None):
        payment = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=on, amount=Decimal(amount), currency=currency or self.usd,
            bank_account=self.bank, counterpart_account=self.payable,
        )
        payment.post()
        return payment

    def posted_bill(self, quantity="10", price="5"):
        bill = self.make_bill(quantity, price)
        bill.post()
        return bill


class BillSettlementTests(PaymentTestCase):
    def test_paying_a_bill_clears_it(self):
        bill = self.posted_bill()
        self.assertEqual(bill.settlement_status(), SettlementStatus.UNPAID)

        BillPayment.objects.create(
            bill=bill, payment=self.disbursement("50"), amount=Decimal("50")
        )

        self.assertEqual(bill.amount_due(), Decimal("0"))
        self.assertEqual(bill.settlement_status(), SettlementStatus.PAID)

    def test_a_part_payment_leaves_the_rest(self):
        bill = self.posted_bill()
        BillPayment.objects.create(
            bill=bill, payment=self.disbursement("20"), amount=Decimal("20")
        )
        self.assertEqual(bill.amount_due(), Decimal("30"))
        self.assertEqual(bill.settlement_status(), SettlementStatus.PARTIAL)

    def test_a_debit_note_reduces_what_is_owed(self):
        bill = self.posted_bill()
        bill.create_debit_note()
        self.assertEqual(bill.amount_due(), Decimal("0"))

    def test_one_payment_can_settle_several_bills(self):
        first = self.posted_bill("4", "5")
        second = self.posted_bill("6", "5")
        payment = self.disbursement("50")

        BillPayment.objects.create(bill=first, payment=payment, amount=Decimal("20"))
        BillPayment.objects.create(bill=second, payment=payment, amount=Decimal("30"))

        self.assertEqual(BillPayment.unallocated_for(payment), Decimal("0"))
        self.assertEqual(vendor_balance(self.vendor), Decimal("0"))

    def test_over_allocating_the_payment_is_refused(self):
        first = self.posted_bill("4", "5")
        second = self.posted_bill("10", "5")
        payment = self.disbursement("30")
        BillPayment.objects.create(bill=first, payment=payment, amount=Decimal("20"))

        with self.assertRaisesMessage(ValidationError, "unallocated"):
            BillPayment.objects.create(bill=second, payment=payment, amount=Decimal("20"))

    def test_over_allocating_the_bill_is_refused(self):
        bill = self.posted_bill()
        with self.assertRaisesMessage(ValidationError, "only has 50"):
            BillPayment.objects.create(
                bill=bill, payment=self.disbursement("80"), amount=Decimal("80")
            )


class SettlementGuardTests(PaymentTestCase):
    def test_a_receipt_cannot_pay_a_bill(self):
        """Money coming in does not settle money owed out."""
        bill = self.posted_bill()
        receipt = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 1, 20), amount=Decimal("50"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable,
        )
        receipt.post()
        with self.assertRaisesMessage(ValidationError, "Only a disbursement"):
            BillPayment.objects.create(bill=bill, payment=receipt, amount=Decimal("50"))

    def test_a_debit_note_is_settled_by_the_vendor_paying_up(self):
        bill = self.posted_bill()
        note = bill.create_debit_note()
        with self.assertRaisesMessage(ValidationError, "refunded by the vendor with a receipt"):
            BillPayment.objects.create(
                bill=note, payment=self.disbursement("50"), amount=Decimal("50")
            )

    def test_another_party_s_payment_cannot_settle_this_bill(self):
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        bill = self.posted_bill()
        payment = Payment.objects.create(
            party=other, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 20), amount=Decimal("50"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable,
        )
        payment.post()
        with self.assertRaisesMessage(ValidationError, "different parties"):
            BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("50"))

    def test_a_draft_bill_cannot_be_settled(self):
        bill = self.make_bill()
        with self.assertRaisesMessage(ValidationError, "Only a posted bill"):
            BillPayment.objects.create(
                bill=bill, payment=self.disbursement("50"), amount=Decimal("50")
            )

    def test_an_unposted_payment_cannot_be_allocated(self):
        bill = self.posted_bill()
        payment = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 20), amount=Decimal("50"),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.payable,
        )
        with self.assertRaisesMessage(ValidationError, "Only a posted payment"):
            BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("50"))

    def test_cross_currency_settlement_is_refused_not_guessed(self):
        """Treating 100 USD as 100 EUR silently writes off the difference."""
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.2"), valid_from=datetime.date(2026, 1, 1)
        )
        bill = self.posted_bill()
        payment = self.disbursement("50", currency=eur)

        with self.assertRaisesMessage(ValidationError, "cross-currency settlement"):
            BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("50"))


class ApAgingTests(PaymentTestCase):
    def test_bills_bucket_by_how_overdue_they_are(self):
        self.posted_bill()  # due 2026-02-09 on net 30
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))
        self.assertEqual(aging["1-30"]["total"], Decimal("50"))
        self.assertEqual(aging["current"]["total"], Decimal("0"))

    def test_a_bill_not_yet_due_is_current(self):
        self.posted_bill()
        aging = ap_aging(as_of=datetime.date(2026, 1, 15))
        self.assertEqual(aging["current"]["total"], Decimal("50"))

    def test_a_paid_bill_leaves_the_report(self):
        bill = self.posted_bill()
        BillPayment.objects.create(
            bill=bill, payment=self.disbursement("50"), amount=Decimal("50")
        )
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))
        self.assertEqual(aging["1-30"]["total"], Decimal("0"))

    def test_the_vendor_balance_agrees_with_the_aging(self):
        self.posted_bill("4", "5")
        self.posted_bill("6", "5")
        aging = ap_aging(as_of=datetime.date(2026, 3, 1))
        total = sum(bucket["total"] for bucket in aging.values())
        self.assertEqual(total, vendor_balance(self.vendor))


class PaymentRunTests(PaymentTestCase):
    def test_it_lists_what_is_due_by_a_date(self):
        self.posted_bill("10", "5")
        rows = payment_run(due_by=datetime.date(2026, 3, 1))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vendor"], self.vendor)
        self.assertEqual(rows[0]["total"], Decimal("50"))
        self.assertEqual(rows[0]["bills"][0]["days_overdue"], 20)

    def test_a_bill_not_yet_due_is_left_out(self):
        self.posted_bill()
        self.assertEqual(payment_run(due_by=datetime.date(2026, 1, 15)), [])

    def test_a_settled_bill_is_left_out(self):
        bill = self.posted_bill()
        BillPayment.objects.create(
            bill=bill, payment=self.disbursement("50"), amount=Decimal("50")
        )
        self.assertEqual(payment_run(due_by=datetime.date(2026, 3, 1)), [])

    def test_bills_group_by_vendor(self):
        other = Party.objects.create(
            code="V-2", name="Other", default_currency=self.usd, payment_terms=self.terms
        )
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.posted_bill("10", "5")
        self.vendor = other
        self.posted_bill("20", "5")

        rows = payment_run(due_by=datetime.date(2026, 3, 1))

        self.assertEqual([row["total"] for row in rows], [Decimal("100"), Decimal("50")])

    def test_different_currencies_are_never_added_together(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.2"), valid_from=datetime.date(2026, 1, 1)
        )
        self.posted_bill("10", "5")
        self.vendor.default_currency = eur
        self.vendor.save()
        self.posted_bill("10", "5")

        rows = payment_run(due_by=datetime.date(2026, 3, 1))

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["currency"] for row in rows}, {self.usd, eur})

    def test_it_can_be_narrowed_to_one_vendor(self):
        self.posted_bill()
        rows = payment_run(due_by=datetime.date(2026, 3, 1), vendor=self.vendor)
        self.assertEqual(len(rows), 1)
