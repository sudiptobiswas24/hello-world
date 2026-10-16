"""g22 review of fix_money: races on PostgreSQL for the discount, the advance and the statement line."""
import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import (BankStatement, BankStatementLine, JournalEntry, Payment, PaymentDirection)
from apps.core.models import PaymentTerms
from apps.e2e import tests_races as r
from apps.sales import tests_base as sales_fixture
from apps.sales import tests_review_money_probes as probes
from apps.sales.models import Invoice, InvoiceLine, InvoicePayment

D = Decimal
MAR1, MAR5 = datetime.date(2026, 3, 1), datetime.date(2026, 3, 5)


def say(ok, claim, detail=""):
    print(("PASS " if ok else "FAIL ") + claim + (f"  [{detail}]" if detail else ""))


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class MoneyRaces(r.RaceCase):
    setUp = sales_fixture.SalesTestCase.setUp
    make_order, receipt, balance = (sales_fixture.SalesTestCase.make_order, sales_fixture.SalesTestCase.receipt,
                                    sales_fixture.SalesTestCase.balance)

    def free_invoice(self, amount="1000"):
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=MAR1, receivable_account=self.ar,
                                         currency=self.usd)
        InvoiceLine.objects.create(invoice=invoice, description="Service", quantity=D("1"), unit_price=D(amount),
                                   revenue_account=self.revenue)
        return invoice

    def payment(self, amount, on=MAR5):
        payment = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=on,
                                         amount=D(amount), currency=self.usd, bank_account=self.bank,
                                         counterpart_account=self.ar)
        payment.post()
        return payment

    def test_a_discount_taken_as_the_last_of_the_invoice_is_allocated(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company
        disc = Account.objects.create(code="5990", name="Discount allowed", account_type=AccountType.EXPENSE)
        Company.objects.update(settlement_discount_account=disc)
        terms = PaymentTerms.objects.create(code="2-10", name="2/10 net 30", net_days=30, discount_percent=D("2"),
                                            discount_days=10)
        invoice = self.free_invoice("1000")
        Invoice.objects.filter(pk=invoice.pk).update(payment_terms=terms)
        invoice = Invoice.objects.get(pk=invoice.pk)
        invoice.post()
        a, b = self.payment("980"), self.payment("20")
        InvoicePayment.objects.create(invoice=invoice, payment=a, amount=D("980"))
        outcomes = r.race((JournalEntry, InvoicePayment),
                          lambda: Invoice.objects.get(pk=invoice.pk).apply_settlement_discount(payment=a),
                          lambda: InvoicePayment.objects.create(invoice_id=invoice.pk, payment_id=b.pk, amount=D("20")))
        due = Invoice.objects.get(pk=invoice.pk).amount_due()
        say(sorted(o == "done" for o in outcomes) == [False, True] and due == 0,
            "RACE discount vs allocation: exactly one of them fills the last 20 of the invoice", f"{outcomes} due {due}")
        self.once(outcomes)
        self.assertEqual(due, 0)

    def test_a_statement_line_matched_as_its_payment_is_voided(self):
        payment = self.payment("1000")
        statement = BankStatement.objects.create(bank_account=self.bank, start_date=MAR1,
                                                 end_date=datetime.date(2026, 3, 31), opening_balance=D("0"),
                                                 closing_balance=D("1000"))
        line = BankStatementLine.objects.create(statement=statement, date=MAR5, amount=D("1000"))
        outcomes = r.race((BankStatementLine, JournalEntry),
                          lambda: BankStatementLine.objects.get(pk=line.pk).match(Payment.objects.get(pk=payment.pk)),
                          lambda: Payment.objects.get(pk=payment.pk).void(memo="bounced", on_date=datetime.date(2026, 3, 20)))
        report = BankStatement.objects.get(pk=statement.pk).reconciliation()
        line.refresh_from_db()
        foots = report["difference"] == 0 or len(report["unresolved_lines"]) >= 1
        say(foots, "RACE a line matched as its payment is voided: the reconciliation foots or names the line",
            f"{outcomes} matched {line.payment_id} difference {report['difference']} unresolved {len(report['unresolved_lines'])} unreturned {len(report['unreturned'])}")
        self.assertTrue(foots)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TcsRaces(r.RaceCase):
    def test_two_goods_invoices_post_at_once_against_one_advance(self):
        made = r.fixture(self, probes.TcsOnAScrapAdvance)
        order, advance = made.order_with_advance()
        first = Invoice.objects.create(customer=made.mh, invoice_date=probes.SEP20, receivable_account=made.ar,
                                       currency=made.inr, sales_order=order)
        second = Invoice.objects.create(customer=made.mh, invoice_date=probes.SEP20, receivable_account=made.ar,
                                        currency=made.inr, sales_order=order)
        for invoice in (first, second):
            line = InvoiceLine.objects.create(invoice=invoice, item=made.scrap, quantity=D("1000"), unit_price=D("45"),
                                              revenue_account=made.revenue, order_line=order.lines.get())
            line.taxes.set(made.pair)
        outcomes = r.race((JournalEntry,), *[lambda pk=pk: Invoice.objects.get(pk=pk).post() for pk in (first.pk, second.pk)])
        from apps.sales.tcs import TcsAdvanceCount, TcsCollection
        counted = sum((c.base for c in TcsAdvanceCount.objects.all()), D("0"))
        posted = [i for i in Invoice.objects.filter(pk__in=[first.pk, second.pk]) if i.posted]
        gross = sum((i.total() - i.tcs_amount for i in posted), D("0")) + 0
        payable = made.ledger("2290")
        # 1% of what was sold: the advance's 270 plus the invoices' own, whichever of them posted
        expected = D("270") + sum((i.tcs_amount for i in posted), D("0"))
        ok = counted <= D("27000.00") and payable == expected
        say(ok, "RACE two goods invoices on one advance never count it twice", f"{outcomes} counted {counted} payable {payable} expected {expected}")
        self.assertLessEqual(counted, D("27000.00"))

