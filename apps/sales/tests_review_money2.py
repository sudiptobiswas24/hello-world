"""Review of fix_money2 (sales): probes."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Currency, ExchangeRate
from apps.accounting.models import Account

from .models import (CustomerProfile, Invoice, RecurringInvoice, RecurringInvoiceLine, SalesOrder, SalesOrderLine,
                     committed_in_base)
from .tests_base import SalesTestCase
from .tests_partial_returns import PartOfADeliveryComesBackTests  # noqa

D = Decimal


class Returns(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.make_order("50", "25")
        self.shipment = self.ship(self.order, "50")
        self.invoice = self.bill(self.order)

    def test_return_with_cogs_and_inventory_switched_off(self):
        for acct in (self.cogs, self.inventory):
            acct.is_active = False
            acct.save()
        back = self.shipment.create_return()
        print("RETURN with COGS/inventory off ok", back.pk)

    def test_credit_note_with_revenue_off(self):
        self.revenue.is_active = False
        self.revenue.save()
        note = self.invoice.create_credit_note()
        print("CREDIT with revenue off ok", note.pk)

    def test_invoice_void_with_receivable_off_and_new_invoice_refused(self):
        self.ar.is_active = False
        self.ar.save()
        order = self.make_order("5", "25")
        with self.assertRaises(ValidationError):
            self.bill(order)


class Recurring(SalesTestCase):
    def schedule(self, start, **kw):
        s = RecurringInvoice.objects.create(code="SUB", customer=self.customer, receivable_account=self.ar,
                                            currency=self.usd, start_date=start, auto_post=True, **kw)
        RecurringInvoiceLine.objects.create(schedule=s, item=self.item, description="r", quantity=D("1"),
                                            unit_price=D("500"), revenue_account=self.revenue)
        return s

    def test_month_end_chain_and_leap(self):
        s = self.schedule(datetime.date(2024, 1, 31))
        days = []
        while s.next_run_date <= datetime.date(2024, 6, 1):
            days.append(s.generate_one().invoice_date)
            s.refresh_from_db()
        print("DAYS", days)
        self.assertEqual(days[1], datetime.date(2024, 2, 29))
        self.assertEqual(days[2], datetime.date(2024, 3, 31))

    def test_on_date_earlier_does_not_double_advance(self):
        s = self.schedule(datetime.date(2026, 1, 31))
        s.generate_one(on_date=datetime.date(2026, 1, 31))
        s.refresh_from_db()
        print("next", s.next_run_date)
        # a person takes March's with an earlier day than the next run
        inv = s.generate_one(on_date=datetime.date(2026, 2, 5), expected=s.next_run_date)
        s.refresh_from_db()
        print("on_date Feb 5 taken for Feb 28 run -> invoice", inv.invoice_date, "next", s.next_run_date)

    def test_edit_start_after_run_sticks(self):
        s = self.schedule(datetime.date(2026, 1, 1))
        s.generate_one()
        s.refresh_from_db()
        s.start_date = datetime.date(2026, 6, 1)
        s.save()
        s.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            s.generate_one()
        print("STUCK", ctx.exception.messages)


class Exposure(SalesTestCase):
    def test_two_currencies_two_dates(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        gbp = Currency.objects.create(code="GBP", name="Pound")
        for cur, rate, day in ((eur, "1.10", "2026-01-01"), (eur, "1.30", "2026-05-01"),
                               (gbp, "1.25", "2026-01-01"), (gbp, "1.50", "2026-04-01")):
            ExchangeRate.objects.create(currency=cur, rate=D(rate), valid_from=datetime.date.fromisoformat(day))
        expected = D("0")
        for cur, day, qty, price, rate in ((eur, datetime.date(2026, 3, 1), "10", "100", "1.10"),
                                           (eur, datetime.date(2026, 6, 1), "10", "100", "1.30"),
                                           (gbp, datetime.date(2026, 3, 15), "4", "250", "1.25"),
                                           (gbp, datetime.date(2026, 4, 1), "4", "250", "1.50")):
            order = SalesOrder.objects.create(customer=self.customer, order_date=day, currency=cur)
            SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D(qty),
                                          unit_price=D(price), revenue_account=self.revenue)
            order.confirm()
            expected += D(qty) * D(price) * D(rate)
        got = committed_in_base(self.customer)
        print("EXPOSURE got", got, "expected", expected)
        self.assertEqual(got, expected.quantize(D("0.01")))

    def test_no_rate_for_old_confirmed_order_blocks_the_next(self):
        eur = Currency.objects.create(code="EUR", name="Euro")
        r = ExchangeRate.objects.create(currency=eur, rate=D("1.10"), valid_from=datetime.date(2026, 1, 1))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1), currency=eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        r.delete()
        CustomerProfile.objects.update_or_create(party=self.customer, defaults={"credit_limit": D("100000")})
        try:
            self.make_order("1", "10")
            print("next order ok without the rate")
        except ValidationError as exc:
            print("NEXT ORDER BLOCKED:", exc.messages)


from apps.accounting.models import BooksOpening, Payment, PaymentDirection, AccountType  # noqa: E402
from apps.accounting.analytic import CostCentre  # noqa: E402
from apps.core.tests_roles import RolesTestCase  # noqa: E402
from apps.sales.models import InvoiceLine  # noqa: E402
from apps.accounting.models import Tax  # noqa: E402


class OpeningDoors(SalesTestCase, RolesTestCase):
    def setUp(self):
        SalesTestCase.setUp(self)
        RolesTestCase.setUp(self)
        self.equity = Account.objects.create(code="3000", name="Opening", account_type=AccountType.EQUITY)
        BooksOpening.objects.create(opened_on=datetime.date(2026, 4, 1), opening_account=self.equity)

    def draft(self, day):
        inv = Invoice.objects.create(customer=self.customer, invoice_date=day, receivable_account=self.ar,
                                     currency=self.usd)
        InvoiceLine.objects.create(invoice=inv, item=self.item, quantity=D("1"), unit_price=D("10"),
                                   revenue_account=self.revenue)
        return inv

    def test_invoice_and_payment_day_before_opening(self):
        c = self.as_("AR Manager")
        inv = self.draft(datetime.date(2026, 3, 31))
        r = c.post(f"/api/sales/invoices/{inv.pk}/post_invoice/")
        print("INV pre-open", r.status_code, r.content[:120])
        self.assertEqual(r.status_code, 400)
        # API create is_opening_balance cannot be claimed
        r = c.post("/api/sales/invoices/", {"customer": self.customer.pk, "invoice_date": "2026-03-31",
                                            "receivable_account": self.ar.pk, "currency": self.usd.pk,
                                            "is_opening_balance": True}, format="json")
        print("create claiming opening", r.status_code, r.json().get("is_opening_balance"))
        pay = Payment.objects.create(party=self.customer, direction=PaymentDirection.RECEIPT,
                                     payment_date=datetime.date(2026, 3, 31), amount=D("5"), currency=self.usd,
                                     bank_account=self.bank, counterpart_account=self.ar)
        r = c.post(f"/api/accounting/payments/{pay.pk}/post_payment/")
        print("PAY pre-open", r.status_code, r.content[:120])
        self.assertEqual(r.status_code, 400)

    def test_off_tax_and_void(self):
        c = self.as_("AR Manager")
        from apps.accounting.models import TaxGroup  # noqa
        inv = self.draft(datetime.date(2026, 4, 5))
        inv.post()
        # now every correction with accounts off
        for a in (self.revenue, self.ar):
            a.is_active = False
            a.save()
        r = c.post(f"/api/sales/invoices/{inv.pk}/credit_note/", {}, format="json")
        print("CREDIT API with revenue+AR off", r.status_code, r.content[:150])
        self.assertIn(r.status_code, (200, 201))
