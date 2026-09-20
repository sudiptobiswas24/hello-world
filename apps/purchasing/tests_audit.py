"""
Purchasing audit pass.

The two shapes that keep turning up in this codebase, on the purchase
side this time: a guard that exists on one path and not its mirror, and
an account that is only self-clearing if nothing ever differs.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company

from .models import Bill, BillLine, BillPolicy, PurchaseOrderLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class AuditTestCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.ppv = Account.objects.create(
            code="5900", name="Purchase Price Variance", account_type=AccountType.EXPENSE
        )
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.default_purchase_expense_account = self.expense
        company.purchase_price_tolerance_percent = Decimal("10")
        company.save()

    def balance(self, account):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def hand_bill(self, order, quantity, price, reference=""):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            purchase_order=order, payable_account=self.payable,
            currency=self.usd, reference=reference,
        )
        BillLine.objects.create(
            bill=bill, order_line=order.lines.first(), item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal(price),
            expense_account=self.expense,
        )
        return bill


class OrderLineImmutabilityTests(AuditTestCase):
    """Sales guards these; the purchase side went without."""

    def test_the_quantity_cannot_drop_below_what_arrived(self):
        order = self.make_order("10", "5")
        self.receive(order, "8")
        line = order.lines.first()
        line.quantity = Decimal("2")

        with self.assertRaisesMessage(ValidationError, "cannot drop below"):
            line.save()

    def test_the_quantity_cannot_drop_below_what_was_billed(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        order.create_bill(self.payable).post()
        line = order.lines.first()
        line.quantity = Decimal("3")

        with self.assertRaisesMessage(ValidationError, "cannot drop below"):
            line.save()

    def test_the_price_cannot_move_after_billing(self):
        """The agreed price is what the three-way match checks against, so
        moving it retrospectively approves whatever was billed."""
        order = self.make_order("10", "5")
        self.receive(order, "10")
        order.create_bill(self.payable).post()
        line = order.lines.first()
        line.unit_price = Decimal("99")

        with self.assertRaisesMessage(ValidationError, "price can no longer change"):
            line.save()

    def test_the_price_can_still_move_before_billing(self):
        order = self.make_order("10", "5", confirm=False)
        line = order.lines.first()
        line.unit_price = Decimal("6")
        line.save()
        self.assertEqual(order.lines.first().unit_price, Decimal("6"))

    def test_a_received_line_cannot_be_removed(self):
        order = self.make_order("10", "5")
        self.receive(order, "4")
        with self.assertRaises(Exception):
            order.lines.first().delete()


class GrniClearingTests(AuditTestCase):
    """GRNI only self-clears if it is cleared at what it accrued."""

    def test_a_price_difference_leaves_no_residue(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")

        self.hand_bill(order, "10", "5.40").post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("4.00"))

    def test_billing_under_the_agreed_price_credits_variance(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")

        self.hand_bill(order, "10", "4.50").post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.ppv), Decimal("-5.00"))

    def test_no_variance_means_no_variance_line(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = self.hand_bill(order, "10", "5")
        bill.post()

        self.assertEqual(self.balance(self.ppv), Decimal("0"))
        self.assertFalse(bill.journal_entry.lines.filter(account=self.ppv).exists())

    def test_a_partial_bill_clears_only_its_share(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        self.hand_bill(order, "4", "5").post()

        # 10 received accrued 50; 4 billed clears 20.
        self.assertEqual(self.balance(self.grni), Decimal("-30"))

    def test_a_variance_with_no_account_is_refused_not_absorbed(self):
        company = Company.get()
        company.purchase_price_variance_account = None
        company.save()
        order = self.make_order("10", "5")
        self.receive(order, "10")

        with self.assertRaisesMessage(ValidationError, "no purchase price variance account"):
            self.hand_bill(order, "10", "5.40").post()

    def test_a_bill_with_no_receipt_does_not_touch_grni(self):
        """Stock is created by receiving it, never by being billed for it.
        Debiting GRNI here leaves a balance nothing will ever offset."""
        bill = self.make_bill("3", "5")
        bill.post()

        self.assertEqual(self.balance(self.grni), Decimal("0"))
        self.assertEqual(self.balance(self.expense), Decimal("15"))

    def test_a_service_line_still_expenses(self):
        from apps.inventory.models import Item

        service = Item.objects.create(
            sku="SVC", name="Consulting", uom=self.uom, track_inventory=False
        )
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, item=service, quantity=Decimal("1"),
            unit_price=Decimal("500"), expense_account=self.expense,
        )
        bill.post()

        self.assertEqual(self.balance(self.expense), Decimal("500"))
        self.assertEqual(self.balance(self.grni), Decimal("0"))


class DuplicateVendorInvoiceTests(AuditTestCase):
    """Paying the same invoice twice because it arrived by post and by
    email is the commonest way money leaves AP by accident."""

    def test_the_same_vendor_reference_cannot_be_used_twice(self):
        self.make_bill_with_reference("INV-77").post()
        with self.assertRaisesMessage(ValidationError, "already on file"):
            self.make_bill_with_reference("INV-77")

    def test_a_different_vendor_may_use_the_same_number(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        self.make_bill_with_reference("INV-77").post()
        other = Party.objects.create(code="V-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        self.vendor = other

        bill = self.make_bill_with_reference("INV-77")
        bill.post()
        self.assertTrue(bill.posted)

    def test_bills_with_no_reference_are_unconstrained(self):
        self.make_bill("1", "5").post()
        self.make_bill("1", "5").post()

    def test_a_debit_note_may_carry_its_bill_s_reference(self):
        bill = self.make_bill_with_reference("INV-77")
        bill.post()
        note = bill.create_debit_note()
        self.assertEqual(note.reference, "INV-77")

    def make_bill_with_reference(self, reference):
        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 10),
            reference=reference, payable_account=self.payable, currency=self.usd,
        )
        BillLine.objects.create(
            bill=bill, description="Service", quantity=Decimal("1"),
            unit_price=Decimal("100"), expense_account=self.expense,
        )
        return bill


class PartialDebitNoteTests(AuditTestCase):
    """Sales has had partial credit notes since its first pass. The
    purchase side could only ever reverse a bill in full, so a vendor who
    short-shipped one line of ten forced the whole bill to be cancelled."""

    def two_line_bill(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        BillLine.objects.create(
            bill=bill, description="Delivery", quantity=Decimal("1"),
            unit_price=Decimal("20"), expense_account=self.expense,
        )
        bill.post()
        return bill

    def test_part_of_a_line_can_be_given_back(self):
        bill = self.two_line_bill()
        goods = bill.lines.filter(item=self.item).get()

        note = bill.create_debit_note(quantities={goods: Decimal("4")})

        self.assertEqual(note.total(), Decimal("20"))
        self.assertEqual(bill.amount_due(), Decimal("50"))

    def test_the_same_goods_cannot_be_debited_twice(self):
        bill = self.two_line_bill()
        goods = bill.lines.filter(item=self.item).get()
        bill.create_debit_note(quantities={goods: Decimal("6")})

        self.assertEqual(goods.quantity_debitable(), Decimal("4"))
        with self.assertRaisesMessage(ValidationError, "left to debit"):
            bill.create_debit_note(quantities={goods: Decimal("6")})

    def test_a_partial_note_is_not_linked_as_a_reversal(self):
        bill = self.two_line_bill()
        goods = bill.lines.filter(item=self.item).get()
        note = bill.create_debit_note(quantities={goods: Decimal("4")})
        self.assertIsNone(note.journal_entry.reverses_id)

    def test_a_full_note_still_reads_as_a_reversal(self):
        bill = self.two_line_bill()
        note = bill.create_debit_note()
        self.assertEqual(note.journal_entry.reverses_id, bill.journal_entry_id)
        self.assertEqual(bill.amount_due(), Decimal("0"))

    def test_it_gives_the_money_back_to_where_it_came_from(self):
        """Recomputing the account would send it elsewhere: the original
        bill still counts as billed when the note posts, so the accrual
        looks used up."""
        bill = self.two_line_bill()
        goods = bill.lines.filter(item=self.item).get()
        self.assertEqual(goods.posted_account, self.grni)

        note = bill.create_debit_note(quantities={goods: Decimal("10")})

        self.assertEqual(note.journal_entry.lines.get(account=self.grni).credit,
                         Decimal("50"))
        self.assertEqual(self.balance(self.grni), Decimal("-50"))

    def test_debiting_releases_the_quantity_for_rebilling(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        goods = bill.lines.get()

        bill.create_debit_note(quantities={goods: Decimal("4")})

        self.assertEqual(order.lines.first().quantity_billed(), Decimal("6"))

    def test_debiting_nothing_is_refused(self):
        bill = self.two_line_bill()
        with self.assertRaisesMessage(ValidationError, "Nothing to debit"):
            bill.create_debit_note(quantities={})


class PaidThenDebitedTests(AuditTestCase):
    def paid_bill(self):
        from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

        from .models import BillPayment

        bank = Account.objects.create(
            code="1010", name="Bank", account_type=AccountType.ASSET
        )
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        payment = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 1, 20), amount=Decimal("50"),
            currency=self.usd, bank_account=bank, counterpart_account=self.payable,
        )
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("50"))
        return bill

    def test_a_paid_bill_does_not_go_negative_when_debited(self):
        """A bill reading minus fifty says the company owes a negative
        amount, which is not a thing."""
        bill = self.paid_bill()
        bill.create_debit_note()
        self.assertEqual(bill.amount_due(), Decimal("0"))

    def test_the_money_owed_back_lives_on_the_note(self):
        bill = self.paid_bill()
        note = bill.create_debit_note()
        self.assertEqual(note.refund_due(), Decimal("50"))
        self.assertEqual(note.amount_absorbed(), Decimal("0"))

    def test_an_unpaid_bill_absorbs_the_note_instead(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()

        note = bill.create_debit_note()

        self.assertEqual(note.amount_absorbed(), Decimal("50"))
        self.assertEqual(note.refund_due(), Decimal("0"))

    def test_the_vendor_balance_reads_negative_when_overpaid(self):
        from .models import vendor_balance

        bill = self.paid_bill()
        bill.create_debit_note()
        self.assertEqual(vendor_balance(self.vendor), Decimal("-50"))

    def test_the_refund_clears_when_the_vendor_pays_it_back(self):
        from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

        from .models import BillPayment, vendor_balance

        bill = self.paid_bill()
        note = bill.create_debit_note()
        bank = Account.objects.get(code="1010")
        refund = Payment.objects.create(
            party=self.vendor, direction=PaymentDirection.RECEIPT,
            payment_date=datetime.date(2026, 2, 1), amount=Decimal("50"),
            currency=self.usd, bank_account=bank, counterpart_account=self.payable,
        )
        refund.post()
        BillPayment.objects.create(bill=note, payment=refund, amount=Decimal("50"))

        self.assertEqual(note.refund_due(), Decimal("0"))
        self.assertEqual(vendor_balance(self.vendor), Decimal("0"))


class VendorSettlementDiscountTests(AuditTestCase):
    """The mirror of the Sales finding, inert for exactly as long."""

    def setUp(self):
        super().setUp()
        self.discount_received = Account.objects.create(
            code="4900", name="Discounts Received", account_type=AccountType.INCOME
        )
        company = Company.get()
        company.settlement_discount_received_account = self.discount_received
        company.save()

    def discounted_bill(self):
        self.terms.discount_percent = Decimal("2")
        self.terms.discount_days = 10
        self.terms.save()
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        return bill

    def test_the_bill_knows_its_discount_and_deadline(self):
        bill = self.discounted_bill()
        self.assertEqual(bill.settlement_discount(), Decimal("1.00"))
        self.assertEqual(bill.discount_due_date(), datetime.date(2026, 1, 20))

    def test_taking_it_reduces_what_is_owed(self):
        bill = self.discounted_bill()
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        self.assertEqual(bill.amount_due(), Decimal("49.00"))

    def test_it_posts_against_payables_and_income(self):
        bill = self.discounted_bill()
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))

        self.assertEqual(self.balance(self.discount_received), Decimal("-1.00"))
        self.assertEqual(self.balance(self.payable), Decimal("-49.00"))

    def test_it_expires(self):
        bill = self.discounted_bill()
        with self.assertRaisesMessage(ValidationError, "No settlement discount is available"):
            bill.take_settlement_discount(on_date=datetime.date(2026, 2, 1))

    def test_it_can_be_taken_late_on_purpose(self):
        bill = self.discounted_bill()
        bill.take_settlement_discount(on_date=datetime.date(2026, 2, 1), force=True)
        self.assertEqual(bill.settlement_discount_amount, Decimal("1.00"))

    def test_it_cannot_be_taken_twice(self):
        bill = self.discounted_bill()
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        with self.assertRaisesMessage(ValidationError, "No settlement discount is available"):
            bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))

    def test_terms_with_no_discount_offer_none(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        self.assertEqual(bill.settlement_discount(), Decimal("0"))


class BilledNotHeldTests(AuditTestCase):
    """Returning billed goods is legitimate; leaving it invisible is not."""

    def test_returning_billed_goods_is_surfaced(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        order.create_bill(self.payable).post()
        receipt.create_return()

        rows = billed_not_held(vendor=self.vendor)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("10"))
        self.assertEqual(rows[0]["value"], Decimal("50"))

    def test_a_debit_note_clears_it(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        receipt.create_return()
        bill.create_debit_note()

        self.assertEqual(billed_not_held(vendor=self.vendor), [])

    def test_an_ordinary_return_before_billing_shows_nothing(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        self.receive(order, "10").create_return()
        self.assertEqual(billed_not_held(vendor=self.vendor), [])

    def test_the_match_report_carries_it(self):
        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        receipt.create_return()

        self.assertEqual(bill.match_report()[0]["billed_not_held"], Decimal("10"))
