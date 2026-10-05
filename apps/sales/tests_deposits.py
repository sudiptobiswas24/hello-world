"""
Down payments.

The accounting question underneath: taking money up front does not earn
it. Until the goods go out the company owes the customer either the
goods or the money back, which is a liability, not revenue.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    DepositApplication,
    Invoice,
    InvoicePolicy,
    SettlementStatus,
    committed_balance,
    commission_report,
    outstanding_balance,
    revenue_report,
)
from .tests_base import SalesTestCase


class DownPaymentInvoiceTests(SalesTestCase):
    def test_it_credits_a_liability_not_revenue(self):
        """The goods are still in the warehouse. Nothing has been earned."""
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()

        self.assertEqual(deposit.total(), Decimal("300"))
        self.assertEqual(self.balance(self.deposits), Decimal("-300"))  # credit
        self.assertEqual(self.balance(self.revenue), Decimal("0"))
        self.assertEqual(self.balance(self.ar), Decimal("300"))

    def test_a_percent_and_an_amount_agree(self):
        order = self.make_order("10", "100")
        by_percent = order.create_down_payment_invoice(self.ar, percent=25)
        self.assertEqual(by_percent.total(), Decimal("250"))

    def test_it_needs_exactly_one_of_amount_or_percent(self):
        order = self.make_order("10", "100")
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_down_payment_invoice(self.ar, amount=Decimal("100"), percent=10)
        with self.assertRaisesMessage(ValidationError, "not both"):
            order.create_down_payment_invoice(self.ar)

    def test_deposits_cannot_exceed_the_order(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, amount=Decimal("700")).post()
        with self.assertRaisesMessage(ValidationError, "exceed the order total"):
            order.create_down_payment_invoice(self.ar, amount=Decimal("400"))

    def test_a_draft_order_cannot_take_one(self):
        from .models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(
            customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=self.usd
        )
        SalesOrderLine.objects.create(
            order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
            unit_price=Decimal("100"), revenue_account=self.revenue,
        )
        with self.assertRaisesMessage(ValidationError, "Only a confirmed order"):
            order.create_down_payment_invoice(self.ar, percent=50)

    def test_it_needs_an_account_to_hold_the_money(self):
        from apps.core.models import Company

        company = Company.get()
        company.customer_deposit_account = None
        company.save()
        order = self.make_order("10", "100")
        with self.assertRaisesMessage(ValidationError, "no customer deposit account"):
            order.create_down_payment_invoice(self.ar, percent=30)

    def test_it_lets_a_bill_on_delivery_order_be_billed_early(self):
        """The whole point: DELIVERED refuses to invoice before shipping,
        and a deposit is how you legitimately take money anyway."""
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        with self.assertRaisesMessage(ValidationError, "ship the goods first"):
            order.create_invoice(self.ar)

        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.assertEqual(deposit.amount_due(), Decimal("300"))


class DepositDrawdownTests(SalesTestCase):
    def deposit_on(self, order, percent=30):
        deposit = order.create_down_payment_invoice(self.ar, percent=percent)
        deposit.post()
        return deposit

    def test_posting_the_real_invoice_draws_it_down(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("1000"))
        self.assertEqual(invoice.amount_deposited(), Decimal("300"))
        self.assertEqual(invoice.amount_due(), Decimal("700"))

    def test_the_ledger_ends_up_right(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        payment = self.receipt(Decimal("300"))
        self.allocate(payment, deposit, Decimal("300"))
        self.bill(order)

        # The liability is discharged, revenue is recognised once, and AR
        # carries only the balance still to collect.
        self.assertEqual(self.balance(self.deposits), Decimal("0"))
        self.assertEqual(self.balance(self.revenue), Decimal("-1000"))
        self.assertEqual(self.balance(self.ar), Decimal("700"))

    def test_the_total_still_says_what_was_sold(self):
        """Netting the deposit into the total would make every revenue
        report unpick the difference."""
        order = self.make_order("10", "100")
        self.deposit_on(order)
        invoice = self.bill(order)
        self.assertEqual(invoice.subtotal(), Decimal("1000"))

    def test_an_unpaid_deposit_still_draws_down(self):
        """Two open receivables that add up to what is owed is correct;
        blocking the final invoice on a slow payer is not."""
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        invoice = self.bill(order)

        self.assertEqual(deposit.amount_due(), Decimal("300"))
        self.assertEqual(invoice.amount_due(), Decimal("700"))
        self.assertEqual(outstanding_balance(self.customer), Decimal("1000"))

    def test_a_deposit_is_only_drawn_down_once(self):
        order = self.make_order("10", "100")
        deposit = self.deposit_on(order)
        self.bill(order)
        self.assertEqual(deposit.deposit_unapplied(), Decimal("0"))

    def test_several_deposits_draw_down_in_order(self):
        order = self.make_order("10", "100")
        self.deposit_on(order, percent=20)
        self.deposit_on(order, percent=10)
        invoice = self.bill(order)
        self.assertEqual(invoice.deposit_applications.count(), 2)
        self.assertEqual(invoice.amount_due(), Decimal("700"))

    def test_a_deposit_larger_than_the_invoice_is_capped(self):
        """Partial billing: the deposit can only clear what has been billed."""
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.deposit_on(order, percent=50)
        self.ship(order, "2")
        invoice = self.bill(order)

        self.assertEqual(invoice.total(), Decimal("200"))
        self.assertEqual(invoice.amount_due(), Decimal("0"))
        self.assertEqual(order.deposits().get().deposit_unapplied(), Decimal("300"))

    def test_the_rest_of_the_deposit_survives_to_the_next_invoice(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        self.deposit_on(order, percent=50)
        self.ship(order, "2")
        self.bill(order)
        self.ship(order, "8")
        second = self.bill(order)

        self.assertEqual(second.total(), Decimal("800"))
        self.assertEqual(second.amount_deposited(), Decimal("300"))
        self.assertEqual(second.amount_due(), Decimal("500"))

    def test_the_drawdown_can_be_turned_off(self):
        order = self.make_order("10", "100")
        self.deposit_on(order)
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        invoice.post(apply_deposits=False)
        self.assertEqual(invoice.amount_due(), Decimal("1000"))


class DepositGuardTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.make_order("10", "100")
        self.deposit = self.order.create_down_payment_invoice(self.ar, percent=30)
        self.deposit.post()

    def test_a_draft_invoice_cannot_draw_down(self):
        invoice = self.order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        with self.assertRaisesMessage(ValidationError, "Only a posted invoice"):
            invoice.apply_deposit(self.deposit)

    def test_a_down_payment_cannot_draw_down_another(self):
        second = self.order.create_down_payment_invoice(self.ar, percent=10)
        second.post()
        with self.assertRaisesMessage(ValidationError, "cannot draw down a deposit"):
            second.apply_deposit(self.deposit)

    def test_only_a_down_payment_can_be_drawn_down(self):
        invoice = self.bill(self.order)
        with self.assertRaisesMessage(ValidationError, "Only a posted down-payment invoice"):
            invoice.apply_deposit(invoice)

    def test_it_cannot_cross_customers(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        other = Party.objects.create(code="C-2", name="Other", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=other, role=PartyRole.CUSTOMER)
        self.customer = other
        invoice = self.bill(self.make_order("1", "500"))
        with self.assertRaisesMessage(ValidationError, "different customer"):
            invoice.apply_deposit(self.deposit)

    def test_it_cannot_be_over_applied(self):
        invoice = self.bill(self.order)  # auto-applies the 300
        with self.assertRaisesMessage(ValidationError, "nothing left to draw down"):
            invoice.apply_deposit(self.deposit)

    def test_a_down_payment_needs_an_order(self):
        invoice = self.bill(self.order)
        invoice.is_down_payment = True
        invoice.sales_order = None
        with self.assertRaisesMessage(ValidationError, "must be against a sales order"):
            invoice.clean()


class DepositReportingTests(SalesTestCase):
    def test_a_deposit_is_not_revenue(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, percent=30).post()

        rows = revenue_report()
        self.assertEqual(rows, [])

        self.bill(order)
        self.assertEqual(revenue_report()[0]["net"], Decimal("1000"))

    def test_a_deposit_earns_no_commission(self):
        from .models import CommissionBasis, CommissionPlan, SalesRep

        plan = CommissionPlan.objects.create(
            code="P1", name="Flat", percent=Decimal("10"), basis=CommissionBasis.INVOICED
        )
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        order.create_down_payment_invoice(self.ar, percent=30).post()

        self.assertEqual(commission_report(), [])

        self.bill(order)
        self.assertEqual(commission_report()[0]["commission"], Decimal("100.00"))

    def test_it_does_not_eat_the_credit_limit_twice(self):
        """Taking money up front must not consume the customer's limit as
        if it were extra exposure."""
        order = self.make_order("10", "100")
        self.assertEqual(committed_balance(self.customer), Decimal("1000"))

        order.create_down_payment_invoice(self.ar, percent=30).post()
        self.assertEqual(committed_balance(self.customer), Decimal("1000"))

    def test_a_fully_deposited_invoice_reads_as_paid(self):
        order = self.make_order("10", "100")
        order.create_down_payment_invoice(self.ar, amount=Decimal("1000")).post()
        invoice = self.bill(order)
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PAID)


class ADepositIsReturnedOnlyOnceTests(SalesTestCase):
    """
    A deposit credited back to the customer used to stay available: the
    final invoice drew it down again, so the customer had the money back
    and a discount of the same amount, and the deposit account went
    into debit. Crediting a deposit that had already been drawn down did
    the same from the other side.
    """

    def test_a_credited_deposit_is_not_drawn_down_again(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        note = deposit.create_credit_note()
        invoice = self.bill(order)

        self.assertEqual(
            (note.total(), deposit.deposit_unapplied(), invoice.amount_deposited(),
             invoice.amount_due(), self.balance(self.deposits), self.balance(self.ar)),
            (Decimal("300.00"), Decimal("0.00"), Decimal("0"), Decimal("1000.00"),
             Decimal("0"), Decimal("1000.00")),
        )

    def test_crediting_a_part_used_deposit_returns_only_what_is_left(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.allocate(self.receipt("300"), deposit, "300")
        self.ship(order, "2")
        invoice = self.bill(order)
        note = deposit.create_credit_note()

        self.assertEqual(
            (invoice.amount_deposited(), note.total(), note.refund_due(),
             self.balance(self.deposits)),
            (Decimal("200.00"), Decimal("100.00"), Decimal("100.00"), Decimal("0")),
        )

    def test_an_unpaid_part_used_deposit_leaves_owed_what_was_delivered(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.ship(order, "2")
        self.bill(order)
        note = deposit.create_credit_note()

        self.assertEqual(
            (note.refund_due(), deposit.amount_due(), outstanding_balance(self.customer),
             self.balance(self.ar), self.balance(self.deposits)),
            (Decimal("0.00"), Decimal("200.00"), Decimal("200.00"), Decimal("200.00"),
             Decimal("0")),
        )

    def test_a_deposit_drawn_down_in_full_cannot_be_credited(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.bill(order)
        with self.assertRaisesMessage(ValidationError, "already been drawn down"):
            deposit.create_credit_note()
        self.assertEqual(self.balance(self.deposits), Decimal("0"))

    def test_a_deposit_is_credited_by_amount_not_quantity(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        with self.assertRaisesMessage(ValidationError, "whatever is left"):
            deposit.create_credit_note(quantities={deposit.lines.get(): Decimal("0.5")})

    def test_a_credited_deposit_makes_room_for_another(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, amount=Decimal("1000"))
        deposit.post()
        deposit.create_credit_note()
        again = order.create_down_payment_invoice(self.ar, amount=Decimal("400"))
        self.assertEqual(again.total(), Decimal("400.00"))


class AForeignDepositClearsTests(SalesTestCase):
    """
    300 EUR taken at 80 and drawn down against an invoice billed at 83.
    The deposit account was debited at the invoice's rate, 24,900 against
    the 24,000 credited, and kept the 900 for good. Released at the rate
    it was taken at, the 900 is what it is: a realised exchange loss on
    the part of the invoice the deposit settled.
    """

    def test_the_deposit_account_clears_and_the_difference_is_exchange(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company, Currency, ExchangeRate

        from .models import SalesOrder, SalesOrderLine

        loss = Account.objects.create(code="7100", name="FX loss",
                                      account_type=AccountType.EXPENSE)
        gain = Account.objects.create(code="7000", name="FX gain",
                                      account_type=AccountType.INCOME)
        company = Company.get()
        company.fx_loss_account, company.fx_gain_account = loss, gain
        company.save()
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("80"),
                                    valid_from=datetime.date(2026, 1, 1))
        order = SalesOrder.objects.create(customer=self.customer, currency=eur,
                                          order_date=datetime.date(2026, 3, 1))
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                      quantity=Decimal("10"), unit_price=Decimal("100"),
                                      revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(
            self.ar, percent=30, invoice_date=datetime.date(2026, 3, 1))
        deposit.post()
        ExchangeRate.objects.create(currency=eur, rate=Decimal("83"),
                                    valid_from=datetime.date(2026, 3, 5))
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 6))
        invoice.post()

        self.assertEqual(
            (invoice.amount_deposited(), invoice.amount_due(), self.balance(self.deposits),
             self.balance(self.ar), self.balance(loss), self.balance(gain)),
            (Decimal("300.00"), Decimal("700.00"), Decimal("0"), Decimal("82100.00"),
             Decimal("900.00"), Decimal("0")),
        )
        # The exchange entry is a fact of the drawdown, kept on it.
        application = invoice.deposit_applications.get()
        self.assertEqual(
            sorted((line.account.code, line.debit, line.credit)
                   for line in application.fx_entry.lines.all()),
            [("1100", Decimal("0.00"), Decimal("900.00")),
             ("7100", Decimal("900.00"), Decimal("0.00"))],
        )


class FxFixture(SalesTestCase):
    def foreign_order(self, price="100", held="80", billed="83"):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company, Currency, ExchangeRate

        from .models import SalesOrder, SalesOrderLine

        self.loss = Account.objects.create(code="7100", name="FX loss",
                                           account_type=AccountType.EXPENSE)
        self.gain = Account.objects.create(code="7000", name="FX gain",
                                           account_type=AccountType.INCOME)
        company = Company.get()
        company.fx_loss_account, company.fx_gain_account = self.loss, self.gain
        company.save()
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal(held),
                                    valid_from=datetime.date(2026, 1, 1))
        ExchangeRate.objects.create(currency=eur, rate=Decimal(billed),
                                    valid_from=datetime.date(2026, 3, 5))
        order = SalesOrder.objects.create(customer=self.customer, currency=eur,
                                          order_date=datetime.date(2026, 3, 1))
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                      quantity=Decimal("1"), unit_price=Decimal(price),
                                      revenue_account=self.revenue)
        order.confirm()
        return order


class NoPaisaLeftOnTheReceivableTests(FxFixture):
    """
    100.01 EUR taken at 80.111111 and drawn down in full against an
    invoice at 83.333333. Rounding the difference of the rates gave
    322.25 of exchange against an invoice booked at 8,334.17 and cleared
    at 8,011.91, so the customer's account kept a paisa that no document
    explained. Each side rounded on its own, the difference is 322.26.
    """

    def test_a_fully_drawn_invoice_leaves_the_receivable_at_the_deposit(self):
        order = self.foreign_order(price="100.01", held="80.111111", billed="83.333333")
        deposit = order.create_down_payment_invoice(
            self.ar, amount=Decimal("100.01"), invoice_date=datetime.date(2026, 3, 1))
        deposit.post()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 6))
        invoice.post()

        self.assertEqual(
            (invoice.amount_due(), self.balance(self.ar), self.balance(self.loss),
             self.balance(self.deposits)),
            (Decimal("0.00"), Decimal("8011.91"), Decimal("322.26"), Decimal("0")),
        )


class NoExchangeAccountTests(FxFixture):
    def test_the_refusal_says_it_is_the_deposit(self):
        from apps.core.models import Company

        order = self.foreign_order()
        company = Company.get()
        company.fx_loss_account = None
        company.save()
        deposit = order.create_down_payment_invoice(
            self.ar, percent=30, invoice_date=datetime.date(2026, 3, 1))
        deposit.post()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 6))
        with self.assertRaisesMessage(ValidationError, "applied to"):
            invoice.post()
        self.assertFalse(Invoice.objects.get(pk=invoice.pk).posted)


class AReturnedDepositIsNotASaleTests(SalesTestCase):
    """
    The deposit was left out of revenue and commission as not a sale;
    the credit note returning it was not, and read as a sale of minus
    the deposit, clawing back commission the rep had never been paid.
    """

    def test_revenue_does_not_go_negative(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        deposit.create_credit_note()
        self.assertEqual(revenue_report(), [])

    def test_no_commission_is_clawed_back(self):
        from .models import CommissionBasis, CommissionPlan, SalesRep

        plan = CommissionPlan.objects.create(
            code="P1", name="Flat", percent=Decimal("10"), basis=CommissionBasis.INVOICED
        )
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        deposit.create_credit_note()
        self.assertEqual(commission_report(), [])


class ADownPaymentIsOneLineOfMoneyHeldTests(SalesTestCase):
    """
    A draft down payment could be given more lines, a tax or another
    account. A second line booked revenue on a document every report
    leaves out; crediting one with two lines crashed.
    """

    def draft(self):
        return self.make_order("10", "100").create_down_payment_invoice(self.ar, percent=30)

    def test_a_second_line_is_refused(self):
        from .models import InvoiceLine

        deposit = self.draft()
        InvoiceLine.objects.create(invoice=deposit, description="Bank charge",
                                   quantity=Decimal("1"), unit_price=Decimal("5"),
                                   revenue_account=self.revenue)
        with self.assertRaisesMessage(ValidationError, "single line"):
            deposit.post()

    def test_tax_on_an_advance_for_goods_is_refused(self):
        from apps.accounting.models import Account, AccountType, Tax

        payable = Account.objects.create(code="2100", name="Tax",
                                          account_type=AccountType.LIABILITY)
        tax = Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"),
                                 collected_account=payable, paid_account=payable)
        deposit = self.draft()
        deposit.lines.get().taxes.add(tax)
        # No GST is due on an advance for goods; a job-work order's is
        # taxed (apps/gst/tests_advances.py).
        with self.assertRaisesMessage(ValidationError, "No tax is due on an advance for goods"):
            deposit.post()

    def test_another_account_is_refused(self):
        deposit = self.draft()
        line = deposit.lines.get()
        line.revenue_account = self.revenue
        line.save()
        with self.assertRaisesMessage(ValidationError, "customer deposit account"):
            deposit.post()

    def test_a_discount_is_refused(self):
        deposit = self.draft()
        line = deposit.lines.get()
        line.discount_percent = Decimal("10")
        line.save()
        with self.assertRaisesMessage(ValidationError, "no discount"):
            deposit.post()


class OnlyACompleteReturnReversesTheDepositTests(SalesTestCase):
    def test_a_deposit_credited_untouched_reverses_it(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        note = deposit.create_credit_note()
        self.assertEqual(note.journal_entry.reverses, deposit.journal_entry)

    def test_what_is_left_of_a_part_used_one_does_not(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.ship(order, "2")
        self.bill(order)
        note = deposit.create_credit_note()
        self.assertIsNone(note.journal_entry.reverses)


class ADraftCreditNoteGivesNothingBackTests(SalesTestCase):
    """Only a posted note has done anything; a draft is a proposal."""

    def test_a_draft_note_leaves_the_deposit_held(self):
        from .models import InvoiceLine

        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        draft = Invoice.objects.create(customer=self.customer, receivable_account=self.ar,
                                       invoice_date=datetime.date(2026, 3, 20),
                                       currency=self.usd, credits=deposit)
        InvoiceLine.objects.create(invoice=draft, credits_line=deposit.lines.get(),
                                   quantity=Decimal("1"), unit_price=Decimal("300"),
                                   revenue_account=self.deposits)
        self.assertEqual((deposit.amount_credited(), deposit.deposit_unapplied()),
                         (Decimal("0"), Decimal("300.00")))


class DownPaymentsStayWithinTheOrderTests(SalesTestCase):
    """
    Drafts were checked against posted deposits only: 700 and then 400 on
    a 1,000 order each passed when drafted, and both then posted.
    """

    def test_the_second_draft_is_refused_when_it_posts(self):
        order = self.make_order("10", "100")
        first = order.create_down_payment_invoice(self.ar, amount=Decimal("700"))
        second = order.create_down_payment_invoice(self.ar, amount=Decimal("400"))
        first.post()
        with self.assertRaisesMessage(ValidationError, "exceed the order total"):
            second.post()
        self.assertEqual(self.balance(self.deposits), Decimal("-700.00"))


class PartOfADepositIsGivenBackTests(SalesTestCase):
    """
    A deposit could only be credited back whole, or whatever was left of
    it: a customer who cut the order kept no deposit for the rest.
    """

    def test_part_is_given_back_and_the_rest_stays_held_for_the_order(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED)
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        self.assertEqual(self.balance(self.deposits), Decimal("-300.00"))

        note = deposit.create_credit_note(memo="Order cut to seven", amount=Decimal("90"))
        self.assertEqual((note.total(), deposit.deposit_unapplied(), self.balance(self.deposits)),
                         (Decimal("90.00"), Decimal("210.00"), Decimal("-210.00")))
        self.assertIsNone(note.journal_entry.reverses)  # part, not the whole entry undone

        self.ship(order, "7")
        invoice = self.bill(order)
        self.assertEqual((invoice.amount_deposited(), invoice.amount_due(), self.balance(self.deposits)),
                         (Decimal("210.00"), Decimal("490.00"), Decimal("0")))

    def test_more_than_is_left_or_nothing_is_refused(self):
        order = self.make_order("10", "100")
        deposit = order.create_down_payment_invoice(self.ar, percent=30)
        deposit.post()
        deposit.create_credit_note(amount=Decimal("200"))
        with self.assertRaisesMessage(ValidationError, "Only 100.00 of"):
            deposit.create_credit_note(amount=Decimal("100.01"))
        for amount in ("0", "-5", "10.005"):
            with self.subTest(amount=amount), self.assertRaisesMessage(ValidationError, "above nothing"):
                deposit.create_credit_note(amount=Decimal(amount))
        self.assertEqual(deposit.deposit_unapplied(), Decimal("100.00"))
        deposit.create_credit_note()  # the rest
        self.assertEqual((deposit.deposit_unapplied(), self.balance(self.deposits)),
                         (Decimal("0.00"), Decimal("0")))

    def test_an_invoice_is_not_credited_by_amount(self):
        invoice = self.bill(self.make_order("10", "100"))
        with self.assertRaisesMessage(ValidationError, "not by an amount"):
            invoice.create_credit_note(amount=Decimal("10"))
