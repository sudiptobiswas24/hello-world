"""review_books3: a refund and a note's absorption at once, on PostgreSQL."""
import datetime, unittest
from decimal import Decimal as D
from django.db import connection
from django.test import tag
from apps.accounting.models import JournalEntry, Payment, PaymentDirection
from apps.e2e import tests_races as r
from apps.e2e.tests_races_settlement import MoneyRaces as _M, say
from apps.sales.models import Invoice, InvoicePayment, InvoiceLine


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class RefundVersusAbsorption(r.RaceCase):
    setUp = _M.setUp
    make_order, receipt, balance = _M.make_order, _M.receipt, _M.balance
    free_invoice, payment = _M.free_invoice, _M.payment

    def foots(self, inv_pk, note_pk):
        inv, note = Invoice.objects.get(pk=inv_pk), Invoice.objects.get(pk=note_pk)
        return self.balance(self.ar), inv.amount_due() - note.amount_due()

    def scenario(self, name, make_calls):
        inv = self.free_invoice("1000"); inv.post()
        a = self.payment("600")
        InvoicePayment.objects.create(invoice=inv, payment=a, amount=D("600"))
        note = inv.create_credit_note(quantities={inv.lines.get(): D("1")}, on_date=datetime.date(2026, 3, 6))
        self.assertEqual((note.amount_absorbed(), note.refund_due()), (D("400"), D("600")))
        refund = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 3, 7),
                                        amount=D("600"), currency=self.usd, bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        outcomes = r.race((JournalEntry, InvoicePayment), *make_calls(inv, note, a, refund))
        book, docs = self.foots(inv.pk, note.pk)
        say(book == docs, f"{name}: AR {book} == invoice due - note due {docs}", outcomes)
        self.assertEqual(book, docs, outcomes)

    def test_refund_allocation_while_the_receipt_is_voided(self):
        self.scenario("refund vs void of the receipt the note absorbed around",
                      lambda inv, note, a, refund: [
                          lambda: InvoicePayment.objects.create(invoice_id=note.pk, payment_id=refund.pk, amount=D("600")),
                          lambda: Payment.objects.get(pk=a.pk).void()])

    def test_refund_while_a_second_receipt_arrives(self):
        # a note posted on an unpaid-in-part invoice; the refund is allocated while another receipt tops the invoice up
        self.scenario("refund vs a top-up receipt",
                      lambda inv, note, a, refund: [
                          lambda: InvoicePayment.objects.create(invoice_id=note.pk, payment_id=refund.pk, amount=D("600")),
                          lambda: InvoicePayment.objects.create(invoice_id=inv.pk, payment_id=self.payment("100", on=datetime.date(2026, 3, 8)).pk, amount=D("100"))])
