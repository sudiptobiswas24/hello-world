"""Probes of the second audit, trade lane (sales). Each asserts what SHOULD be true."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from .models import Invoice, InvoiceLine
from .tests_base import SalesTestCase


class HandKeyedCreditNote(SalesTestCase):
    def posted_invoice(self, day=datetime.date(2026, 3, 1)):
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        return self.bill(order, on=day)

    def note(self, invoice, **extra):
        return Invoice.objects.create(
            customer=invoice.customer, invoice_date=extra.pop("invoice_date", invoice.invoice_date),
            receivable_account=extra.pop("receivable_account", invoice.receivable_account),
            currency=invoice.currency, credits=invoice, **extra)

    def test_S1_a_note_by_hand_on_another_control_account_is_refused(self):
        invoice = self.posted_invoice()
        other = Account.objects.create(code="1101", name="AR 2", account_type=AccountType.ASSET)
        line = invoice.lines.first()
        note = self.note(invoice, receivable_account=other)
        InvoiceLine.objects.create(invoice=note, credits_line=line, item=line.item, quantity=Decimal("2"),
                                   unit_price=line.unit_price, revenue_account=line.revenue_account)
        with self.assertRaises(ValidationError):
            note.post()

    def test_S2_a_note_by_hand_dated_before_its_invoice_is_refused(self):
        invoice = self.posted_invoice(datetime.date(2026, 3, 10))
        line = invoice.lines.first()
        note = self.note(invoice, invoice_date=datetime.date(2026, 3, 1))
        InvoiceLine.objects.create(invoice=note, credits_line=line, item=line.item, quantity=Decimal("2"),
                                   unit_price=line.unit_price, revenue_account=line.revenue_account)
        with self.assertRaises(ValidationError):
            note.post()

    def test_S2b_a_note_by_hand_dated_a_year_ahead_is_refused(self):
        invoice = self.posted_invoice()
        line = invoice.lines.first()
        note = self.note(invoice, invoice_date=timezone.localdate() + datetime.timedelta(days=400))
        InvoiceLine.objects.create(invoice=note, credits_line=line, item=line.item, quantity=Decimal("2"),
                                   unit_price=line.unit_price, revenue_account=line.revenue_account)
        with self.assertRaises(ValidationError):
            note.post()

    def test_S5_a_typed_credit_line_cannot_credit_more_than_the_invoice(self):
        invoice = self.posted_invoice()   # 1000
        note = self.note(invoice)
        InvoiceLine.objects.create(invoice=note, description="oops", quantity=Decimal("1"),
                                   unit_price=Decimal("5000"), revenue_account=self.revenue)
        with self.assertRaises(ValidationError):
            note.post()


from apps.core.tests_roles import RolesTestCase  # noqa: E402


class DunningOnlyAdvances(SalesTestCase):
    def test_S7_a_customer_who_got_the_60_day_notice_is_not_sent_the_7_day_one_after(self):
        """Levels 7, 30, 60. Run first at 70 days overdue: the 60-day notice. Run again next day: nothing
        (or a higher level), never the 30-day and then the 7-day reminder after the final notice."""
        from django.core import mail
        from .models import DunningLevel, DunningNotice, run_dunning

        for name, days in (("First", 7), ("Second", 30), ("Final", 60)):
            DunningLevel.objects.create(name=name, days_overdue=days)
        order = self.make_order(quantity="10", price="100")
        self.ship(order, 10)
        invoice = self.bill(order, on=datetime.date(2026, 3, 1))   # net 30: due 31 March
        first_day = invoice.due_date + datetime.timedelta(days=70)
        run_dunning(as_of=first_day)
        run_dunning(as_of=first_day + datetime.timedelta(days=1))
        run_dunning(as_of=first_day + datetime.timedelta(days=2))
        sent = list(DunningNotice.objects.filter(invoice=invoice).order_by("id").values_list("level__days_overdue", flat=True))
        self.assertEqual(sent, [60], f"levels sent in order: {sent}")


class CommissionOnCollectedExcludesTax(SalesTestCase):
    def test_S8_commission_on_collected_cash_is_on_the_net_sale_as_the_invoiced_basis_is(self):
        """Invoice 10 x 100 + 20% VAT = 1,200, paid in full. 5% of the net sale 1,000 is 50.00 on either basis;
        the collected basis counts the payment, tax included: 60.00."""
        from apps.accounting.models import Tax, TaxGroup
        from .models import CommissionBasis, CommissionPlan, SalesRep, commission_report

        group = TaxGroup.objects.create(code="VAT", name="VAT")
        vat_account = Account.objects.create(code="2100", name="VAT Payable", account_type=AccountType.LIABILITY)
        vat = Tax.objects.create(code="VAT20", name="VAT 20%", rate=Decimal("20"), group=group,
                                 collected_account=vat_account, paid_account=vat_account)
        plan = CommissionPlan.objects.create(code="P", name="5% collected", percent=Decimal("5"),
                                             basis=CommissionBasis.PAID)
        SalesRep.objects.create(party=self.rep, plan=plan)
        order = self.make_order("10", "100", rep=self.rep)
        for line in order.lines.all():
            line.taxes.set([vat])
        invoice = self.bill(order)
        self.assertEqual(invoice.total(), Decimal("1200.00"))
        self.allocate(self.receipt("1200"), invoice, "1200")
        self.assertEqual(commission_report()[0]["commission"], Decimal("50.00"))


