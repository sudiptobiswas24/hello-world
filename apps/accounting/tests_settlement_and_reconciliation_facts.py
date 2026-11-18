"""
g22 review of fix_money: probes that assert what SHOULD be true, written to fail on the tip d554c46 where it is
not. Each test states one claim about money. Failures are printed as FAIL lines with the numbers.
"""
import datetime
import itertools
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting import tests_fx_settlement as base
from apps.accounting.models import (Account, AccountType, BankStatement, BankStatementLine, Payment,
                                    PaymentDirection)
from apps.core.models import Currency, ExchangeRate, PaymentTerms
from apps.core.tests_claims import say

D = Decimal
JAN5 = datetime.date(2026, 1, 5)
FEB1, MAR1 = datetime.date(2026, 2, 1), datetime.date(2026, 3, 1)


class AllocationsInEveryOrder(base.FxSettlementTestCase):
    """O209: control account clears and FX holds exactly invoice base less the receipts' bases, in every order."""

    invoice = base.PartSettlementTakesWhatIsLeftTests.invoice
    bill = base.PartSettlementTakesWhatIsLeftTests.bill

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=FEB1)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.15012"), valid_from=MAR1)

    def fx(self):
        return self.balance(self.loss) + self.balance(self.gain)

    def receipts(self, amounts, direction=PaymentDirection.RECEIPT):
        days = [datetime.date(2026, 2, 15), datetime.date(2026, 3, 15), datetime.date(2026, 2, 20)]
        return [self.euro_payment(direction, a, on=days[i % 3]) for i, a in enumerate(amounts)]

    def test_every_order_of_delete_and_apply_again_clears_the_receivable(self):
        from apps.sales.models import InvoicePayment
        bad = []
        amounts = ("30", "30", "40.09")
        runs = 0
        for apply_order in itertools.permutations(range(3)):
            for redo in [(k,) for k in range(3)] + [tuple(p) for p in itertools.permutations(range(3))]:
                runs += 1
                ar0, fx0 = self.balance(self.ar), self.fx()
                inv = self.invoice("100.09")
                pays = self.receipts(amounts)
                rows = {}
                for i in apply_order:
                    rows[i] = InvoicePayment.objects.create(invoice=inv, payment=pays[i], amount=D(amounts[i]))
                for i in redo:
                    rows[i].delete()
                for i in redo[::-1] if len(redo) > 1 else redo:
                    rows[i] = InvoicePayment.objects.create(invoice=inv, payment=pays[i], amount=D(amounts[i]))
                booked = D("112.45")
                paid_base = sum((p.base_amount() for p in pays), D("0"))
                residue = self.balance(self.ar) - ar0
                fx = self.fx() - fx0
                if residue != 0 or fx != booked - paid_base or inv.amount_due() != 0:
                    bad.append((apply_order, redo, str(residue), str(fx), str(booked - paid_base)))
        say(not bad, f"O209 sales: AR clears and FX = invoice base - receipts base over {runs} orders", str(bad[:3]))
        self.assertEqual(bad, [])

    def test_every_order_on_the_payable_side(self):
        from apps.purchasing.models import BillPayment
        bad = []
        amounts = ("30", "30", "40.09")
        runs = 0
        for apply_order in itertools.permutations(range(3)):
            for redo in [(k,) for k in range(3)] + [tuple(p) for p in itertools.permutations(range(3))]:
                runs += 1
                ap0, fx0 = self.balance(self.ap), self.fx()
                bill = self.bill("100.09")
                pays = self.receipts(amounts, PaymentDirection.DISBURSEMENT)
                rows = {}
                for i in apply_order:
                    rows[i] = BillPayment.objects.create(bill=bill, payment=pays[i], amount=D(amounts[i]))
                for i in redo:
                    rows[i].delete()
                for i in redo[::-1] if len(redo) > 1 else redo:
                    rows[i] = BillPayment.objects.create(bill=bill, payment=pays[i], amount=D(amounts[i]))
                paid_base = sum((p.base_amount() for p in pays), D("0"))
                residue = self.balance(self.ap) - ap0
                # a payable booked at 112.45 and settled for paid_base: a gain of booked - paid
                fx = self.fx() - fx0
                if residue != 0 or fx != -(D("112.45") - paid_base) or bill.amount_due() != 0:
                    bad.append((apply_order, redo, str(residue), str(fx)))
        say(not bad, f"O209 purchasing: AP clears and FX = -(bill base - payments base) over {runs} orders", str(bad[:3]))
        self.assertEqual(bad, [])

    def test_one_receipt_spread_over_three_invoices_every_order(self):
        from apps.sales.models import InvoicePayment
        bad = []
        for apply_order in itertools.permutations(range(3)):
            for k in range(3):
                ar0, fx0 = self.balance(self.ar), self.fx()
                invs = [self.invoice(a) for a in ("0.07", "1.57", "98.45")]
                receipt = self.euro_payment(PaymentDirection.RECEIPT, "100.09", on=datetime.date(2026, 3, 15))
                rows = {}
                for i in apply_order:
                    rows[i] = InvoicePayment.objects.create(invoice=invs[i], payment=receipt, amount=invs[i].total())
                rows[k].delete()
                rows[k] = InvoicePayment.objects.create(invoice=invs[k], payment=receipt, amount=invs[k].total())
                booked = sum((D(a) for a in ("0.07", "1.57", "98.45")), D("0"))
                residue = self.balance(self.ar) - ar0
                fx = self.fx() - fx0
                expected = sum((i.journal_entry.lines.get(account=self.ar).debit for i in invs), D("0")) - receipt.base_amount()
                if residue != 0 or fx != expected:
                    bad.append((apply_order, k, str(residue), str(fx), str(expected)))
        say(not bad, "O209 one receipt over three invoices, delete+reapply any: AR clears, FX exact", str(bad[:3]))
        self.assertEqual(bad, [])

    def test_receipt_credit_note_receipt_in_the_middle_clears(self):
        # receipt, then a credit note on two of three lines, then the receipt for the rest, then delete the first
        # receipt's allocation and apply it again.
        from apps.sales.models import InvoicePayment, SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        for price in ("10.01", "20.78", "25.37"):
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                          unit_price=D(price), revenue_account=self.revenue)
        order.confirm()
        inv = order.create_invoice(self.ar, invoice_date=JAN5)
        inv.post()
        r1 = self.euro_payment(PaymentDirection.RECEIPT, "10.00", on=datetime.date(2026, 2, 15))
        a1 = InvoicePayment.objects.create(invoice=inv, payment=r1, amount=D("10.00"))
        first, second, _ = inv.lines.order_by("pk")
        inv.create_credit_note(quantities={first: D("1")}, on_date=datetime.date(2026, 2, 20))
        r2 = self.euro_payment(PaymentDirection.RECEIPT, str(inv.amount_due()), on=datetime.date(2026, 3, 15))
        InvoicePayment.objects.create(invoice=inv, payment=r2, amount=inv.amount_due())
        due_a, ar_a = inv.amount_due(), self.balance(self.ar)
        a1.delete()
        InvoicePayment.objects.create(invoice=inv, payment=r1, amount=D("10.00"))
        due_b, ar_b = inv.amount_due(), self.balance(self.ar)
        say(ar_a == 0 and ar_b == 0 and due_a == 0 and due_b == 0, "O209 receipt/note/receipt then re-apply the first: AR 0",
            f"due {due_a}/{due_b}, AR {ar_a}/{ar_b}")
        self.assertEqual((ar_a, ar_b, due_a, due_b), (0, 0, 0, 0))

    def test_a_settlement_discount_taken_last_clears_the_receivable_for_many_amounts(self):
        # 2/10 on a euro invoice: the discount books round(2% x rate), the receipts book their slices, and the
        # last of the base must be what the discount took. Scan amounts for a paisa left on AR.
        from apps.sales.models import InvoicePayment
        Company = __import__("apps.core.models", fromlist=["Company"]).Company
        disc = Account.objects.create(code="5990", name="Discount allowed", account_type=AccountType.EXPENSE)
        Company.objects.update(settlement_discount_account=disc)
        terms = PaymentTerms.objects.create(code="2-10", name="2/10 net 30", net_days=30,
                                            discount_percent=D("2"), discount_days=30)
        bad = []
        import random
        rnd = random.Random(7)
        amounts = [D(str(round(rnd.uniform(10, 3000), 2))) for _ in range(120)]
        from apps.sales.models import Invoice
        for amount in amounts:
            ar0 = self.balance(self.ar)
            inv = self.invoice(str(amount))
            Invoice.objects.filter(pk=inv.pk).update(payment_terms=terms)
            inv.refresh_from_db()
            discount = inv.settlement_discount()
            first = (amount - discount) / 2
            first = first.quantize(D("0.01"))
            second = amount - discount - first
            pays = [self.euro_payment(PaymentDirection.RECEIPT, str(first), on=datetime.date(2026, 2, 15)),
                    self.euro_payment(PaymentDirection.RECEIPT, str(second), on=datetime.date(2026, 3, 15))]
            for pay, part in zip(pays, (first, second)):
                InvoicePayment.objects.create(invoice=inv, payment=pay, amount=part)
            inv.apply_settlement_discount(on_date=datetime.date(2026, 3, 16), force=True)
            inv.refresh_from_db()
            residue = self.balance(self.ar) - ar0
            if residue != 0 or inv.amount_due() != 0:
                bad.append((str(amount), str(residue), str(inv.amount_due())))
        say(not bad, f"O109/O209 AR clears when a discount is taken last, {len(amounts)} amounts", f"{len(bad)} bad: {bad[:4]}")
        self.assertEqual(bad, [])


class SettlementDiscountOnAPartPayment(base.FxSettlementTestCase):
    """O109: a part payment within the days earns the discount on what it paid, not on the whole invoice."""

    invoice = base.PartSettlementTakesWhatIsLeftTests.invoice
    bill = base.PartSettlementTakesWhatIsLeftTests.bill

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        from apps.core.models import Company
        self.disc = Account.objects.create(code="5990", name="Discount allowed", account_type=AccountType.EXPENSE)
        self.recv = Account.objects.create(code="4991", name="Discount received", account_type=AccountType.INCOME)
        Company.objects.update(settlement_discount_account=self.disc, settlement_discount_received_account=self.recv)
        self.terms = PaymentTerms.objects.create(code="2-10", name="2/10 net 30", net_days=30,
                                                 discount_percent=D("2"), discount_days=10)

    def test_a_tenth_paid_within_the_days_does_not_earn_the_discount_on_the_whole_invoice(self):
        from apps.sales.models import Invoice, InvoicePayment
        inv = self.invoice("1000")
        Invoice.objects.filter(pk=inv.pk).update(payment_terms=self.terms)
        inv.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.RECEIPT, "100", on=datetime.date(2026, 1, 10))
        InvoicePayment.objects.create(invoice=inv, payment=pay, amount=D("100"))
        try:
            inv.apply_settlement_discount(payment=pay)
            taken = inv.settlement_discount_amount
        except ValidationError as exc:
            taken = D("0")
            print("   refused:", exc.messages)
        # Decided (integrator, review F2): 100 within 2/10 settles 100/0.98 and earns 100 x 0.02 / 0.98 = 2.04;
        # the customer owes 1,000 - 100 - 2.04 = 897.96 (calc_fix_money/g22/f2.py).
        say(taken == D("2.04"), "O109 sales: discount on a 100 part payment of a 1,000 invoice is 2.04", f"taken {taken}")
        self.assertEqual((taken, inv.amount_due()), (D("2.04"), D("897.96")))

    def test_the_same_on_the_bill(self):
        from apps.purchasing.models import Bill, BillPayment
        bill = self.bill("1000")
        Bill.objects.filter(pk=bill.pk).update(payment_terms=self.terms)
        bill.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=datetime.date(2026, 1, 10))
        BillPayment.objects.create(bill=bill, payment=pay, amount=D("100"))
        try:
            bill.take_settlement_discount(payment=pay)
            taken = bill.settlement_discount_amount
        except ValidationError as exc:
            taken = D("0")
            print("   refused:", exc.messages)
        say(taken == D("2.04"), "O109 purchasing: discount on a 100 part payment of a 1,000 bill is 2.04", f"taken {taken}")
        self.assertEqual((taken, bill.amount_due()), (D("2.04"), D("897.96")))

    def test_a_payment_outside_the_days_earns_nothing(self):
        from apps.sales.models import Invoice, InvoicePayment
        inv = self.invoice("1000")
        Invoice.objects.filter(pk=inv.pk).update(payment_terms=self.terms)
        inv.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.RECEIPT, "980", on=datetime.date(2026, 2, 15))
        InvoicePayment.objects.create(invoice=inv, payment=pay, amount=D("980"))
        with self.assertRaises(ValidationError):
            inv.apply_settlement_discount(payment=pay)
        say(True, "O109 sales: a receipt outside the days is refused")

    def test_a_payment_in_the_days_applied_to_another_invoice_is_refused(self):
        from apps.sales.models import Invoice, InvoicePayment
        inv, other = self.invoice("1000"), self.invoice("500")
        Invoice.objects.filter(pk__in=[inv.pk, other.pk]).update(payment_terms=self.terms)
        inv.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.RECEIPT, "490", on=datetime.date(2026, 1, 10))
        InvoicePayment.objects.create(invoice=other, payment=pay, amount=D("490"))
        with self.assertRaises(ValidationError):
            inv.apply_settlement_discount(payment=pay)
        say(True, "O109 sales: a receipt applied elsewhere earns no discount here")

    # O250, worked by hand in /home/user/work/g48_calc/scenarios.py: 1,000 at 2/10, rate d = 2%. Every payment
    # inside the days earns, on the running total of what the payments that earned here settle: round(total x
    # d / (1 - d)) less what they took, no more than the discount left nor what is due.

    def discounted(self, side="sales"):
        from apps.purchasing.models import Bill
        from apps.sales.models import Invoice
        document = self.invoice("1000") if side == "sales" else self.bill("1000")
        model = Invoice if side == "sales" else Bill
        model.objects.filter(pk=document.pk).update(payment_terms=self.terms)
        return model.objects.get(pk=document.pk)

    def paid(self, document, *amounts, on=datetime.date(2026, 1, 10)):
        from apps.purchasing.models import BillPayment
        from apps.sales.models import InvoicePayment
        receipt = not hasattr(document, "vendor")
        payments = []
        for amount in amounts:
            pay = self.euro_payment(PaymentDirection.RECEIPT if receipt else PaymentDirection.DISBURSEMENT, amount, on=on)
            if receipt:
                InvoicePayment.objects.create(invoice=document, payment=pay, amount=D(amount))
            else:
                BillPayment.objects.create(bill=document, payment=pay, amount=D(amount))
            payments.append(pay)
        return payments

    def earn(self, document, payment):
        take = document.apply_settlement_discount if hasattr(document, "customer") else document.take_settlement_discount
        take(payment=payment)
        document.refresh_from_db()
        return document.discounts.get(payment=payment).amount

    def test_each_payment_inside_the_days_earns_its_share(self):
        inv = self.discounted()
        p1, p2 = self.paid(inv, "490", "490")
        earned = [self.earn(inv, p2), self.earn(inv, p1)]
        say(inv.amount_due() == 0, "O250 sales: 490 + 490 within the days earn 10 + 10 and settle 1,000",
            f"earned {earned} due {inv.amount_due()}")
        self.assertEqual((earned, inv.settlement_discount_amount, inv.amount_due()),
                         ([D("10.00"), D("10.00")], D("20.00"), D("0.00")))

    def test_a_payment_earns_once(self):
        inv = self.discounted()
        (pay,) = self.paid(inv, "490")
        self.earn(inv, pay)
        with self.assertRaisesMessage(ValidationError, "earned its settlement discount"):
            inv.apply_settlement_discount(payment=pay)
        inv.refresh_from_db()
        self.assertEqual((inv.settlement_discount_amount, inv.discounts.count()), (D("10.00"), 1))

    def test_three_payments_earn_on_their_running_total_and_leave_no_paisa(self):
        # Each share rounded on its own: 8.16 three times and 0.01 owing that nothing explains.
        inv = self.discounted()
        payments = self.paid(inv, "400", "400", "180")
        earned = [self.earn(inv, pay) for pay in payments]
        self.assertEqual((earned, inv.amount_due()), ([D("8.16"), D("8.17"), D("3.67")], D("0.00")))

    def test_a_payment_after_the_days_earns_nothing_and_its_share_is_owed(self):
        inv = self.discounted()
        (early,) = self.paid(inv, "490")
        (late,) = self.paid(inv, "490", on=datetime.date(2026, 2, 15))
        self.assertEqual(self.earn(inv, early), D("10.00"))
        with self.assertRaisesMessage(ValidationError, "after the discount days"):
            inv.apply_settlement_discount(payment=late)
        inv.refresh_from_db()
        self.assertEqual(inv.amount_due(), D("10.00"))

    def test_a_returned_payment_takes_back_its_own_discount_and_no_other(self):
        for side in ("sales", "purchasing"):
            with self.subTest(side=side):
                document = self.discounted(side)
                p1, p2 = self.paid(document, "490", "490")
                self.earn(document, p1)
                self.earn(document, p2)
                p1.void(memo="Returned unpaid", on_date=datetime.date(2026, 1, 20))
                document.refresh_from_db()
                rows = {row.payment_id: (row.amount, row.withdrawn_amount) for row in document.discounts.all()}
                # p1's 490 never arrived and nor did its 10; p2 paid inside the days and keeps its 10.
                self.assertEqual(rows, {p1.pk: (D("10.00"), D("10.00")), p2.pk: (D("10.00"), D("0.00"))})
                self.assertEqual((document.settlement_discount_withdrawn, document.amount_due()),
                                 (D("10.00"), D("500.00")))

    def test_the_bill_earns_the_same(self):
        bill = self.discounted("purchasing")
        p1, p2 = self.paid(bill, "490", "490")
        earned = [self.earn(bill, p1), self.earn(bill, p2)]
        self.assertEqual((earned, bill.amount_due()), ([D("10.00"), D("10.00")], D("0.00")))

    def test_the_history_and_the_statement_show_each_discount_on_its_day(self):
        from apps.sales.models import customer_statement
        inv = self.discounted()
        (p1,) = self.paid(inv, "490", on=datetime.date(2026, 1, 8))
        (p2,) = self.paid(inv, "490", on=datetime.date(2026, 1, 10))
        self.earn(inv, p1)
        self.earn(inv, p2)
        discounts = [(day, amount) for day, amount, _entries, words in inv.settled_otherwise_on()
                     if words == "a settlement discount"]
        self.assertEqual(discounts, [(datetime.date(2026, 1, 8), D("10.00")), (datetime.date(2026, 1, 10), D("10.00"))])
        statement = customer_statement(self.customer, as_of=datetime.date(2026, 12, 31))
        self.assertEqual([(entry.date, entry.credit) for entry in statement["entries"] if entry.kind == "Settlement discount"],
                         [(datetime.date(2026, 1, 8), D("10.00")), (datetime.date(2026, 1, 10), D("10.00"))])

    def test_a_credit_note_of_the_whole_undoes_both_discounts(self):
        inv = self.discounted()
        p1, p2 = self.paid(inv, "490", "490")
        self.earn(inv, p1)
        self.earn(inv, p2)
        note = inv.create_credit_note()
        inv.refresh_from_db()
        self.assertEqual((note.reversed_discount, note.refund_due(), inv.settlement_discount_standing()),
                         (D("20.00"), D("980.00"), D("0.00")))
        self.assertEqual(self.balance(self.disc), D("0.00"))

    # O599, worked by hand in /home/user/work/g49_calc/scenarios.py: what a payment earned rests on what it applied.
    # Re-sized, re-pointed or deleted, its allocation takes the discount back on the day of the change; still on the
    # same document, the payment earns again on what it applies now. 5 at 1.12345 is 5.62 in the books.

    def standing_of(self, document, payment):
        document.refresh_from_db()
        return [row.amount for row in document.discounts.filter(payment=payment, withdrawal_entry__isnull=True)]

    def facts(self, document, payment):
        return self.standing_of(document, payment), document.settlement_discount_standing(), document.amount_due()

    def test_an_allocation_cut_earns_again_on_what_it_applies_now(self):
        for side in ("sales", "purchasing"):
            with self.subTest(side=side):
                document = self.discounted(side)
                (pay,) = self.paid(document, "490")
                self.earn(document, pay)
                allocation = document.payment_allocations.get(payment=pay)
                allocation.amount = D("245")
                allocation.save()
                self.assertEqual(self.facts(document, pay), ([D("5.00")], D("5.00"), D("750.00")))
        self.assertEqual((self.balance(self.disc), self.balance(self.recv)), (D("5.62"), D("-5.62")))

    def test_an_allocation_deleted_takes_its_discount_back(self):
        for side in ("sales", "purchasing"):
            with self.subTest(side=side):
                document = self.discounted(side)
                (pay,) = self.paid(document, "490")
                self.earn(document, pay)
                document.payment_allocations.get(payment=pay).delete()
                self.assertEqual(self.facts(document, pay), ([], D("0.00"), D("1000.00")))
        self.assertEqual((self.balance(self.disc), self.balance(self.recv)), (D("0.00"), D("0.00")))

    def test_a_cut_beside_another_payments_discount_earns_on_the_running_total(self):
        inv = self.discounted()
        p1, p2 = self.paid(inv, "490", "490")
        self.earn(inv, p1)
        self.earn(inv, p2)
        allocation = inv.payment_allocations.get(payment=p1)
        allocation.amount = D("245")
        allocation.save()
        # 735 settled earns 15.00 at 2/10; p2 holds 10.00 of it, so p1 earns 5.00 and 250.00 is owed.
        self.assertEqual((self.standing_of(inv, p1), self.standing_of(inv, p2), inv.amount_due()),
                         ([D("5.00")], [D("10.00")], D("250.00")))
        self.assertEqual(self.balance(self.disc), D("16.85"))

    def test_an_allocation_moved_to_another_invoice_takes_its_discount_back_and_earns_there_when_taken(self):
        x, y = self.discounted(), self.discounted()
        (pay,) = self.paid(x, "490")
        self.earn(x, pay)
        allocation = x.payment_allocations.get(payment=pay)
        allocation.invoice = y
        allocation.save()
        self.assertEqual((self.facts(x, pay), self.facts(y, pay)),
                         (([], D("0.00"), D("1000.00")), ([], D("0.00"), D("510.00"))))
        self.assertEqual((self.earn(y, pay), y.amount_due()), (D("10.00"), D("500.00")))

    def test_an_allocation_raised_earns_on_the_whole_of_it(self):
        from apps.sales.models import InvoicePayment
        inv = self.discounted()
        pay = self.euro_payment(PaymentDirection.RECEIPT, "980", on=datetime.date(2026, 1, 10))
        allocation = InvoicePayment.objects.create(invoice=inv, payment=pay, amount=D("490"))
        self.earn(inv, pay)
        allocation.amount = D("980")
        allocation.save()
        self.assertEqual(self.facts(inv, pay), ([D("20.00")], D("20.00"), D("0.00")))
        self.assertEqual(inv.discounts.filter(payment=pay).count(), 2)
        with self.assertRaisesMessage(ValidationError, "earned its settlement discount"):
            inv.apply_settlement_discount(payment=pay)
        self.assertEqual(self.balance(self.disc), D("22.47"))

    def test_an_allocation_saved_unchanged_leaves_its_discount(self):
        inv = self.discounted()
        (pay,) = self.paid(inv, "490")
        self.earn(inv, pay)
        inv.payment_allocations.get(payment=pay).save()
        self.assertEqual((self.facts(inv, pay), inv.discounts.count()), (([D("10.00")], D("10.00"), D("500.00")), 1))

    def test_a_payment_returned_after_its_cut_takes_back_what_it_earns_now(self):
        from django.utils import timezone
        for side in ("sales", "purchasing"):
            with self.subTest(side=side):
                document = self.discounted(side)
                (pay,) = self.paid(document, "490")
                self.earn(document, pay)
                allocation = document.payment_allocations.get(payment=pay)
                allocation.amount = D("245")
                allocation.save()
                pay.void(memo="Returned unpaid", on_date=timezone.localdate())
                self.assertEqual(self.facts(document, pay), ([], D("0.00"), D("1000.00")))
        self.assertEqual((self.balance(self.disc), self.balance(self.recv)), (D("0.00"), D("0.00")))

    def test_an_allocation_deleted_over_the_api_by_the_ar_manager_takes_its_discount_back(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        inv = self.discounted()
        (pay,) = self.paid(inv, "490")
        self.earn(inv, pay)
        user = User.objects.create_user("ar")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        allocation = inv.payment_allocations.get(payment=pay)
        response = client.patch(f"/api/sales/invoice-payments/{allocation.pk}/", {"amount": "245"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.facts(inv, pay), ([D("5.00")], D("5.00"), D("750.00")))
        response = client.delete(f"/api/sales/invoice-payments/{allocation.pk}/")
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(self.facts(inv, pay), ([], D("0.00"), D("1000.00")))

    def test_an_allocation_moved_over_the_api_holds_both_invoices_in_order_and_takes_its_discount_back(self):
        # The edit holds what save() will before the row (lock_for_change): the invoice it leaves beside the one
        # it joins, in key order. Appended after it, a move from the earlier invoice to the later was a 500.
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        x, y = self.discounted(), self.discounted()
        (pay,) = self.paid(x, "490")
        self.earn(x, pay)
        user = User.objects.create_user("ar")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        allocation = x.payment_allocations.get(payment=pay)
        response = client.patch(f"/api/sales/invoice-payments/{allocation.pk}/", {"invoice": y.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((self.facts(x, pay), self.facts(y, pay)),
                         (([], D("0.00"), D("1000.00")), ([], D("0.00"), D("510.00"))))


class AEEFCAccount(base.FxSettlementTestCase):
    """O24: an EEFC account moves only its currency; every way money moves through it."""

    def test_an_inr_like_base_payment_through_a_euro_account_is_refused_in_words(self):
        eefc = Account.objects.create(code="1020", name="EEFC EUR", account_type=AccountType.ASSET,
                                      holds_money=True, currency=self.eur)
        pay = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                     payment_date=datetime.date(2026, 2, 15), amount=D("100"), currency=self.usd,
                                     bank_account=eefc, counterpart_account=self.ar)
        try:
            pay.post()
            refused = ""
        except ValidationError as exc:
            refused = " ".join(exc.messages)
        say("EUR" in refused, "O24 a base-currency payment through a euro account is refused naming the currency", refused or "posted")
        self.assertIn("EUR", refused)

    def test_other_doors_into_an_account_kept_in_euros_a_journal_entry(self):
        # Other doors into an account kept in euros: a statement line posted to it, a plain journal entry.
        from apps.accounting.models import JournalEntry, JournalLine
        eefc = Account.objects.create(code="1020", name="EEFC EUR", account_type=AccountType.ASSET,
                                      holds_money=True, currency=self.eur)
        entry = JournalEntry.objects.create(date=JAN5, memo="probe")
        JournalLine.objects.create(entry=entry, account=eefc, debit=D("100"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=D("100"))
        try:
            entry.post()
            refused = ""
        except ValidationError as exc:
            refused = " ".join(exc.messages)
        say(bool(refused), "O24 (mirror) a journal entry in base money on an account kept in euros is refused", refused or "posted")
        self.assertIn("is kept in EUR", refused)


class BouncedChequeAcrossStatements(base.FxSettlementTestCase):
    """O230: the reconciliation foots at every step, on several statements and months."""

    def setUp(self):
        super().setUp()
        self.bank.save()

    def statement(self, start, end, opening, closing):
        return BankStatement.objects.create(bank_account=self.bank, start_date=start, end_date=end,
                                            opening_balance=D(opening), closing_balance=D(closing))

    def line(self, statement, amount, on):
        return BankStatementLine.objects.create(statement=statement, date=on, amount=D(amount))

    def cheque(self, on):
        pay = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=on,
                                     amount=D("1000"), currency=self.usd, bank_account=self.bank,
                                     counterpart_account=self.ar)
        pay.post()
        return pay

    def test_void_dated_before_the_deposits_statement_foots_on_both(self):
        # Paid 20 Jan, voided 25 Jan (before the bank ever showed it), the bank shows it 3 Feb and takes it back 10 Feb.
        cheque = self.cheque(datetime.date(2026, 1, 20))
        jan = self.statement(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), "0", "0")
        cheque.void(memo="Returned", on_date=datetime.date(2026, 1, 25))
        r1 = jan.reconciliation()
        feb = self.statement(datetime.date(2026, 2, 1), datetime.date(2026, 2, 28), "0", "0")
        dep = self.line(feb, "1000", datetime.date(2026, 2, 3))
        ret = self.line(feb, "-1000", datetime.date(2026, 2, 10))
        ret.match(cheque)   # the bank's return first: a voided payment is shown to have reached the bank only so
        dep.match(cheque)
        r2 = feb.reconciliation()
        r1b = jan.reconciliation()
        ok = (r1["difference"] == 0 and r2["difference"] == 0 and r1b["difference"] == 0 and not r2["unreturned"]
              and feb.is_reconciled())
        say(ok, "O230 void before the deposit's statement: January, February, January again all foot",
            f"jan {r1['difference']} feb {r2['difference']} jan-again {r1b['difference']}")
        self.assertTrue(ok)

    def test_a_return_line_dated_before_a_late_void_leaves_a_named_item(self):
        # Deposited 5 Jan, bank returned it 28 Jan (closing 0), the void is booked on 2 Feb.
        cheque = self.cheque(datetime.date(2026, 1, 5))
        jan = self.statement(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), "0", "0")
        self.line(jan, "1000", datetime.date(2026, 1, 5)).match(cheque)
        cheque.void(memo="Returned unpaid", on_date=datetime.date(2026, 2, 2))
        ret = self.line(jan, "-1000", datetime.date(2026, 1, 28))
        try:
            ret.match(cheque)
            matched = True
        except ValidationError as exc:
            matched = False
            print("   return refused:", exc.messages)
        r = jan.reconciliation()
        ok = (not matched) or (r["difference"] == 0)
        say(ok, "O230 a return line (28 Jan) matched to a payment voided 2 Feb: January foots or the match is refused",
            f"matched {matched} difference {r['difference']} items {len(r['unpresented'])}+{len(r['unreturned'])}")
        self.assertTrue(ok)

    def test_unmatching_each_line_again_keeps_the_ledger_side_whole(self):
        cheque = self.cheque(datetime.date(2026, 1, 5))
        jan = self.statement(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), "0", "0")
        dep = self.line(jan, "1000", datetime.date(2026, 1, 5))
        dep.match(cheque)
        cheque.void(memo="Returned unpaid", on_date=datetime.date(2026, 1, 8))
        ret = self.line(jan, "-1000", datetime.date(2026, 1, 8))
        ret.match(cheque)
        steps = [("matched both", jan.reconciliation())]
        dep.unmatch()
        steps.append(("deposit unmatched", jan.reconciliation()))
        dep.match(cheque)
        steps.append(("deposit matched again", jan.reconciliation()))
        try:
            ret.unmatch()
            steps.append(("return unmatched (deposit still matched)", jan.reconciliation()))
        except ValidationError as exc:
            steps.append(("return unmatch refused: " + " ".join(exc.messages), jan.reconciliation()))
        for label, r in steps:
            unresolved = len(r["unresolved_lines"])
            explained = r["difference"] == 0 or unresolved > 0
            say(explained, "O230 unmatch/match steps: " + label, f"difference {r['difference']}, unresolved {unresolved}, "
                f"items {len(r['unpresented'])}/{len(r['unreturned'])}")
            self.assertTrue(explained, label)
        # And once everything is matched again the statement closes.
        if ret.returned_payment_id is None:
            ret.refresh_from_db()
            if ret.returned_payment_id is None:
                ret.match(cheque)
        self.assertTrue(jan.is_reconciled())

    def test_two_cheques_one_bounces_in_march_statements_foot_each_month(self):
        c1 = self.cheque(datetime.date(2026, 1, 5))
        c2 = self.cheque(datetime.date(2026, 1, 6))
        jan = self.statement(datetime.date(2026, 1, 1), datetime.date(2026, 1, 31), "0", "2000")
        self.line(jan, "1000", datetime.date(2026, 1, 5)).match(c1)
        self.line(jan, "1000", datetime.date(2026, 1, 6)).match(c2)
        ok_jan = jan.reconciliation()["difference"] == 0
        jan.close()
        c1.void(memo="bounced", on_date=datetime.date(2026, 2, 27))
        feb = self.statement(datetime.date(2026, 2, 1), datetime.date(2026, 2, 28), "2000", "2000")
        r_feb = feb.reconciliation()
        mar = self.statement(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31), "2000", "1000")
        self.line(mar, "-1000", datetime.date(2026, 3, 2)).match(c1)
        r_mar = mar.reconciliation()
        r_feb_again = feb.reconciliation()
        ok = ok_jan and r_feb["difference"] == 0 and r_mar["difference"] == 0 and r_feb_again["difference"] == 0
        say(ok, "O230 bounce voided in Feb, return in March: Jan, Feb, Mar and Feb-again foot",
            f"feb {r_feb['difference']} mar {r_mar['difference']} feb-again {r_feb_again['difference']}")
        self.assertTrue(ok)


class TheDiscountAndTheBouncedReceipt(base.FxSettlementTestCase):
    """O109 reverse path: the receipt that earned a settlement discount is voided (a bounced cheque)."""

    invoice = base.PartSettlementTakesWhatIsLeftTests.invoice

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        from apps.core.models import Company
        self.disc = Account.objects.create(code="5990", name="Discount allowed", account_type=AccountType.EXPENSE)
        Company.objects.update(settlement_discount_account=self.disc)
        self.terms = PaymentTerms.objects.create(code="2-10", name="2/10 net 30", net_days=30,
                                                 discount_percent=D("2"), discount_days=10)

    def test_voiding_the_receipt_that_earned_the_discount_does_not_leave_the_discount_standing(self):
        from apps.sales.models import Invoice, InvoicePayment
        inv = self.invoice("1000")
        Invoice.objects.filter(pk=inv.pk).update(payment_terms=self.terms)
        inv.refresh_from_db()
        pay = self.euro_payment(PaymentDirection.RECEIPT, "980", on=datetime.date(2026, 1, 10))
        InvoicePayment.objects.create(invoice=inv, payment=pay, amount=D("980"))
        inv.apply_settlement_discount(payment=pay)
        inv.refresh_from_db()
        due_before = inv.amount_due()
        try:
            pay.void(memo="Bounced", on_date=datetime.date(2026, 1, 20))
            voided = True
        except ValidationError as exc:
            voided = False
            print("   void refused:", exc.messages)
        inv.refresh_from_db()
        due_after = inv.amount_due()
        # The cheque never paid: the customer owes the 1,000 again, or the void waits for the discount to be
        # withdrawn. Left as 980 owed, a customer pays 980 in March and keeps a 2% discount for a bounced cheque.
        ok = (not voided) or due_after == D("1000.00")
        say(ok, "O109 reverse: after the earning receipt bounces the invoice owes 1,000 again (or the void is refused)",
            f"voided {voided}, due {due_before} -> {due_after}, discount standing {inv.settlement_discount_standing()}")
        self.assertTrue(ok)


class AnAdvanceInEuros(base.FxSettlementTestCase):
    """O209/O243: an advance in euros drawn down on posting, then a receipt, deleted and applied again."""

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=FEB1)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.15012"), valid_from=MAR1)
        from apps.core.models import Company
        self.deposits = Account.objects.create(code="2400", name="Customer advances", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=self.deposits)

    def test_the_receivable_and_the_deposit_account_clear_whatever_is_deleted_and_applied_again(self):
        from apps.sales.models import InvoicePayment, SalesOrder, SalesOrderLine
        bad = []
        for percent, price in ((40, "100.09"), (33, "777.77"), (10, "13.37"), (70, "1234.56")):
            ar0, dep0 = self.balance(self.ar), self.balance(self.deposits)
            order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                          unit_price=D(price), revenue_account=self.revenue)
            order.confirm()
            deposit = order.create_down_payment_invoice(self.ar, percent=percent, invoice_date=JAN5)
            deposit.post()
            inv = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 2, 15))
            inv.post()
            inv.refresh_from_db()
            rest = inv.amount_due()
            half = (rest / 2).quantize(D("0.01"))
            pays = [self.euro_payment(PaymentDirection.RECEIPT, str(half), on=datetime.date(2026, 3, 15)),
                    self.euro_payment(PaymentDirection.RECEIPT, str(rest - half), on=datetime.date(2026, 2, 20))]
            rows = [InvoicePayment.objects.create(invoice=inv, payment=p, amount=a) for p, a in zip(pays, (half, rest - half))]
            rows[0].delete()
            InvoicePayment.objects.create(invoice=inv, payment=pays[0], amount=half)
            # the advance itself is an invoice on AR that nobody paid: take it out of the sum
            deposit.refresh_from_db()
            owed_on_deposit = deposit.amount_due()
            ar_net = self.balance(self.ar) - ar0 - (deposit.journal_entry.lines.get(account=self.ar).debit - 0)
            if inv.amount_due() != 0 or self.balance(self.deposits) - dep0 != 0 or ar_net != 0:
                bad.append((percent, price, str(inv.amount_due()), str(self.balance(self.deposits) - dep0), str(ar_net)))
        say(not bad, "O209 EUR advance drawn on posting then a receipt split, one deleted and applied again: AR (net of the unpaid advance) and deposits 0",
            str(bad))
        self.assertEqual(bad, [])
