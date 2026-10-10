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
        with self.assertRaisesMessage(ValidationError, 'This line has been received or billed and can no longer be r'):
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
            code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True
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

    def test_not_taken_before_the_bill_nor_a_prepayment_applied(self):
        # O163: take_settlement_discount and apply_prepayment took any day.
        bill = self.discounted_bill()
        before = bill.bill_date - datetime.timedelta(days=1)
        with self.assertRaisesMessage(ValidationError, f"takes no settlement discount on {before}"):
            bill.take_settlement_discount(on_date=before)
        with self.assertRaisesMessage(ValidationError, f"is not applied to {bill.number} on {before}"):
            bill.apply_prepayment(bill, on_date=before)

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
        # Refused as taken, not as unavailable: the date was never the reason.
        with self.assertRaisesMessage(ValidationError, "taken already"):
            bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))

    def test_forcing_waives_the_deadline_not_the_once(self):
        # The once was asked only where force skips, so a forced second call took it again.
        bill = self.discounted_bill()
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        with self.assertRaisesMessage(ValidationError, "taken already"):
            bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15), force=True)
        self.assertEqual(self.balance(self.discount_received), Decimal("-1.00"))
        self.assertEqual(bill.amount_due(), Decimal("49.00"))

    def test_our_payment_returned_the_discount_goes_with_it(self):
        from apps.accounting.models import Payment, PaymentDirection

        from .models import Bill, BillPayment

        bill = self.discounted_bill()
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        paying = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                        payment_date=datetime.date(2026, 1, 15), amount=Decimal("49"),
                                        currency=self.usd, bank_account=bank, counterpart_account=self.payable)
        paying.post()
        BillPayment.objects.create(bill=bill, payment=paying, amount=Decimal("49"))
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        paying.void(memo="Recalled", on_date=datetime.date(2026, 1, 25))
        bill = Bill.objects.get(pk=bill.pk)
        self.assertEqual((bill.amount_due(), bill.settlement_discount_withdrawn), (Decimal("50.00"), Decimal("1.00")))
        self.assertEqual(bill.settlement_discount_withdrawal_entry.date, datetime.date(2026, 1, 25))
        self.assertEqual((self.balance(self.payable), self.balance(self.discount_received)), (Decimal("-50.00"), Decimal("0")))
        # Paid again less the discount, then debited in full: the 49 comes back, and the withdrawn
        # discount is not undone a second time.
        again = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                       payment_date=datetime.date(2026, 1, 26), amount=Decimal("49"),
                                       currency=self.usd, bank_account=bank, counterpart_account=self.payable)
        again.post()
        BillPayment.objects.create(bill=bill, payment=again, amount=Decimal("49"))
        note = Bill.objects.get(pk=bill.pk).create_debit_note(memo="Wrong film")
        self.assertEqual((note.reversed_discount, note.refund_due()), (Decimal("0"), Decimal("49.00")))
        self.assertEqual(self.balance(self.discount_received), Decimal("0"))

    def test_terms_with_no_discount_offer_none(self):
        order = self.make_order("10", "5")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        self.assertEqual(bill.settlement_discount(), Decimal("0"))


class BilledNotHeldTests(AuditTestCase):
    """
    A return now raises its own debit note, so the gap this report was
    written for only survives a *replacement* — goods sent back that the
    vendor is replacing rather than refunding. That is still exposure
    worth naming, and still invisible without it.
    """

    def test_returning_billed_goods_for_replacement_is_surfaced(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        order.create_bill(self.payable).post()
        receipt.create_return(debit_bills=False)

        rows = billed_not_held(vendor=self.vendor)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("10"))
        self.assertEqual(rows[0]["value"], Decimal("50"))

    def test_a_refund_return_clears_it_by_itself(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        order.create_bill(self.payable).post()

        returned = receipt.create_return()

        self.assertEqual(len(returned.debit_notes_created), 1)
        self.assertEqual(billed_not_held(vendor=self.vendor), [])

    def test_a_debit_note_raised_by_hand_also_clears_it(self):
        from .models import billed_not_held

        order = self.make_order("10", "5")
        receipt = self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        receipt.create_return(debit_bills=False)
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
        receipt.create_return(debit_bills=False)

        self.assertEqual(bill.match_report()[0]["billed_not_held"], Decimal("10"))


class DebitAfterDiscountTests(VendorSettlementDiscountTests):
    """
    A debit note on a bill paid with a discount: the vendor owes back what
    we paid, and the discount is undone, in proportion to what the note
    gives back. It used to claim the discount back as cash never paid.
    """

    def paid_with_discount(self):
        from apps.accounting.models import Payment, PaymentDirection

        from .models import BillPayment

        bank = Account.objects.create(code="1011", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        bill = self.discounted_bill()
        payment = Payment.objects.create(party=self.vendor, direction=PaymentDirection.DISBURSEMENT,
                                         amount=Decimal("49"), payment_date=datetime.date(2026, 1, 15),
                                         bank_account=bank, counterpart_account=self.payable, currency=self.usd)
        payment.post()
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("49"))
        bill.take_settlement_discount(on_date=datetime.date(2026, 1, 15))
        return bill

    def test_debited_in_full_the_vendor_owes_back_what_was_paid(self):
        bill = self.paid_with_discount()
        note = bill.create_debit_note(memo="All back")
        self.assertEqual((note.reversed_discount, note.refund_due()), (Decimal("1.00"), Decimal("49.00")))
        self.assertEqual((self.balance(self.payable), self.balance(self.discount_received)), (Decimal("49"), Decimal("0")))

    def test_debited_in_part_the_discount_comes_back_in_proportion(self):
        bill = self.paid_with_discount()
        note = bill.create_debit_note(memo="One back", quantities={bill.lines.get(): Decimal("1")})
        self.assertEqual((note.reversed_discount, note.refund_due()), (Decimal("0.10"), Decimal("4.90")))


# -- what has moved stays as it moved (shared rule A), and a line answers to its
# document (shared rule B). The 9 October purchasing audit's probes, kept as
# regression tests with their numbers.

JAN = lambda day: datetime.date(2026, 1, day)  # noqa: E731


class TradeRuleCase(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        from apps.core.models import Currency, ExchangeRate

        acc = lambda code, name, kind: Account.objects.create(code=code, name=name, account_type=kind)  # noqa: E731
        self.ppv = acc("5900", "Purchase price variance", AccountType.EXPENSE)
        self.fx_loss = acc("7100", "FX loss", AccountType.EXPENSE)
        self.fx_gain = acc("7000", "FX gain", AccountType.INCOME)
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.default_purchase_expense_account = self.expense
        company.fx_loss_account = self.fx_loss
        company.fx_gain_account = self.fx_gain
        company.purchase_price_tolerance_percent = Decimal("0")
        company.save()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=Decimal("90"), valid_from=JAN(1))

    def balance(self, account):
        from django.db.models import Sum

        from apps.accounting.models import JournalLine

        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (rows["d"] or Decimal("0")) - (rows["c"] or Decimal("0"))

    def order(self, quantity="10", price="5", discount="0", policy=BillPolicy.RECEIVED):
        from .models import PurchaseOrder

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN(1), bill_policy=policy)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal(quantity),
                                         unit_price=Decimal(price), discount_percent=Decimal(discount))
        order.confirm()
        return order

    def hand_bill(self, line, quantity, price, vendor=None, order=None, currency=None, item=None, reference=""):
        bill = Bill.objects.create(vendor=vendor or self.vendor, bill_date=JAN(10), purchase_order=order,
                                   payable_account=self.payable, currency=currency or self.usd, reference=reference)
        BillLine.objects.create(bill=bill, order_line=line, item=item or self.item, quantity=Decimal(quantity),
                                unit_price=Decimal(price), expense_account=self.expense)
        return bill

    def other_vendor(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        other = Party.objects.create(code="V-2", name="Another supplier", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.VENDOR)
        return other

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class AReceivedOrderStaysAsItMovedTests(TradeRuleCase):
    """O96: a received or billed order's item, discount, vendor and currency could all still change."""

    def test_a_received_lines_item_cannot_change(self):
        from apps.inventory.models import Item

        order = self.order()
        self.receive(order, "10")
        other = Item.objects.create(sku="WDG-2", name="Other widget", uom=self.uom)
        line = order.lines.get()
        line.item = other
        with self.assertRaisesMessage(ValidationError, "been received on GRN-"):
            line.save()
        self.assertEqual(PurchaseOrderLine.objects.get(pk=line.pk).item, self.item)

    def test_a_received_lines_item_cannot_change_through_the_api(self):
        from apps.inventory.models import Item

        order = self.order()
        self.receive(order, "10")
        other = Item.objects.create(sku="WDG-2", name="Other widget", uom=self.uom)
        line = order.lines.get()
        response = self.as_("Purchasing Clerk").patch(f"/api/purchasing/purchase-order-lines/{line.pk}/",
                                                      {"item": other.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("item", response.json())

    def test_a_billed_lines_discount_cannot_change(self):
        order = self.order("10", "100")
        self.receive(order, "10")
        order.create_bill(self.payable, bill_date=JAN(10)).post()
        line = order.lines.get()
        line.discount_percent = Decimal("10")
        with self.assertRaisesMessage(ValidationError, "been billed for 10"):
            line.save()
        self.assertEqual(PurchaseOrderLine.objects.get(pk=line.pk).net_amount(), Decimal("1000.00"))

    def test_a_received_lines_price_may_still_be_agreed_before_its_bill(self):
        order = self.order()
        self.receive(order, "10")
        line = order.lines.get()
        line.unit_price = Decimal("4.50")
        line.save()
        self.assertEqual(PurchaseOrderLine.objects.get(pk=line.pk).unit_price, Decimal("4.50"))

    def test_a_received_orders_vendor_cannot_change(self):
        order = self.order()
        self.receive(order, "10")
        order.vendor = self.other_vendor()
        # Frozen from confirmation (O138), which comes before anything is received.
        with self.assertRaisesMessage(ValidationError, "has been confirmed; its vendor can no longer change"):
            order.save()

    def test_a_received_orders_currency_cannot_change(self):
        order = self.order()
        self.receive(order, "10")
        order.currency = self.eur
        with self.assertRaisesMessage(ValidationError, "currency can no longer change"):
            order.save()


class AConfirmedPurchaseOrderKeepsItsVendorAndLinesTests(TradeRuleCase):
    """O138, the mirror: a confirmed order's vendor and currency, and the order a line is on."""

    def test_a_confirmed_orders_vendor_cannot_change(self):
        order = self.order()
        order.vendor = self.other_vendor()
        with self.assertRaisesMessage(ValidationError, "has been confirmed; its vendor can no longer change"):
            order.save()

    def test_a_confirmed_orders_currency_cannot_change(self):
        order = self.order()
        order.currency = self.eur
        with self.assertRaisesMessage(ValidationError, "its currency can no longer change"):
            order.save()

    def test_a_line_is_not_moved_between_confirmed_orders(self):
        first, second = self.order(), self.order()
        response = self.as_("Purchasing Clerk").patch(
            f"/api/purchasing/purchase-order-lines/{first.lines.get().pk}/", {"order": second.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("been confirmed on", response.json()["order"][0])
        self.assertEqual((first.lines.count(), second.lines.count()), (1, 1))


class ABillLineBillsOnlyItsOwnOrderLineTests(TradeRuleCase):
    """O90: a typed bill line was never checked against the order line it names."""

    def test_a_bill_for_one_vendor_cannot_bill_anothers_order(self):
        order = self.order()
        self.receive(order, "10")
        with self.assertRaisesMessage(ValidationError, "is for V-2"):
            self.hand_bill(order.lines.get(), "10", "5", vendor=self.other_vendor()).post()
        self.assertEqual((order.lines.get().quantity_billed(), self.balance(self.grni)), (Decimal("0"), Decimal("-50.00")))

    def test_a_bill_in_another_currency_cannot_bill_the_order(self):
        order = self.order()
        self.receive(order, "10")
        with self.assertRaisesMessage(ValidationError, "in EUR"):
            self.hand_bill(order.lines.get(), "10", "5", order=order, currency=self.eur).post()
        self.assertEqual((self.balance(self.payable), self.balance(self.fx_loss)), (Decimal("0"), Decimal("0")))

    def test_a_typed_bill_naming_no_order_cannot_bill_a_cancelled_one(self):
        order = self.order(policy=BillPolicy.ORDERED)
        order.cancel()
        bill = self.hand_bill(order.lines.get(), "10", "5")
        with self.assertRaisesMessage(ValidationError, "only a confirmed order is billed"):
            bill.post()
        self.assertEqual(self.balance(self.payable), Decimal("0"))

    def test_a_bill_line_names_the_item_its_order_line_ordered(self):
        from apps.inventory.models import Item

        order = self.order()
        self.receive(order, "10")
        other = Item.objects.create(sku="WDG-2", name="Other widget", uom=self.uom)
        with self.assertRaisesMessage(ValidationError, "this line bills WDG-2"):
            self.hand_bill(order.lines.get(), "10", "5", order=order, item=other).post()
        self.assertEqual(self.balance(self.grni), Decimal("-50.00"))

    def test_the_vendor_changed_after_the_line_is_asked_again_as_it_posts(self):
        order = self.order()
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "5")
        bill.vendor = self.other_vendor()
        bill.save()
        with self.assertRaisesMessage(ValidationError, "is for V-2"):
            Bill.objects.get(pk=bill.pk).post()


class ADebitLineDebitsOnlyItsNotesBillTests(TradeRuleCase):
    """O140, the mirror: the bill line a debit note's line names as the one it gives back."""

    def billed(self):
        order = self.order()
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=JAN(10))
        bill.post()
        return bill

    def test_a_bill_line_debits_only_its_own_bills_lines(self):
        bill = self.billed()
        other = Bill.objects.create(vendor=self.other_vendor(), bill_date=JAN(12), payable_account=self.payable,
                                    currency=self.usd)
        with self.assertRaisesMessage(ValidationError, "corrects no document"):
            BillLine.objects.create(bill=other, debits_line=bill.lines.get(), item=self.item,
                                    quantity=Decimal("10"), unit_price=Decimal("5"), expense_account=self.expense)
        self.assertEqual(bill.lines.get().quantity_debitable(), Decimal("10"))

    def test_a_note_gives_back_no_more_than_the_line_holds(self):
        bill = self.billed()
        line = bill.lines.get()

        def typed_note():
            note = Bill.objects.create(vendor=self.vendor, bill_date=JAN(12), payable_account=self.payable,
                                       currency=self.usd, debits=bill)
            BillLine.objects.create(bill=note, debits_line=line, order_line=line.order_line, item=self.item,
                                    quantity=Decimal("10"), unit_price=Decimal("5"), expense_account=self.expense)
            return note

        typed_note().post()
        with self.assertRaisesMessage(ValidationError, "Only 0 of"):
            typed_note().post()
        self.assertEqual((line.quantity_debitable(), self.balance(self.payable)), (Decimal("0"), Decimal("0.00")))


class ATypedBillDoesNotPayForGoodsOnOrderTests(TradeRuleCase):
    """O92: a typed bill naming no order line and the order's own bill both paid one receipt: payable -100."""

    def test_a_hand_bill_with_no_link_and_a_generated_bill_do_not_both_pay_one_receipt(self):
        order = self.order()
        self.receive(order, "10")
        with self.assertRaisesMessage(ValidationError, "Name the order line this bill pays"):
            self.hand_bill(None, "10", "5").post()
        order.create_bill(self.payable, bill_date=JAN(12)).post()
        self.assertEqual((self.balance(self.grni), self.balance(self.payable)), (Decimal("0"), Decimal("-50.00")))

    def test_another_vendors_goods_are_not_cleared_by_it(self):
        order = self.order()
        self.receive(order, "10")
        bill = self.hand_bill(None, "10", "5", vendor=self.other_vendor())
        bill.post()
        # Nothing of V-2's is on order: the line expenses, and V-1's accrual waits for V-1's bill.
        self.assertEqual((self.balance(self.grni), self.balance(self.expense)), (Decimal("-50.00"), Decimal("50.00")))


class TypedBillsOneAfterTheOtherTests(TradeRuleCase):
    """O107, one after the other: the second typed bill for the last 10 of a line is refused."""

    def test_two_typed_bills_naming_no_order_do_not_both_bill_the_last_of_a_line(self):
        order = self.order()
        self.receive(order, "10")
        line = order.lines.get()
        first, second = (self.hand_bill(line, "10", "5", reference=ref) for ref in ("A-1", "A-2"))
        first.post()
        with self.assertRaisesMessage(ValidationError, "10 already billed"):
            Bill.objects.get(pk=second.pk).post()
        self.assertEqual(self.balance(self.payable), Decimal("-50.00"))

    def test_two_lines_of_one_bill_on_one_order_line_count_together(self):
        order = self.order()
        self.receive(order, "10")
        bill = self.hand_bill(order.lines.get(), "10", "5")
        BillLine.objects.create(bill=bill, order_line=order.lines.get(), item=self.item, quantity=Decimal("10"),
                                unit_price=Decimal("5"), expense_account=self.expense)
        with self.assertRaisesMessage(ValidationError, "would exceed the ordered quantity"):
            bill.post()


class ALineStaysOnItsPostedDocumentTests(TradeRuleCase):
    """O82: a bill or receipt line asked only the document it joined."""

    def test_a_line_is_not_moved_off_a_posted_bill(self):
        order = self.order("20", "5")
        self.receive(order, "20")
        posted = self.hand_bill(order.lines.get(), "10", "5", order=order)
        posted.post()
        draft = self.hand_bill(order.lines.get(), "10", "5", order=order)
        line = posted.lines.get()
        line.bill = draft
        with self.assertRaisesMessage(ValidationError, "posted bill"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "posted bill"):
            BillLine.objects.get(pk=line.pk).delete()
        self.assertEqual((Bill.objects.get(pk=posted.pk).total(), self.balance(self.payable)),
                         (Decimal("50.00"), Decimal("-50.00")))

    def test_a_line_is_not_moved_off_a_posted_receipt(self):
        from .models import GoodsReceipt, GoodsReceiptLine

        order = self.order("20", "5")
        posted = self.receive(order, "10")
        draft = GoodsReceipt.objects.create(purchase_order=order, receipt_date=JAN(6))
        line = posted.lines.get()
        line.receipt = draft
        with self.assertRaisesMessage(ValidationError, "posted goods receipt"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "posted goods receipt"):
            GoodsReceiptLine.objects.get(pk=line.pk).delete()
        self.assertEqual(order.lines.get().quantity_received(), Decimal("10"))


class ADebitNoteDebitsABillNeverANoteTests(ADebitLineDebitsOnlyItsNotesBillTests.__mro__[1]):  # O170
    billed = ADebitLineDebitsOnlyItsNotesBillTests.billed

    def test_a_debit_note_against_a_debit_note_is_refused(self):
        from .models import Bill

        bill = self.billed()
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("5")})
        with self.assertRaisesMessage(ValidationError, "is itself a note"):
            Bill.objects.create(vendor=self.vendor, bill_date=JAN(12), payable_account=self.payable,
                                currency=self.usd, debits=note)
