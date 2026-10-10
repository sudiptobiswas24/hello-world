import datetime
from decimal import Decimal as D
from django.core.exceptions import ValidationError
from apps.accounting.tests_probe_audit import InrBooks
from apps.accounting.models import JournalLine
from django.db.models import Sum


def show(*a):
    print("\nRV", *a)


class O55(InrBooks):
    def test_write_off_before_payment_and_recover_before_write_off(self):
        from apps.accounting.models import Payment, PaymentDirection
        from apps.sales.models import InvoicePayment
        invoice = self.invoice("1000")
        show("invoice date", invoice.invoice_date)
        try:
            wo = invoice.write_off(on_date=datetime.date(2026, 9, 20), reason="x")
        except ValidationError as e:
            show("wo refused", e); return
        show("write_off returned", type(wo).__name__)
        try:
            invoice.recover_write_off(invoice.write_offs.get(), on_date=datetime.date(2026, 9, 12))
            show("recover_write_off dated 12 Sep, before the write-off of 20 Sep: ACCEPTED")
        except ValidationError as e:
            show("recover before write-off refused", e)
        except TypeError as e:
            show("sig", e)

    def test_write_off_before_a_payment(self):
        from apps.accounting.models import Payment, PaymentDirection
        from apps.sales.models import InvoicePayment
        invoice = self.invoice("1000")
        pay = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT, payment_date=datetime.date(2026, 9, 25),
                                     amount=D("600"), bank_account=self.bank, counterpart_account=self.ar) if hasattr(self, "bank") else None
        if pay is None:
            show("no bank in fixture"); return
        pay.post()
        InvoicePayment.objects.create(invoice=invoice, payment=pay, amount=D("600"))
        try:
            invoice.write_off(on_date=datetime.date(2026, 9, 15), reason="x")
            ar = JournalLine.objects.filter(account=self.ar, entry__date__lte=datetime.date(2026, 9, 20)).aggregate(d=Sum("debit"), c=Sum("credit"))
            show("write-off of 400 dated 15 Sep, BEFORE the 600 payment of 25 Sep: ACCEPTED; AR on 20 Sep =", (ar["d"] or 0) - (ar["c"] or 0), "(invoice 1000 unpaid on that day)")
        except ValidationError as e:
            show("refused", e)
