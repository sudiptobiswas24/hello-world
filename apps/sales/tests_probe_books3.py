"""review_books3 probes: O260 in rupees with a real TCS give-back; O350 commission; printed PASS/FAIL."""
import datetime
from decimal import Decimal as D

from apps.accounting.models import Payment, PaymentDirection, JournalLine
from django.db.models import Sum

from .models import InvoicePayment
from .tests_tcs import TcsTestCase, OCTOBER


class O260Rupees(TcsTestCase):
    def pay(self, direction, amount, doc, on=datetime.date(2026, 10, 6)):
        p = Payment.objects.create(party=doc.customer, direction=direction, payment_date=on, amount=D(amount),
                                   currency=self.inr, bank_account=self.bank, counterpart_account=self.ar)
        p.post()
        row = InvoicePayment.objects.create(invoice=doc, payment=p, amount=D(amount))
        return p, row

    def bal(self, code="1100"):
        r = JournalLine.objects.filter(account__code=code, entry__posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (r["d"] or D(0)) - (r["c"] or D(0))

    def check(self, name, got, want):
        print(("PASS " if got == want else "FAIL ") + name, got if got != want else "", "" if got == want else f"expected {want}")
        return got == want

    def test_credit_note_with_giveback_partly_absorbed(self):
        inv = self.t1()
        ok = [self.check("invoice total", inv.total(), D("107262.00"))]
        self.pay(PaymentDirection.RECEIPT, "100000", inv)
        note = self.credit(inv, "500", OCTOBER)
        ok.append(self.check("note total (26550 + 266 give-back)", note.total(), D("26816.00")))
        ok.append(self.check("absorbed = invoice left 7262", note.amount_absorbed(), D("7262.00")))
        refund, row = self.pay(PaymentDirection.DISBURSEMENT, str(note.refund_due()), note)
        inv.refresh_from_db(); note.refresh_from_db()
        ok.append(self.check("refund due 19554", row.amount, D("19554.00")))
        ok.append(self.check("invoice due", inv.amount_due(), D("0")))
        ok.append(self.check("note due", note.amount_due(), D("0")))
        ok.append(self.check("AR control", self.bal(), D("0")))
        ok.append(self.check("TCS payable keeps collected less given back (796)", -self.bal("2290"), D("796.00")))
        # void of the refund: the note is open again, AR must equal what the note says is due
        refund.void()
        inv.refresh_from_db(); note.refresh_from_db()
        ok.append(self.check("after void of refund: AR == -(note due) ", self.bal(), -note.amount_due()))
        ok.append(self.check("after void: note due 19554", note.amount_due(), D("19554.00")))
        # a full receipt first, then the whole note refunded (invoice settled before the note)
        self.assertTrue(all(ok))

    def test_invoice_paid_in_full_then_note(self):
        inv = self.t1()
        self.pay(PaymentDirection.RECEIPT, "107262", inv)
        note = self.credit(inv, "500", OCTOBER)
        ok = [self.check("nothing absorbed", note.amount_absorbed(), D("0"))]
        refund, row = self.pay(PaymentDirection.DISBURSEMENT, str(note.refund_due()), note)
        inv.refresh_from_db(); note.refresh_from_db()
        ok.append(self.check("refund = whole note 26816", row.amount, D("26816.00")))
        ok.append(self.check("AR - after full pay, note refunded: AR == invoice residue of the other 1500kg",
                             self.bal(), D("107262.00") - D("107262.00") + D("26816.00") - D("26816.00")))
        self.assertTrue(all(ok))


from .tests_audit_round2 import CommissionLeavesTcsOutTests as _Fixture
from .tests_base import SalesTestCase


class O350Commission(SalesTestCase):
    invoice_with_tcs = _Fixture.invoice_with_tcs
    basis = _Fixture.basis

    def check(self, name, got, want):
        print(("PASS " if got == want else "FAIL ") + name, "" if got == want else f"got {got} expected {want}")
        return got == want

    def test_paid_in_full_then_half_credited_and_refunded(self):
        from .models import InvoicePayment
        from apps.accounting.models import Payment, PaymentDirection
        inv = self.invoice_with_tcs()
        self.allocate(self.receipt("1212"), inv, "1212")
        ok = [self.check("paid in full: 1000/50", self.basis(), (D("1000.00"), D("50.00")))]
        line = inv.lines.get()
        note = inv.create_credit_note(quantities={line: D("5")}, on_date=datetime.date(2026, 4, 5))
        ok.append(self.check("note total 600 (planted TCS has no collection to give back)", note.total(), D("600.00")))
        p = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT,
                                   payment_date=datetime.date(2026, 4, 6), amount=note.refund_due(), currency=self.usd,
                                   bank_account=self.bank, counterpart_account=self.ar)
        p.post()
        InvoicePayment.objects.create(invoice=note, payment=p, amount=note.refund_due())
        ok.append(self.check("whole period: 500/25", self.basis(), (D("500.00"), D("25.00"))))
        ok.append(self.check("April alone: -500/-25", self.basis(date_from=datetime.date(2026, 4, 1), date_to=datetime.date(2026, 4, 30)),
                             (D("-500.00"), D("-25.00"))))
        ok.append(self.check("March alone: 1000/50", self.basis(date_from=datetime.date(2026, 3, 1), date_to=datetime.date(2026, 3, 31)),
                             (D("1000.00"), D("50.00"))))
        self.assertTrue(all(ok))

    def test_tcs_only_and_period_before_and_cap(self):
        inv = self.invoice_with_tcs()
        self.allocate(self.receipt("12"), inv, "12")
        ok = [self.check("12 paid (the TCS figure) counts as 12 of 1212: 10.00", self.basis(), (D("10.00"), D("0.50")))]
        self.allocate(self.receipt("1200", on=datetime.date(2026, 5, 2)), inv, "1200")
        ok.append(self.check("all paid: 1000", self.basis(), (D("1000.00"), D("50.00"))))
        ok.append(self.check("May alone 990", self.basis(date_from=datetime.date(2026, 5, 1), date_to=datetime.date(2026, 5, 31)),
                             (D("990.00"), D("49.50"))))
        self.assertTrue(all(ok))
