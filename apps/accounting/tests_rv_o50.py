import datetime
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from django.db.models import Sum
from apps.accounting.tests_fx_settlement import FxSettlementTestCase
from apps.accounting.models import JournalLine, JournalEntry, PaymentDirection, AccountingPeriod, Payment
from django.utils import timezone


def show(tag, *a):
    print("RV", tag, *a)


class O50(FxSettlementTestCase):
    def setUp(self):
        super().setUp()
        self.invoice = self.euro_invoice("1000")
        self.rate_moves_to("1.10")
        self.receipt = self.euro_payment(PaymentDirection.RECEIPT, "1000")

    def close(self, name, s, e):
        p = AccountingPeriod.objects.create(name=name, start_date=s, end_date=e); p.close(); return p

    def fxrows(self):
        return [(l.entry.date, l.entry.memo[:40], l.debit, l.credit, l.account.code) for l in JournalLine.objects.filter(account__in=[self.gain, self.loss], entry__posted=True).select_related("entry", "account").order_by("entry__date", "id")]

    def test_edit_delete_across_closed(self):
        from apps.sales.models import InvoicePayment
        self.close("Feb", datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
        a = InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=D("1000"))
        show("create", a.date, self.fxrows(), "AR", self.balance(self.ar))
        a.amount = D("600"); a.save()
        show("edit600", a.date, self.fxrows(), "AR", self.balance(self.ar))
        a.refresh_from_db()
        a.amount = D("0.01"); a.save()
        show("edit0.01", a.date, self.fxrows(), "AR", self.balance(self.ar))
        a.delete()
        show("deleted", self.fxrows(), "AR", self.balance(self.ar))
        # AR after delete should be invoice 1200 - receipt 1100 = 100
        self.assertEqual(self.balance(self.ar), D("100.00"))

    def test_void_payment_across_closed(self):
        from apps.sales.models import InvoicePayment
        self.close("Feb", datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
        a = InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=D("1000"))
        try:
            self.receipt.void()
        except ValidationError as e:
            show("void refused", e)
        else:
            show("voided", self.fxrows(), "AR", self.balance(self.ar), "bank", self.balance(self.bank))
        try:
            self.receipt.void(on_date=datetime.date(2026, 3, 1))
        except ValidationError as e:
            show("void mar1 refused", e)

    def test_future_and_before(self):
        from apps.sales.models import InvoicePayment
        for d in (datetime.date(2026, 2, 1), datetime.date(2026, 12, 31), datetime.date(2026, 2, 15), datetime.date(2026, 2, 10)):
            try:
                a = InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=D("100"), date=d)
                show("created with", d, a.date, self.fxrows()); a.delete()
            except ValidationError as e:
                show("refused", d, e)

    def test_closed_date_given(self):
        from apps.sales.models import InvoicePayment
        self.close("Feb", datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
        try:
            a = InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=D("100"), date=datetime.date(2026, 2, 20))
            show("closed-date allowed", a.date, self.fxrows())
        except ValidationError as e:
            show("closed-date refused", e)

    def test_inr_allocation_closed_date(self):
        # no fx difference: allocation dated in a closed month passes?
        from apps.sales.models import InvoicePayment
        pass

    def test_edit_date_earlier(self):
        from apps.sales.models import InvoicePayment
        a = InvoicePayment.objects.create(invoice=self.invoice, payment=self.receipt, amount=D("500"))
        a.date = datetime.date(2026, 3, 1)
        try:
            a.save(); show("date moved earlier ok", a.date, self.fxrows())
        except ValidationError as e:
            show("date earlier refused", e)

    def test_bill_mirror(self):
        from apps.purchasing.models import BillPayment
        bill = self.euro_bill("1000")
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "1000")
        self.close("Feb", datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))
        a = BillPayment.objects.create(bill=bill, payment=pay, amount=D("1000"))
        show("bill create", a.date, self.fxrows(), "AP", self.balance(self.ap))
        a.amount = D("400"); a.save()
        show("bill edit", a.date, self.fxrows(), "AP", self.balance(self.ap))
        a.delete()
        show("bill del", self.fxrows(), "AP", self.balance(self.ap))
        a = BillPayment.objects.create(bill=bill, payment=pay, amount=D("1000"))
        pay.void()
        show("bill voided", self.fxrows(), "AP", self.balance(self.ap))
