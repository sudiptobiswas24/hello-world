"""
review_money probes, sales side (gate g20). Expected figures are worked in
$SP/calc_review_money/calc.py or in the docstring, not read off the code.
"""
import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import Currency, ExchangeRate, UnitOfMeasure

from .models import (
    CommissionBasis, CommissionPlan, Invoice, InvoiceLine, InvoicePayment, InvoicePolicy, PriceList,
    PriceListItem, SalesOrder, SalesOrderLine, SalesRep, ar_aging, bad_debt_report, commission_report,
    outstanding_balance,
)
from .tests_money import DAY, MoneyCase


class LedgerAgainstDerived(MoneyCase):
    def derived_ar(self):
        total = D("0")
        for inv in Invoice.objects.filter(customer=self.customer, posted=True, is_down_payment=False):
            total += -inv.amount_due() if inv.is_credit_note() else inv.amount_due()
        return total

    def agree(self, label):
        ledger = self.balance(self.ar)
        aging = sum(b["total"] for b in ar_aging(as_of=datetime.date(2026, 12, 31)).values())
        from .models import still_owed
        invoices = Invoice.objects.filter(customer=self.customer, posted=True, credits__isnull=True,
                                          is_down_payment=False)
        owing = {i.pk for i in invoices if i.amount_due() > 0}
        self.assertEqual(set(still_owed(invoices).values_list("pk", flat=True)), owing,
                         f"{label}: still_owed() against amount_due() > 0")
        if ledger < 0:
            aging = ledger  # aging reads what is owed to us only
        self.assertEqual((ledger, self.derived_ar(), outstanding_balance(self.customer), aging),
                         (ledger, ledger, ledger, ledger), f"{label}: (ledger AR, sum of dues, balance, aging)")

    def invoice_1000(self):
        return self.bill(self.make_order("10", "100"))

    def note(self, invoice, qty):
        invoice = Invoice.objects.get(pk=invoice.pk)
        return invoice.create_credit_note(quantities={invoice.lines.get(): D(qty)})

    def refresh(self, invoice):
        return Invoice.objects.get(pk=invoice.pk)


class WriteOffSequenceProbe(LedgerAgainstDerived):
    def test_note_larger_than_the_write_off_then_refund_then_recover(self):
        # 1000, paid 400, WO 600, CN 800: undoes 600, refund 200. AR = 1000-400-600-800+600 = -200 (calc case 6)
        inv = self.invoice_1000()
        self.allocate(self.receipt("400"), inv, "400")
        self.refresh(inv).write_off(reason="liquidation", on_date=datetime.date(2026, 4, 1))
        note = self.note(inv, "8")
        self.agree("after the note")
        self.assertEqual(self.balance(self.ar), D("-200"))
        refund = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT,
                                        payment_date=datetime.date(2026, 4, 5), amount=D("200"),
                                        currency=self.usd, bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        InvoicePayment.objects.create(invoice=self.refresh(note), payment=refund, amount=D("200"))
        self.agree("after the refund")
        self.assertEqual(self.balance(self.ar), D("0"))
        refund.void()
        self.agree("after the refund bounced")
        self.assertEqual(self.balance(self.ar), D("-200"))

    def test_written_off_in_full_noted_recovered_noted_again_paid(self):
        # 1000 WO in full; CN 300 undoes 300; recover min(1000, 1000-300)=700 -> AR 700;
        # CN2 200 clears 200 of the 700 -> 500; pay 500 -> 0.
        inv = self.invoice_1000()
        self.refresh(inv).write_off(reason="x", on_date=datetime.date(2026, 4, 1))
        self.note(inv, "3")
        self.agree("note undoing the write-off")
        inv = self.refresh(inv)
        inv.recover_write_off(inv.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        self.agree("recovered what was left")
        self.assertEqual(self.balance(self.ar), D("700"))
        self.note(inv, "2")
        self.agree("second note after the recovery")
        self.assertEqual(self.balance(self.ar), D("500"))
        self.allocate(self.receipt("500", on=datetime.date(2026, 5, 20)), self.refresh(inv), "500")
        self.agree("paid")
        self.assertEqual(self.balance(self.ar), D("0"))

    def test_credit_note_before_the_write_off_and_a_note_after(self):
        # CN 300 on unpaid 1000 -> due 700. WO 700 -> 0. CN2 200 undoes 200 of the WO -> AR 0.
        # recover: min(700, 700-200)=500 -> AR 500.
        inv = self.invoice_1000()
        self.note(inv, "3")
        self.refresh(inv).write_off(reason="x", on_date=datetime.date(2026, 4, 1))
        self.agree("write-off after a note")
        self.note(inv, "2")
        self.agree("second note after the write-off")
        inv = self.refresh(inv)
        inv.recover_write_off(inv.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        self.agree("recovered")
        self.assertEqual(self.balance(self.ar), D("500"))

    def test_two_write_offs_a_note_and_recovery_of_each(self):
        # 1000 paid 400; WO#1 300, WO#2 300; CN 400 undoes 400 of the 600; recoverable in all 200.
        inv = self.invoice_1000()
        self.allocate(self.receipt("400"), inv, "400")
        self.refresh(inv).write_off(amount=D("300"), reason="a", on_date=datetime.date(2026, 4, 1))
        self.refresh(inv).write_off(amount=D("300"), reason="b", on_date=datetime.date(2026, 4, 2))
        self.note(inv, "4")
        self.agree("note over two write-offs")
        inv = self.refresh(inv)
        first, second = inv.write_offs.order_by("pk")
        inv.recover_write_off(first, on_date=datetime.date(2026, 5, 1))
        self.agree("first recovered")
        inv = self.refresh(inv)
        with self.assertRaises(ValidationError):
            inv.recover_write_off(second, on_date=datetime.date(2026, 5, 2))
        self.assertEqual(self.balance(self.ar), D("200"))

    def test_bad_debt_report_after_a_part_recovery(self):
        # O80 scenario: WO 600, CN 300 undoes 300, recover 300. Ledger bad debt 0; recovered cash-side = 300.
        inv = self.invoice_1000()
        self.allocate(self.receipt("400"), inv, "400")
        self.refresh(inv).write_off(reason="x", on_date=datetime.date(2026, 4, 1))
        self.note(inv, "3")
        inv = self.refresh(inv)
        inv.recover_write_off(inv.write_offs.get(), on_date=datetime.date(2026, 5, 1))
        row = bad_debt_report()[0]
        self.assertEqual((row["written_off"], row["recovered"], self.balance(self.bad_debt)),
                         (D("600"), D("300"), D("0")), "(written off, recovered, bad debt in the ledger)")


class FxRepointProbe(MoneyCase):
    def test_repointed_foreign_allocation_restates_its_difference(self):
        # X: 500 EUR @1.1 (550), Y: 500 EUR @1.2 (600), receipt 500 EUR @1.15 = 575.
        # On X: 550-575 = -25 (gain). Repointed to Y: 600-575 = +25 (loss). AR = X open 550 only.
        loss = Account.objects.create(code="7100", name="loss", account_type=AccountType.EXPENSE)
        gain = Account.objects.create(code="7000", name="gain", account_type=AccountType.INCOME)
        from apps.core.models import Company
        Company.objects.update(fx_loss_account=loss, fx_gain_account=gain)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 5))
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.15"), valid_from=datetime.date(2026, 3, 9))

        def inv(day):
            i = Invoice.objects.create(customer=self.customer, invoice_date=day, receivable_account=self.ar,
                                       currency=self.eur)
            InvoiceLine.objects.create(invoice=i, item=self.item, quantity=D("1"), unit_price=D("500"),
                                       revenue_account=self.revenue)
            i.post()
            return i
        x, y = inv(datetime.date(2026, 3, 1)), inv(datetime.date(2026, 3, 5))
        receipt = self.receipt_in("500", self.eur, on=datetime.date(2026, 3, 10))
        a = InvoicePayment.objects.create(invoice=x, payment=receipt, amount=D("500"))
        self.assertEqual(self.balance(gain), D("-25"))
        a.invoice = y
        a.save()
        self.assertEqual((self.balance(self.ar), self.balance(gain) + self.balance(loss),
                          Invoice.objects.get(pk=x.pk).amount_due(), Invoice.objects.get(pk=y.pk).amount_due()),
                         (D("550"), D("25"), D("500"), D("0")), "(AR, net FX, X due, Y due)")


class NoteLineAccountProbe(MoneyCase):
    def test_a_note_line_naming_no_original_line_credits_any_revenue_account(self):
        # O201 closed for a line that names its original line; a free-typed one names none.
        other = Account.objects.create(code="4100", name="Other sales", account_type=AccountType.INCOME)
        inv = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=inv, item=self.item, quantity=D("10"), unit_price=D("100"),
                                   revenue_account=self.revenue)
        inv.post()
        note = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar,
                                      credits=inv)
        InvoiceLine.objects.create(invoice=note, description="Goodwill", quantity=D("1"), unit_price=D("100"),
                                   revenue_account=other)
        note.post()
        self.assertEqual((self.balance(self.revenue), self.balance(other)), (D("-900"), D("0")),
                         "(4000, 4100): the note credits where the sale was booked, or is refused")


class CommissionProbe(MoneyCase):
    def plan(self, basis):
        CommissionPlan.objects.create(code="P", name="Ten", percent=D("10"), basis=basis)
        SalesRep.objects.create(party=self.rep, plan=CommissionPlan.objects.get(code="P"))

    def figure(self, **period):
        rows = commission_report(**period)
        return (rows[0]["basis_amount"], rows[0]["commission"]) if rows else (D("0.00"), D("0.00"))

    def test_bounced_then_paid_again_counts_once(self):
        self.plan(CommissionBasis.PAID)
        inv = self.bill(self.make_order("10", "100", rep=self.rep))
        first = self.receipt("1000")
        self.allocate(first, inv, "1000")
        first.void()
        self.allocate(self.receipt("1000", on=datetime.date(2026, 3, 12)), inv, "1000")
        self.assertEqual(self.figure(), (D("1000.00"), D("100.00")))

    def test_foreign_invoice_paid_at_another_rate_is_commissioned_at_the_invoice_rate(self):
        # 1000 EUR @1.1 = 1100; receipt later at 1.2; 10% of 1100 = 110 (calc case 3)
        self.plan(CommissionBasis.PAID)
        from apps.core.models import Company
        Company.objects.update(
            fx_loss_account=Account.objects.create(code="7100", name="loss", account_type=AccountType.EXPENSE),
            fx_gain_account=Account.objects.create(code="7000", name="gain", account_type=AccountType.INCOME))
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 8))
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY, currency=self.eur,
                                          sales_rep=self.rep)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        inv = self.bill(order)
        self.allocate(self.receipt_in("1000", self.eur, on=datetime.date(2026, 3, 10)), inv, "1000")
        self.assertEqual(self.figure(), (D("1100.00"), D("110.00")))

    def test_credit_note_after_the_period_is_a_later_periods_business(self):
        # paid 1000 on 10 March; credit note 300 refunded on 5 April. March = 1000; April = -300; all = 700.
        self.plan(CommissionBasis.PAID)
        inv = self.bill(self.make_order("10", "100", rep=self.rep))
        self.allocate(self.receipt("1000"), inv, "1000")
        note = Invoice.objects.get(pk=inv.pk).create_credit_note(quantities={inv.lines.get(): D("3")})
        refund = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT,
                                        payment_date=datetime.date(2026, 4, 5), amount=D("300"), currency=self.usd,
                                        bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        InvoicePayment.objects.create(invoice=Invoice.objects.get(pk=note.pk), payment=refund, amount=D("300"))
        march = self.figure(date_from=datetime.date(2026, 3, 1), date_to=datetime.date(2026, 3, 31))
        april = self.figure(date_from=datetime.date(2026, 4, 1), date_to=datetime.date(2026, 4, 30))
        whole = self.figure()
        self.assertEqual((march, april, whole),
                         ((D("1000.00"), D("100.00")), (D("-300.00"), D("-30.00")), (D("700.00"), D("70.00"))))

    def deposit_order(self):
        order = self.make_order("10", "100", policy=InvoicePolicy.DELIVERED, rep=self.rep)
        return order

    def test_deposit_paid_in_february_is_counted_on_the_day_it_is_drawn_not_before(self):
        self.plan(CommissionBasis.PAID)
        order = self.deposit_order()
        dep = order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=DAY)
        dep.post()
        self.allocate(self.receipt("300", on=datetime.date(2026, 2, 10)), dep, "300")
        self.ship(order, "10")
        inv = self.bill(order, on=datetime.date(2026, 3, 6))
        self.allocate(self.receipt("700", on=datetime.date(2026, 3, 10)), inv, "700")
        self.assertEqual(self.figure(date_from=datetime.date(2026, 3, 1), date_to=datetime.date(2026, 3, 31)),
                         (D("1000.00"), D("100.00")))

    def test_deposit_drawn_before_it_was_paid_is_counted_when_the_money_arrives(self):
        # drawn 6 March against an unpaid deposit; the 300 arrives 10 April. March must not count it, April must.
        self.plan(CommissionBasis.PAID)
        order = self.deposit_order()
        dep = order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=DAY)
        dep.post()
        self.ship(order, "10")
        inv = self.bill(order, on=datetime.date(2026, 3, 6))
        self.allocate(self.receipt("700", on=datetime.date(2026, 3, 10)), inv, "700")
        self.allocate(self.receipt("300", on=datetime.date(2026, 4, 10)), Invoice.objects.get(pk=dep.pk), "300")
        march = self.figure(date_from=datetime.date(2026, 3, 1), date_to=datetime.date(2026, 3, 31))
        april = self.figure(date_from=datetime.date(2026, 4, 1), date_to=datetime.date(2026, 4, 30))
        self.assertEqual((march, april), ((D("700.00"), D("70.00")), (D("300.00"), D("30.00"))),
                         "(March, April): cash of 300 came in April")

    def test_deposit_credited_back_in_part_counts_only_what_was_drawn(self):
        # deposit 300 paid; 100 credited back and refunded; 200 drawn on 1000 invoice; 800 paid. Collected 1000-100.. :
        # the customer paid 300+800 = 1100, got 100 back: invoice collected 200 + 800 = 1000.
        self.plan(CommissionBasis.PAID)
        order = self.deposit_order()
        dep = order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=DAY)
        dep.post()
        self.allocate(self.receipt("300", on=datetime.date(2026, 2, 10)), dep, "300")
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("100"))
        self.ship(order, "10")
        inv = self.bill(order, on=datetime.date(2026, 3, 6))
        self.allocate(self.receipt("800", on=datetime.date(2026, 3, 10)), inv, "800")
        self.assertEqual(self.figure(), (D("1000.00"), D("100.00")))


class PricingProbe(MoneyCase):
    def setUp(self):
        super().setUp()
        self.item.sale_price = D("10")
        self.item.save()
        self.box = UnitOfMeasure.objects.create(code="box", name="Box of 12", base_unit=self.uom,
                                                conversion_factor=D("12"))

    def line(self, currency, uom, quantity, on=DAY):
        order = SalesOrder.objects.create(customer=self.customer, order_date=on, currency=currency)
        return SalesOrderLine.objects.create(order=order, item=self.item, uom=uom, quantity=D(quantity),
                                             revenue_account=self.revenue)

    def test_a_default_list_in_euro_does_not_price_a_base_order_at_face_value(self):
        # EUR default list: 8 EUR each. A base-currency order (currency None) must not take 8 USD from it.
        lst = PriceList.objects.create(code="EU", name="Euro", is_default=True, currency=self.eur)
        PriceListItem.objects.create(price_list=lst, item=self.item, min_quantity=D("1"), unit_price=D("8"))
        self.assertIn(self.line(None, self.uom, "1").unit_price, (D("8.80"), D("10.00")))

    def test_euro_list_in_boxes_for_a_euro_order(self):
        lst = PriceList.objects.create(code="EU", name="Euro", is_default=True, currency=self.eur)
        PriceListItem.objects.create(price_list=lst, item=self.item, min_quantity=D("1"), unit_price=D("8"))
        PriceListItem.objects.create(price_list=lst, item=self.item, min_quantity=D("24"), unit_price=D("7"))
        self.assertEqual((self.line(self.eur, self.box, "2").unit_price, self.line(self.eur, self.box, "1").unit_price),
                         (D("84.00"), D("96.00")), "(2 boxes = 24 each at 7 -> 84 a box; 1 box at 8 -> 96)")

    def test_fractional_box_quantity_reaches_the_break_by_its_each_equivalent(self):
        # 1.9999 boxes = 23.9988 eaches: below the 24 break; 2 boxes: at it.
        lst = PriceList.objects.create(code="STD", name="Std", is_default=True, currency=Currency.objects.get(is_base=True))
        PriceListItem.objects.create(price_list=lst, item=self.item, min_quantity=D("1"), unit_price=D("10"))
        PriceListItem.objects.create(price_list=lst, item=self.item, min_quantity=D("24"), unit_price=D("9"))
        self.assertEqual((self.line(None, self.box, "1.9999").unit_price, self.line(None, self.box, "2").unit_price),
                         (D("120.00"), D("108.00")))

    def test_a_thousand_euro_units_total_what_the_base_price_gives(self):
        # 1000 at 10 USD = 10,000 USD = 9090.909.. EUR: a 9.09 unit price totals 9090.00, 0.91 short.
        line = self.line(self.eur, self.uom, "1000")
        self.assertEqual(line.net_amount(), D("9090.91"), "(1000 x per-unit rounded price)")

    def test_a_quoted_price_is_kept_when_the_rate_moves_before_the_order(self):
        from .models import Quotation, QuotationLine
        quote = Quotation.objects.create(customer=self.customer, quotation_date=DAY, currency=self.eur,
                                         valid_until=datetime.date(2026, 12, 31))
        ql = QuotationLine.objects.create(quotation=quote, item=self.item, uom=self.uom, quantity=D("10"),
                                          revenue_account=self.revenue)
        self.assertEqual(ql.unit_price, D("9.09"))
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.25"), valid_from=datetime.date(2026, 3, 5))
        order = Quotation.objects.get(pk=quote.pk).accept(order_date=datetime.date(2026, 3, 6))
        self.assertEqual(order.lines.get().unit_price, D("9.09"))


class RoleRepointProbe(MoneyCase):
    def test_a_clerk_without_the_right_cannot_repoint_and_the_manager_cannot_exceed_the_payment(self):
        call_command("setup_roles", verbosity=0)
        inv = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=inv, item=self.item, quantity=D("1"), unit_price=D("1000"),
                                   revenue_account=self.revenue)
        inv.post()
        alloc = InvoicePayment.objects.create(invoice=inv, payment=self.receipt("600"), amount=D("600"))
        user = User.objects.create_user("clerk")
        user.groups.add(Group.objects.get(name="Sales Rep"))
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.patch(f"/api/sales/invoice-payments/{alloc.pk}/", {"amount": "700"},
                                      format="json").status_code, 403)
        mgr = User.objects.create_user("ar")
        mgr.groups.add(Group.objects.get(name="AR Manager"))
        client.force_authenticate(mgr)
        over = client.patch(f"/api/sales/invoice-payments/{alloc.pk}/", {"amount": "700"}, format="json")
        self.assertEqual(over.status_code, 400, "700 of a 600 receipt")


class FractionalGiveBackProbe(MoneyCase):
    def test_a_line_credited_in_two_halves_leaves_nothing_on_the_receivable(self):
        # O199 untouched: 2.5 @ 3.33 = 8.325 -> 8.33. Two notes of 1.25 each: 4.1625 -> 4.16 twice = 8.32.
        inv = Invoice.objects.create(customer=self.customer, invoice_date=DAY, receivable_account=self.ar)
        InvoiceLine.objects.create(invoice=inv, item=self.item, quantity=D("2.5"), unit_price=D("3.33"),
                                   revenue_account=self.revenue)
        inv.post()
        line = inv.lines.get()
        for _ in range(2):
            Invoice.objects.get(pk=inv.pk).create_credit_note(quantities={line: D("1.25")})
        inv = Invoice.objects.get(pk=inv.pk)
        self.assertEqual((self.balance(self.ar), inv.amount_due()), (D("0"), D("0")), "(AR, due) after crediting 2.5 of 2.5")


class TheAuditMissesProbe(__import__("django.test", fromlist=["SimpleTestCase"]).SimpleTestCase):
    def findings(self, check, edit):
        from apps.core.management.commands.audit_invariants import Command, app_sources
        sources = app_sources()
        sources["sales"] = {path: edit(text) if path.name == "models.py" else text
                            for path, text in sources["sales"].items()}
        return [detail for _, detail in getattr(Command(), check)(["sales"], sources)]

    def test_a_filtered_walk_of_the_allocations_is_reported(self):
        found = self.findings("allocations_counted_past_a_void", lambda text: text.replace(
            "        return standing_paid(self)\n",
            "        return sum(a.amount for a in self.payment_allocations.filter(amount__gt=0))\n", 1))
        self.assertTrue(any("sales.amount_paid" in detail for detail in found), found)

    def test_an_aggregate_over_the_allocations_is_reported(self):
        found = self.findings("allocations_counted_past_a_void", lambda text: text.replace(
            "        return standing_paid(self)\n",
            "        return self.payment_allocations.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')\n", 1))
        self.assertTrue(any("sales.amount_paid" in detail for detail in found), found)

    def test_a_drawdown_off_a_company_held_in_a_variable_is_reported(self):
        found = self.findings("drawdowns_from_todays_setting", lambda text: text.replace(
            "        account = deposit.lines.get().revenue_account\n",
            "        company = Company.get()\n        account = company.customer_deposit_account\n", 1))
        self.assertTrue(any("sales.apply_deposit" in detail for detail in found), found)


class DepositThenCreditNoteProbe(CommissionProbe):
    def test_invoice_met_partly_by_a_deposit_then_credited_in_full(self):
        # 1000 = deposit 300 (drawn) + 700 paid. CN 1000 refunded 1000: commission 1000 - 1000 = 0; AR agrees.
        self.plan(CommissionBasis.PAID)
        order = self.deposit_order()
        dep = order.create_down_payment_invoice(self.ar, amount=D("300"), invoice_date=DAY)
        dep.post()
        self.allocate(self.receipt("300", on=datetime.date(2026, 2, 10)), dep, "300")
        self.ship(order, "10")
        inv = self.bill(order, on=datetime.date(2026, 3, 6))
        self.allocate(self.receipt("700", on=datetime.date(2026, 3, 10)), inv, "700")
        note = Invoice.objects.get(pk=inv.pk).create_credit_note(quantities={inv.lines.get(): D("10")})
        refund = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT,
                                        payment_date=datetime.date(2026, 4, 5), amount=D("1000"), currency=self.usd,
                                        bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        InvoicePayment.objects.create(invoice=Invoice.objects.get(pk=note.pk), payment=refund, amount=D("1000"))
        self.assertEqual(self.balance(self.ar), D("0"))
        self.assertEqual((self.balance(self.deposits), self.figure()), (D("0"), (D("0.00"), D("0.00"))),
                         "(Deposits, (basis, commission)) after the whole sale was refunded")
