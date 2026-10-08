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
            code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True
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


class APrepaymentIsReturnedOnlyOnceTests(PrepaymentTestCase):
    """
    The mirror of sales' deposits, with the same holes copied across: a
    prepayment the vendor had debited back stayed available and the
    bill drew it down again, so the vendor was short-paid by it; and a
    prepayment already drawn down could be debited in full.
    """

    def bill_received(self, order):
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        return bill

    def test_a_debited_prepayment_is_not_drawn_down_again(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        note = prepayment.create_debit_note()
        self.receive(order, "10")
        bill = self.bill_received(order)

        self.assertEqual(
            (note.total(), prepayment.prepayment_unapplied(), bill.amount_prepaid(),
             bill.amount_due(), self.balance(self.prepaid), self.balance(self.payable)),
            (Decimal("15.00"), Decimal("0.00"), Decimal("0"), Decimal("50.00"),
             Decimal("0"), Decimal("-50.00")),
        )

    def test_debiting_a_part_used_prepayment_returns_only_what_is_left(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        self.pay(prepayment, "15")
        self.receive(order, "2")
        bill = self.bill_received(order)
        note = prepayment.create_debit_note()

        self.assertEqual(
            (bill.amount_prepaid(), note.total(), note.refund_due(), self.balance(self.prepaid)),
            (Decimal("10.00"), Decimal("5.00"), Decimal("5.00"), Decimal("0")),
        )

    def test_a_prepayment_drawn_down_in_full_cannot_be_debited(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        self.receive(order, "10")
        self.bill_received(order)
        with self.assertRaisesMessage(ValidationError, "already been drawn down"):
            prepayment.create_debit_note()
        self.assertEqual(self.balance(self.prepaid), Decimal("0"))

    def test_a_prepayment_is_debited_by_amount_not_quantity(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        with self.assertRaisesMessage(ValidationError, "whatever is left"):
            prepayment.create_debit_note(quantities={prepayment.lines.get(): Decimal("0.5")})

    def test_a_debited_prepayment_makes_room_for_another(self):
        order = self.make_order("10", "5")
        prepayment = order.create_prepayment_bill(self.payable, amount=Decimal("50"))
        prepayment.post()
        prepayment.create_debit_note()
        again = order.create_prepayment_bill(self.payable, amount=Decimal("20"))
        self.assertEqual(again.total(), Decimal("20.00"))


class AForeignPrepaymentClearsTests(PrepaymentTestCase):
    """
    15 EUR prepaid at 80, drawn down against a bill at 83: the prepayment
    account was credited at the bill's rate and left 45 in credit. At
    the rate it was paid at it clears, and the 45 is a realised gain —
    the company had paid for that part of the goods when euros were
    cheaper.
    """

    def test_the_prepayment_account_clears_and_the_difference_is_exchange(self):
        from apps.core.models import Currency, ExchangeRate

        from .models import PurchaseOrder, PurchaseOrderLine

        gain = Account.objects.create(code="7000", name="FX gain",
                                      account_type=AccountType.INCOME)
        loss = Account.objects.create(code="7100", name="FX loss",
                                      account_type=AccountType.EXPENSE)
        company = Company.get()
        company.fx_gain_account, company.fx_loss_account = gain, loss
        company.save()
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("80"),
                                    valid_from=datetime.date(2025, 1, 1))
        order = PurchaseOrder.objects.create(vendor=self.vendor, currency=eur,
                                             order_date=datetime.date(2026, 1, 1))
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                         quantity=Decimal("10"), unit_price=Decimal("5"))
        order.confirm()
        prepayment = order.create_prepayment_bill(
            self.payable, percent=30, bill_date=datetime.date(2026, 1, 2))
        prepayment.post()
        ExchangeRate.objects.create(currency=eur, rate=Decimal("83"),
                                    valid_from=datetime.date(2026, 1, 4))
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()

        self.assertEqual(
            (bill.amount_prepaid(), bill.amount_due(), self.balance(self.prepaid),
             self.balance(self.payable), self.balance(gain), self.balance(loss)),
            (Decimal("15.00"), Decimal("35.00"), Decimal("0"), Decimal("-4105.00"),
             Decimal("-45.00"), Decimal("0")),
        )


class PrepaymentFxFixture(PrepaymentTestCase):
    def foreign_order(self, price="5", quantity="10", held="80", billed="83"):
        from apps.core.models import Currency, ExchangeRate

        from .models import PurchaseOrder, PurchaseOrderLine

        self.gain = Account.objects.create(code="7000", name="FX gain",
                                           account_type=AccountType.INCOME)
        self.loss = Account.objects.create(code="7100", name="FX loss",
                                           account_type=AccountType.EXPENSE)
        company = Company.get()
        company.fx_gain_account, company.fx_loss_account = self.gain, self.loss
        company.save()
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal(held),
                                    valid_from=datetime.date(2025, 1, 1))
        ExchangeRate.objects.create(currency=eur, rate=Decimal(billed),
                                    valid_from=datetime.date(2026, 1, 4))
        order = PurchaseOrder.objects.create(vendor=self.vendor, currency=eur,
                                             order_date=datetime.date(2026, 1, 1))
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                         quantity=Decimal(quantity), unit_price=Decimal(price))
        order.confirm()
        return order


class ThePrepaymentKeepsItsExchangeEntryTests(PrepaymentFxFixture):
    def test_the_drawdown_records_the_gain_it_posted(self):
        order = self.foreign_order()
        order.create_prepayment_bill(self.payable, percent=30,
                                     bill_date=datetime.date(2026, 1, 2)).post()
        self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        application = bill.prepayment_applications.get()
        self.assertEqual(
            sorted((line.account.code, line.debit, line.credit)
                   for line in application.fx_entry.lines.all()),
            [("2000", Decimal("45.00"), Decimal("0.00")),
             ("7000", Decimal("0.00"), Decimal("45.00"))],
        )


class NoPaisaLeftOnThePayableTests(PrepaymentFxFixture):
    """The mirror of sales: 100.01 EUR at 80.111111, billed at 83.333333."""

    def test_a_fully_drawn_bill_leaves_the_payable_at_the_prepayment(self):
        order = self.foreign_order(price="100.01", quantity="1",
                                   held="80.111111", billed="83.333333")
        order.create_prepayment_bill(self.payable, amount=Decimal("100.01"),
                                     bill_date=datetime.date(2026, 1, 2)).post()
        self.receive(order, "1")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        self.assertEqual(
            (bill.amount_due(), self.balance(self.payable), self.balance(self.gain),
             self.balance(self.prepaid)),
            (Decimal("0.00"), Decimal("-8011.91"), Decimal("-322.26"), Decimal("0")),
        )


class APrepaymentIsOneLineOfMoneyHeldTests(PrepaymentTestCase):
    def draft(self):
        return self.make_order("10", "5").create_prepayment_bill(self.payable, percent=30)

    def test_a_second_line_is_refused(self):
        from .models import BillLine

        prepayment = self.draft()
        BillLine.objects.create(bill=prepayment, description="Bank charge",
                                quantity=Decimal("1"), unit_price=Decimal("5"),
                                expense_account=self.expense)
        with self.assertRaisesMessage(ValidationError, "single line"):
            prepayment.post()

    def test_tax_is_refused(self):
        from apps.accounting.models import Tax

        payable = Account.objects.create(code="2300", name="Tax",
                                          account_type=AccountType.LIABILITY)
        tax = Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"),
                                 collected_account=payable, paid_account=payable)
        prepayment = self.draft()
        prepayment.lines.get().taxes.add(tax)
        with self.assertRaisesMessage(ValidationError, "Tax on a prepayment"):
            prepayment.post()

    def test_another_account_is_refused(self):
        prepayment = self.draft()
        line = prepayment.lines.get()
        line.expense_account = self.expense
        line.save()
        with self.assertRaisesMessage(ValidationError, "vendor prepayment account"):
            prepayment.post()


class OnlyACompleteReturnReversesThePrepaymentTests(PrepaymentTestCase):
    def test_a_prepayment_debited_untouched_reverses_it(self):
        prepayment = self.prepay(self.make_order("10", "5"))
        note = prepayment.create_debit_note()
        self.assertEqual(note.journal_entry.reverses, prepayment.journal_entry)

    def test_what_is_left_of_a_part_used_one_does_not(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        self.receive(order, "2")
        order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10)).post()
        note = prepayment.create_debit_note()
        self.assertIsNone(note.journal_entry.reverses)


class ADraftDebitNoteGivesNothingBackTests(PrepaymentTestCase):
    """Only a posted note has done anything; a draft is a proposal."""

    def test_a_draft_note_leaves_the_prepayment_held(self):
        from .models import BillLine

        prepayment = self.prepay(self.make_order("10", "5"))
        draft = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 20),
                                    payable_account=self.payable, debits=prepayment)
        BillLine.objects.create(bill=draft, debits_line=prepayment.lines.get(),
                                quantity=Decimal("1"), unit_price=Decimal("15"),
                                expense_account=self.prepaid)
        self.assertEqual((prepayment.amount_debited(), prepayment.prepayment_unapplied()),
                         (Decimal("0"), Decimal("15.00")))


class PrepaymentsStayWithinTheOrderTests(PrepaymentTestCase):
    def test_the_second_draft_is_refused_when_it_posts(self):
        order = self.make_order("10", "5")
        first = order.create_prepayment_bill(self.payable, amount=Decimal("35"))
        second = order.create_prepayment_bill(self.payable, amount=Decimal("20"))
        first.post()
        with self.assertRaisesMessage(ValidationError, "exceed the order total"):
            second.post()
        self.assertEqual(self.balance(self.prepaid), Decimal("35.00"))


class PartOfAPrepaymentIsTakenBackTests(PrepaymentTestCase):
    """The mirror of sales' PartOfADepositIsGivenBackTests."""

    def test_part_is_taken_back_and_more_than_is_left_is_refused(self):
        prepayment = self.prepay(self.make_order("10", "100"))  # 30% of 1,000.00
        note = prepayment.create_debit_note(memo="Order cut", amount=Decimal("90"))
        self.assertEqual((note.total(), prepayment.prepayment_unapplied()),
                         (Decimal("90.00"), Decimal("210.00")))
        self.assertIsNone(note.journal_entry.reverses)
        with self.assertRaisesMessage(ValidationError, "Only 210.00 of"):
            prepayment.create_debit_note(amount=Decimal("210.01"))
        with self.assertRaisesMessage(ValidationError, "above nothing"):
            prepayment.create_debit_note(amount=Decimal("0"))
        prepayment.create_debit_note()
        self.assertEqual(prepayment.prepayment_unapplied(), Decimal("0.00"))

    def test_a_bill_is_not_debited_by_amount(self):
        order = self.make_order(quantity="10")
        self.receive(order, "10")
        bill = order.create_bill(self.payable)
        bill.post()
        with self.assertRaisesMessage(ValidationError, "not by an amount"):
            bill.create_debit_note(amount=Decimal("10"))


class ACancelledOrderHoldsNoPrepaymentTests(PrepaymentTestCase):
    """
    Sales' cancelled deposit, mirrored: an order holding a prepayment was cancelled with
    nothing left to draw it down and the vendor holding the money, and a prepayment drafted
    before the cancel posted after it. Order 10 x 5; prepayment 30%, 15.00.
    """

    def test_a_prepayment_held_is_debited_back_before_the_order_is_cancelled(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        with self.assertRaisesMessage(ValidationError, f"Prepayment {prepayment.number} still holds 15.00"):
            order.cancel()
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")

        prepayment.create_debit_note(memo="Order called off")
        order.cancel()
        self.assertEqual((self.balance(self.prepaid), vendor_balance(self.vendor)), (Decimal("0"), Decimal("0.00")))

    def test_a_draft_drawn_up_before_the_cancel_does_not_post_after_it(self):
        order = self.make_order("10", "5")
        prepayment = order.create_prepayment_bill(self.payable, percent=30)
        order.cancel()
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order can take a prepayment"):
            prepayment.post()
        prepayment.refresh_from_db()
        self.assertEqual((prepayment.posted, self.balance(self.prepaid)), (False, Decimal("0")))


    def test_through_the_api_the_buyer_is_told_why_and_the_debit_note_clears_the_way(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap_manager")
        user.groups.add(Group.objects.get(name="AP Manager"))
        ap = APIClient()
        ap.force_authenticate(user)
        order = self.make_order("10", "5")
        prepayment = self.prepay(order)
        cancel = f"/api/purchasing/purchase-orders/{order.pk}/cancel/"
        refused = ap.post(cancel, {}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn(f"Prepayment {prepayment.number} still holds 15.00", str(refused.json()))
        noted = ap.post(f"/api/purchasing/bills/{prepayment.pk}/debit_note/", {"memo": "Order called off"}, format="json")
        self.assertEqual((noted.status_code, noted.json()["total"]), (200, "15.00"), noted.content)
        cancelled = ap.post(cancel, {}, format="json")
        self.assertEqual((cancelled.status_code, cancelled.json()["status"]), (200, "cancelled"), cancelled.content)


class AnOrderComesToNoLessThanItsPrepaymentsTests(PrepaymentTestCase):
    """
    Sales' deposits, mirrored: 10 x 5 with 40 paid up front, cut to 5, came to 25 and left 15
    with the vendor for no bill to draw down; closed short after 5 came, the same.
    """

    def test_a_line_cut_under_its_prepayment_is_left_as_it_was_until_the_difference_is_debited(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order, percent=80)
        line = order.lines.get()
        line.quantity = Decimal("5")
        with self.assertRaisesMessage(
                ValidationError, "would come to 25.00: less than the 40.00 paid on it up front. Raise a debit note for 15.00"):
            line.save()
        line.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("10"))

        prepayment.create_debit_note(memo="Order cut to five", amount=Decimal("15"))
        line.quantity = Decimal("5")
        line.save()
        self.assertEqual((order.total(), order.prepayment_total(), self.balance(self.prepaid)),
                         (Decimal("25.00"), Decimal("25.00"), Decimal("25.00")))

    def test_closed_short_under_its_prepayment_once_the_rest_is_debited_the_account_clears(self):
        order = self.make_order("10", "5")
        prepayment = self.prepay(order, percent=80)
        self.receive(order, "5")
        order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10)).post()
        line = order.lines.get()
        with self.assertRaisesMessage(
                ValidationError, "would come to 25.00, once what was closed short is taken off: less than the 40.00"):
            line.close_short("Vendor out of stock")
        line.refresh_from_db()
        self.assertFalse(line.is_closed_short())

        prepayment.create_debit_note(memo="Rest called off")
        line.close_short("Vendor out of stock")
        self.assertEqual(self.balance(self.prepaid), Decimal("0"))

    def test_a_line_that_may_not_go_leaves_the_approval_where_it_was(self):
        from django.utils import timezone

        from .models import PurchaseOrder

        order = self.make_order("10", "5")
        self.receive(order, "4")
        PurchaseOrder.objects.filter(pk=order.pk).update(approved_at=timezone.now())
        order.refresh_from_db()  # the line reads its order from here
        with self.assertRaisesMessage(ValidationError, "can no longer be removed"):
            order.lines.get().delete()
        order.refresh_from_db()
        self.assertIsNotNone(order.approved_at)
