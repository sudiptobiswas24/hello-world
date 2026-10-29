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
        """
        AR against the documents and the money no document has taken: receipts on account less, refunds paid and
        not allocated more (a refund paid on a receipt that bounced is owed back). And the customer's own balance,
        as the statement and the aging read it, against AR.
        """
        from apps.accounting.settlement import standing_on_account
        from apps.sales.models import outstanding_balance, receivable_accounts

        inv, note = Invoice.objects.get(pk=inv_pk), Invoice.objects.get(pk=note_pk)
        received, paid_out = standing_on_account(
            Payment.objects.filter(party=self.customer, counterpart_account__in=receivable_accounts()))
        return (self.balance(self.ar), inv.amount_due() - note.amount_due() - received + paid_out,
                outstanding_balance(self.customer))

    def scenario(self, name, make_calls, first=None):
        inv = self.free_invoice("1000"); inv.post()
        a = self.payment("600")
        InvoicePayment.objects.create(invoice=inv, payment=a, amount=D("600"))
        note = inv.create_credit_note(quantities={inv.lines.get(): D("1")}, on_date=datetime.date(2026, 3, 6))
        self.assertEqual((note.amount_absorbed(), note.refund_due()), (D("400"), D("600")))
        refund = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=datetime.date(2026, 3, 7),
                                        amount=D("600"), currency=self.usd, bank_account=self.bank, counterpart_account=self.ar)
        refund.post()
        calls = make_calls(inv, note, a, refund)
        if first is not None:
            outcomes = r.race_in_order((JournalEntry, InvoicePayment), *(calls if first == 0 else calls[::-1]))
            outcomes = outcomes if first == 0 else outcomes[::-1]
        else:
            outcomes = r.race((JournalEntry, InvoicePayment), *calls)
        book, docs, owed = self.foots(inv.pk, note.pk)
        say(book == docs == owed, f"{name}: AR {book} == documents and money on account {docs} == owed {owed}",
            outcomes)
        self.assertEqual((book, owed), (docs, docs), outcomes)
        return outcomes, book

    @staticmethod
    def refund_and_void(inv, note, a, refund):
        return [lambda: InvoicePayment.objects.create(invoice_id=note.pk, payment_id=refund.pk, amount=D("600")),
                lambda: Payment.objects.get(pk=a.pk).void()]

    def test_refund_allocation_while_the_receipt_is_voided_the_refund_first(self):
        # Worked by hand (fix_g23b6): invoice +1,000, receipt -600, note -1,000 absorbing 400 and owing 600 back,
        # refund +600 allocated to it; then the receipt bounces: +600. AR 600, the invoice's 600 owed again.
        outcomes, book = self.scenario("refund first, then the void of the receipt", self.refund_and_void, first=0)
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.assertEqual(book, D("600.00"))

    def test_refund_allocation_while_the_receipt_is_voided_the_void_first(self):
        # The receipt bounces first: the note absorbs all 1,000 and owes nothing back, so the refund's allocation is
        # refused, and the refund stands paid and unallocated: invoice +1,000, note -1,000, refund +600 = AR 600,
        # owed back by a customer paid a refund on a receipt that bounced.
        outcomes, book = self.scenario("void of the receipt first, then the refund", self.refund_and_void, first=1)
        self.assertEqual(outcomes[1], "done", outcomes)
        self.assertIn("only has 0.00 outstanding", outcomes[0])
        self.assertEqual(book, D("600.00"))

    def test_refund_while_a_second_receipt_arrives(self):
        # a note posted on an unpaid-in-part invoice; the refund is allocated while another receipt tops the invoice up
        self.scenario("refund vs a top-up receipt",
                      lambda inv, note, a, refund: [
                          lambda: InvoicePayment.objects.create(invoice_id=note.pk, payment_id=refund.pk, amount=D("600")),
                          lambda: InvoicePayment.objects.create(invoice_id=inv.pk, payment_id=self.payment("100", on=datetime.date(2026, 3, 8)).pk, amount=D("100"))])
