"""
Money that came out wrong on the sales side (g20, from the 9 October
audit's probes): exchange on a refunded note, commission, pricing in
another currency or unit, a write-off recovered in part. Each expected
figure was worked in a separate plain-Python script (calc_fix_money/).
"""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError

from apps.accounting.models import Payment, PaymentDirection
from apps.core.models import Currency, ExchangeRate

from .models import (
    CommissionBasis,
    CommissionPlan,
    Invoice,
    InvoicePolicy,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
    commission_report,
    outstanding_balance,
)
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


class MoneyCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.1"), valid_from=datetime.date(2026, 1, 1))

    def receipt_in(self, amount, currency, on=datetime.date(2026, 3, 10)):
        payment = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=on,
            amount=D(amount), currency=currency, bank_account=self.bank, counterpart_account=self.ar)
        payment.post()
        return payment


class RefundedCreditNoteExchangeTests(MoneyCase):
    """
    O70 (ForeignDepositCycleProbe). 800 EUR deposit at 1.1 (880), paid at
    1.1; 600 billed at 1.2 (720) draws 600 of it (660 held, 60 exchange);
    200 credited back at 1.1 (220) and refunded at 1.2 (240). The refund
    leaves 20 on the receivable, which is a loss: 80 in all, nothing on
    deposits or receivables (calc_fix_money/fx.py). Every allocation was
    passed as a receivable's, so the 20 went on the wrong side: 40.
    """

    def test_deposits_and_receivables_clear_and_the_gap_is_exchange(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company
        from .models import InvoicePayment

        loss = Account.objects.create(code="7100", name="FX loss", account_type=AccountType.EXPENSE)
        gain = Account.objects.create(code="7000", name="FX gain", account_type=AccountType.INCOME)
        company = Company.get()
        company.fx_loss_account, company.fx_gain_account = loss, gain
        company.save()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 5))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        deposit = order.create_down_payment_invoice(self.ar, amount=D("800"), invoice_date=DAY)
        deposit.post()
        self.allocate(self.receipt_in("800", self.eur, on=datetime.date(2026, 3, 2)), deposit, "800")
        self.ship(order, "6")
        invoice = self.bill(order, on=datetime.date(2026, 3, 6))
        note = Invoice.objects.get(pk=deposit.pk).create_credit_note(amount=D("200"))
        refund = Payment.objects.create(
            party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 3, 10),
            amount=D("200"), currency=self.eur, bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        InvoicePayment.objects.create(invoice=note, payment=refund, amount=D("200"))
        deposit = Invoice.objects.get(pk=deposit.pk)
        self.assertEqual(
            (self.balance(self.deposits), self.balance(self.ar), deposit.deposit_unapplied(),
             Invoice.objects.get(pk=invoice.pk).amount_due(), Invoice.objects.get(pk=note.pk).amount_due(),
             outstanding_balance(self.customer), self.balance(loss) + self.balance(gain)),
            (D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("0.00"), D("80.00")),
            "(deposits, AR, left on deposit, due on invoice, due on note, customer balance, net FX loss)")


class DepositHeldWhereItWasTakenTests(MoneyCase):
    """
    O105, the sales side. 300 taken up front into the deposit account; the
    setting then moved to another account. The final invoice draws the 300
    off the account that holds it: both read nothing after.
    """

    def test_a_drawdown_takes_the_deposit_from_where_it_was_held(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY,
                                          invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=DAY).post()
        moved = Account.objects.create(code="2310", name="Advances", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=moved)
        self.ship(order, "10")
        self.bill(order, on=datetime.date(2026, 3, 6))
        self.assertEqual((self.balance(self.deposits), self.balance(moved)), (D("0"), D("0")),
                         "(deposits held, the account set since)")


class CommissionTests(MoneyCase):
    """
    O74, O75, O76 (CommissionProbe). A plan of 10%. A bounced 1,000 counts
    nothing; 300 taken up front and 700 paid count 1,000; 1,000 EUR at 1.1
    counts 1,100 (calc_fix_money/commission.py).
    """

    def plan(self, basis):
        plan = CommissionPlan.objects.create(code="P", name="Ten", percent=D("10"), basis=basis)
        SalesRep.objects.create(party=self.rep, plan=plan)

    def commission(self, **period):
        rows = commission_report(**period)
        return (rows[0]["basis_amount"], rows[0]["commission"]) if rows else (D("0.00"), D("0.00"))

    def test_a_bounced_cheque_earns_no_commission_on_collection(self):
        self.plan(CommissionBasis.PAID)
        invoice = self.bill(self.make_order("10", "100", rep=self.rep))
        payment = self.receipt("1000")
        self.allocate(payment, invoice, "1000")
        payment.void()
        self.assertEqual(self.commission(), (D("0.00"), D("0.00")),
                         "(basis, commission) after the receipt was voided")

    def test_money_taken_up_front_counts_as_collected(self):
        self.plan(CommissionBasis.PAID)
        order = self.make_order("10", "100", rep=self.rep)
        deposit = order.create_down_payment_invoice(self.ar, percent=30, invoice_date=DAY)
        deposit.post()
        self.allocate(self.receipt("300"), deposit, "300")
        invoice = self.bill(order)  # draws the 300 down
        self.assertEqual(invoice.amount_deposited(), D("300.00"))
        self.allocate(self.receipt("700"), invoice, "700")
        self.assertEqual(self.commission(), (D("1000.00"), D("100.00")),
                         "(basis, commission): 1000 collected, 300 of it up front")

    def test_a_deposit_never_paid_is_not_collected(self):
        """The same, its 300 never paid: drawn down all the same, but only the 700 was collected."""
        self.plan(CommissionBasis.PAID)
        order = self.make_order("10", "100", rep=self.rep)
        order.create_down_payment_invoice(self.ar, percent=30, invoice_date=DAY).post()
        invoice = self.bill(order)
        self.allocate(self.receipt("700"), invoice, "700")
        self.assertEqual(self.commission(), (D("700.00"), D("70.00")), "(basis, commission)")

    def test_money_up_front_counts_on_the_day_it_is_drawn(self):
        """Drawn down on 1 March: a report of February counts none of it."""
        self.plan(CommissionBasis.PAID)
        order = self.make_order("10", "100", rep=self.rep)
        deposit = order.create_down_payment_invoice(self.ar, percent=30, invoice_date=datetime.date(2026, 2, 1))
        deposit.post()
        self.allocate(self.receipt("300", on=datetime.date(2026, 2, 2)), deposit, "300")
        self.bill(order)
        self.assertEqual(self.commission(date_to=datetime.date(2026, 2, 28)), (D("0.00"), D("0.00")))
        self.assertEqual(self.commission(date_from=DAY), (D("300.00"), D("30.00")))

    def test_commission_is_in_the_base_currency(self):
        self.plan(CommissionBasis.INVOICED)
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          sales_rep=self.rep)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        invoice = self.bill(order)
        self.assertEqual(invoice.exchange_rate, D("1.1"))
        self.assertEqual(self.commission(), (D("1100.00"), D("110.00")), "(basis, commission) for 1000 EUR at 1.1")

    def test_collected_commission_is_in_the_base_currency(self):
        """1,000 EUR billed at 1.1, collected at 1.2: 1,100 of sales collected, at the invoice's rate."""
        self.plan(CommissionBasis.PAID)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 5))
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        Company.objects.update(
            fx_gain_account=Account.objects.create(code="7000", name="FX gain", account_type=AccountType.INCOME),
            fx_loss_account=Account.objects.create(code="7100", name="FX loss", account_type=AccountType.EXPENSE))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          sales_rep=self.rep)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        invoice = self.bill(order)
        self.allocate(self.receipt_in("1000", self.eur), invoice, "1000")
        self.assertEqual(self.commission(), (D("1100.00"), D("110.00")), "(basis, commission)")


class RepointedAllocationTests(MoneyCase):
    """
    O54 (RepointProbe). 1,000 received and applied to X (1,000), then
    re-pointed to Y (500, unpaid): Y has 500 outstanding, so 1,000 cannot
    be applied to it. The ceiling added back the allocation's old amount,
    which was X's, not Y's, and Y read -500 due.
    """

    def invoice(self, price):
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        from .models import InvoiceLine

        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=D("1"), unit_price=D(price),
                                   revenue_account=self.revenue)
        invoice.post()
        return invoice

    def test_an_allocation_re_pointed_to_a_smaller_invoice_is_refused(self):
        from .models import InvoicePayment

        x, y = self.invoice("1000"), self.invoice("500")
        allocation = InvoicePayment.objects.create(invoice=x, payment=self.receipt("1000"), amount=D("1000"))
        allocation.invoice = y
        with self.assertRaisesMessage(ValidationError, "only has 500.00 outstanding"):
            allocation.save()
        self.assertEqual((Invoice.objects.get(pk=x.pk).amount_due(), Invoice.objects.get(pk=y.pk).amount_due()),
                         (D("0.00"), D("500.00")))

    def test_re_sized_on_its_own_invoice_it_still_counts_its_own_amount(self):
        from .models import InvoicePayment

        x = self.invoice("1000")
        allocation = InvoicePayment.objects.create(invoice=x, payment=self.receipt("1000"), amount=D("600"))
        allocation.amount = D("1000")
        allocation.save()
        self.assertEqual(Invoice.objects.get(pk=x.pk).amount_due(), D("0.00"))

    def test_re_pointed_over_the_api_by_the_ar_manager(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        from .models import InvoicePayment

        call_command("setup_roles", verbosity=0)
        x, y = self.invoice("1000"), self.invoice("500")
        allocation = InvoicePayment.objects.create(invoice=x, payment=self.receipt("1000"), amount=D("1000"))
        user = User.objects.create_user("ar")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        response = client.patch(f"/api/sales/invoice-payments/{allocation.pk}/", {"invoice": y.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Invoice.objects.get(pk=y.pk).amount_due(), D("500.00"))
        response = client.patch(f"/api/sales/invoice-payments/{allocation.pk}/", {"invoice": y.pk, "amount": "500"},
                                format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((Invoice.objects.get(pk=x.pk).amount_due(), Invoice.objects.get(pk=y.pk).amount_due()),
                         (D("1000.00"), D("0.00")))


class TheAuditAsksItTests(__import__("django.test", fromlist=["SimpleTestCase"]).SimpleTestCase):
    """audit_invariants reports an allocation summed past a void (O53, O74) and a drawdown off today's setting (O105)."""

    def findings(self, check, edit=lambda text: text):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        sources["sales"] = {path: edit(text) if path.name == "models.py" else text
                            for path, text in sources["sales"].items()}
        return [detail for _, detail in getattr(Command(), check)(["sales"], sources)]

    def test_allocations_summed_whole_are_reported(self):
        found = self.findings("allocations_counted_past_a_void", lambda text: text.replace(
            "        return standing_paid(self)\n",
            "        return sum(a.amount for a in self.payment_allocations.all())\n", 1))
        self.assertTrue(any("sales.amount_paid" in detail for detail in found), found)

    def test_a_drawdown_off_todays_setting_is_reported(self):
        found = self.findings("drawdowns_from_todays_setting", lambda text: text.replace(
            "        account = deposit.lines.get().revenue_account\n",
            "        account = Company.get().customer_deposit_account\n", 1))
        self.assertTrue(any("sales.apply_deposit" in detail for detail in found), found)

    def test_the_code_as_it_stands_is_not(self):
        self.assertEqual(self.findings("allocations_counted_past_a_void")
                         + self.findings("drawdowns_from_todays_setting"), [])


class NoteLineBookedWhereItsLineWasTests(MoneyCase):
    """
    O201. A sale of 1,000 booked to revenue 4000; a typed credit note line
    giving back 100 of it to another revenue account is refused; left blank,
    it takes 4000.
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from .models import InvoiceLine

        self.other = Account.objects.create(code="4100", name="Other sales", account_type=AccountType.INCOME)
        self.invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        self.line = InvoiceLine.objects.create(invoice=self.invoice, item=self.item, quantity=D("10"),
                                               unit_price=D("100"), revenue_account=self.revenue)
        self.invoice.post()
        self.note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar,
                                           credits=self.invoice)

    def test_a_typed_line_to_another_revenue_account_is_refused(self):
        from .models import InvoiceLine

        with self.assertRaisesMessage(ValidationError, "is booked there too"):
            InvoiceLine.objects.create(invoice=self.note, credits_line=self.line, item=self.item, quantity=D("1"),
                                       unit_price=D("100"), revenue_account=self.other)

    def test_left_blank_it_takes_its_lines_account(self):
        from .models import InvoiceLine

        line = InvoiceLine.objects.create(invoice=self.note, credits_line=self.line, item=self.item,
                                          quantity=D("1"), unit_price=D("100"))
        self.assertEqual(line.revenue_account, self.revenue)
        self.note.post()
        self.assertEqual((self.balance(self.revenue), self.balance(self.other)), (D("-900"), D("0")))

    def test_a_line_naming_no_line_is_held_to_its_originals_accounts(self):
        """Review F6: "Goodwill" 100 typed on the note to 4100 is refused; to 4000 it posts, 4000 at -900."""
        from .models import InvoiceLine

        with self.assertRaisesMessage(ValidationError, "a note on it is booked there too"):
            InvoiceLine.objects.create(invoice=self.note, description="Goodwill", quantity=D("1"),
                                       unit_price=D("100"), revenue_account=self.other)
        InvoiceLine.objects.create(invoice=self.note, description="Goodwill", quantity=D("1"),
                                   unit_price=D("100"), revenue_account=self.revenue)
        self.note.post()
        self.assertEqual((self.balance(self.revenue), self.balance(self.other)), (D("-900"), D("0")))


class WriteOffRecoveredInPartTests(MoneyCase):
    """
    O80 (WriteOffRecoveryProbe). 1,000 billed, 400 paid, 600 written off; a
    credit note of 300 undoes half of it. The other 300, paid after all, is
    recovered and received: bad debt, receivables and the aging all read 0.
    """

    def test_what_a_credit_note_left_written_off_can_be_recovered(self):
        from .models import ar_aging, customer_statement

        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt("400"), invoice, "400")
        invoice.write_off(reason="Customer in liquidation", on_date=datetime.date(2026, 4, 1))
        invoice = Invoice.objects.get(pk=invoice.pk)
        invoice.create_credit_note(quantities={invoice.lines.get(): D("3")})
        self.assertEqual((self.balance(self.bad_debt), self.balance(self.ar)), (D("300.00"), D("0.00")))
        invoice = Invoice.objects.get(pk=invoice.pk)
        invoice.recover_write_off(invoice.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        invoice = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual((invoice.amount_due(), invoice.write_offs.get().amount_recovered()),
                         (D("300.00"), D("300.00")))
        self.allocate(self.receipt("300", on=datetime.date(2026, 5, 2)), invoice, "300")
        aging = ar_aging(as_of=datetime.date(2026, 5, 31))
        self.assertEqual(
            (self.balance(self.bad_debt), self.balance(self.ar), sum(b["total"] for b in aging.values())),
            (D("0.00"), D("0.00"), D("0.00")), "(bad debt, AR, aging total)")
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 5, 31))
        self.assertEqual(statement["closing_balance"] if isinstance(statement, dict) else statement.closing_balance,
                         D("0.00"))
        with self.assertRaisesMessage(ValidationError, "already been recovered"):
            invoice.recover_write_off(invoice.write_offs.get())

    def test_the_bad_debt_report_reads_what_was_recovered_and_what_a_note_undid(self):
        """Review F7: written off 600, a note undid 300, 300 recovered: net 0, as bad debt in the ledger."""
        from .models import bad_debt_report

        invoice = self.bill(self.make_order("10", "100"))
        self.allocate(self.receipt("400"), invoice, "400")
        invoice.write_off(reason="Customer in liquidation", on_date=datetime.date(2026, 4, 1))
        Invoice.objects.get(pk=invoice.pk).create_credit_note(quantities={invoice.lines.get(): D("3")})
        invoice = Invoice.objects.get(pk=invoice.pk)
        invoice.recover_write_off(invoice.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        row = bad_debt_report()[0]
        self.assertEqual((row["written_off"], row["recovered"], row["undone_by_notes"], row["net"],
                          self.balance(self.bad_debt)),
                         (D("600.00"), D("300.00"), D("300.00"), D("0.00"), D("0.00")))


class PricedInTheLinesTermsTests(MoneyCase):
    """
    O78, O79 (PricingProbe). The item's list price is 10 in base (USD). A
    EUR line at 1.1 is priced 9.09; 2 boxes of 12 at 10 an each are 120 a
    box, 240.00; a break at 24 eaches is reached by 2 boxes
    (calc_fix_money/pricing.py).
    """

    def setUp(self):
        super().setUp()
        from apps.core.models import UnitOfMeasure

        self.item.sale_price = D("10")
        self.item.save()
        self.box = UnitOfMeasure.objects.create(code="box", name="Box of 12", base_unit=self.uom,
                                                conversion_factor=D("12"))

    def line(self, currency, uom, quantity):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=currency)
        return SalesOrderLine.objects.create(order=order, item=self.item, uom=uom, quantity=D(quantity),
                                             revenue_account=self.revenue)

    def test_an_order_in_another_currency_is_priced_in_it(self):
        line = self.line(self.eur, self.uom, "1")
        self.assertEqual(line.unit_price, D("9.09"), "10 USD is 9.09 EUR at 1.1")

    def test_a_line_in_boxes_is_priced_per_box(self):
        line = self.line(None, self.box, "2")
        self.assertEqual((line.unit_price, line.net_amount()), (D("120.00"), D("240.00")),
                         "(unit price per box, line net) at 10 an each, 12 to a box")

    def test_a_break_in_eaches_is_reached_by_boxes(self):
        from .models import PriceList, PriceListItem

        prices = PriceList.objects.create(code="STD", name="Standard", is_default=True,
                                          currency=Currency.objects.get(is_base=True))
        PriceListItem.objects.create(price_list=prices, item=self.item, min_quantity=D("1"), unit_price=D("10"))
        PriceListItem.objects.create(price_list=prices, item=self.item, min_quantity=D("24"), unit_price=D("9"))
        self.assertEqual(self.line(None, self.box, "2").unit_price, D("108.00"), "24 eaches at 9, 12 to a box")
        self.assertEqual(self.line(None, self.box, "1").unit_price, D("120.00"), "12 eaches at 10")


class AListInNoCurrencyIsInBaseTests(PricedInTheLinesTermsTests):
    """Review item 5, a decision: a list with no currency is in the base, so 6 on it is 5.45 on a EUR line at 1.1."""

    def test_a_list_in_no_currency_is_converted_for_a_euro_line(self):
        from .models import CustomerProfile, PriceList, PriceListItem

        prices = PriceList.objects.create(code="ANY", name="No currency", currency=None)
        PriceListItem.objects.create(price_list=prices, item=self.item, unit_price=D("6"))
        CustomerProfile.objects.create(party=self.customer, price_list=prices)
        self.assertEqual(self.line(self.eur, self.uom, "1").unit_price, D("5.45"))
        self.assertEqual(self.line(None, self.uom, "1").unit_price, D("6.00"))


class TheLastOfALineIsGivenBackAsItWasBookedTests(MoneyCase):
    """
    O199, review F10 (calc_fix_money/review/o199.py). 2.5 at 3.33 books 8.33. A note for 1.25 gives
    back its share, 4.16; the note for the other 1.25 is the last of the line and gives back the 4.17
    left of what it booked, as 0.25 at 3.33 (0.83) and 1 at 3.34: the receivable reads nothing.
    """

    def test_a_line_credited_in_two_halves_leaves_nothing_on_the_receivable(self):
        from .models import InvoiceLine

        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=D("2.5"), unit_price=D("3.33"),
                                   revenue_account=self.revenue)
        invoice.post()
        line = invoice.lines.get()
        first = Invoice.objects.get(pk=invoice.pk).create_credit_note(quantities={line: D("1.25")})
        last = Invoice.objects.get(pk=invoice.pk).create_credit_note(quantities={line: D("1.25")})
        self.assertEqual((first.total(), last.total()), (D("4.16"), D("4.17")))
        self.assertEqual(sorted((row.quantity, row.unit_price) for row in last.lines.all()),
                         [(D("0.25"), D("3.33")), (D("1"), D("3.34"))])
        self.assertEqual((self.balance(self.ar), Invoice.objects.get(pk=invoice.pk).amount_due()), (D("0"), D("0")))

    def test_the_last_of_a_line_is_held_to_what_is_left_as_booked(self):
        """A typed note for the last 1.25 at 3.34 (4.18) gives back a paisa more than the 4.17 left: refused."""
        from .models import InvoiceLine

        invoice = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=D("2.5"), unit_price=D("3.33"),
                                          revenue_account=self.revenue)
        invoice.post()
        Invoice.objects.get(pk=invoice.pk).create_credit_note(quantities={line: D("1.25")})
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar,
                                      credits=invoice)
        with self.assertRaisesMessage(ValidationError, "has 4.17 left to give back; this note gives back 4.18"):
            InvoiceLine.objects.create(invoice=note, credits_line=line, item=self.item, quantity=D("1.25"),
                                       unit_price=D("3.34"))
            note.post()
