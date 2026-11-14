"""review_stat2 probes: O163 deposit draw-down on the later of the two dates, against the FX rule."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.sales.tests_base import SalesTestCase


def show(*a):
    print("RV2", *a)


class Rv2Deposit(SalesTestCase):
    def build(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company, Currency, ExchangeRate
        from apps.sales.models import SalesOrder, SalesOrderLine

        self.loss = Account.objects.create(code="7100", name="FX loss", account_type=AccountType.EXPENSE)
        self.gain = Account.objects.create(code="7000", name="FX gain", account_type=AccountType.INCOME)
        company = Company.get()
        company.fx_loss_account, company.fx_gain_account = self.loss, self.gain
        company.save()
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("80"), valid_from=datetime.date(2026, 1, 1))
        ExchangeRate.objects.create(currency=eur, rate=Decimal("83"), valid_from=datetime.date(2026, 3, 5))
        ExchangeRate.objects.create(currency=eur, rate=Decimal("85"), valid_from=datetime.date(2026, 3, 8))
        order = SalesOrder.objects.create(customer=self.customer, currency=eur, order_date=datetime.date(2026, 3, 1))
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("100"), revenue_account=self.revenue)
        order.confirm()
        return order

    def test_deposit_dated_after_the_final_invoice(self):
        order = self.build()
        deposit = order.create_down_payment_invoice(self.ar, percent=30, invoice_date=datetime.date(2026, 3, 10))
        deposit.post()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 6))
        invoice.post()
        app = invoice.deposit_applications.get()
        lines = sorted((l.account.code, l.debit, l.credit) for l in app.fx_entry.lines.all()) if app.fx_entry else None
        show("FX deposit 10 Mar @85 vs final invoice 6 Mar @83: draw-down date", app.date, "amount", app.amount,
             "deposits bal", self.balance(self.deposits), "AR", self.balance(self.ar), "loss", self.balance(self.loss),
             "gain", self.balance(self.gain), "fx lines", lines,
             "entry lines", sorted((l.account.code, l.debit, l.credit) for l in app.journal_entry.lines.all()))
        self.assertEqual(self.balance(self.deposits), Decimal("0"))
        # explicit earlier dates
        order2 = self.build_second(order)

    def build_second(self, order):
        return None

    def test_explicit_dates(self):
        order = self.build()
        deposit = order.create_down_payment_invoice(self.ar, percent=30, invoice_date=datetime.date(2026, 3, 1))
        deposit.post()
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 6))
        invoice.post(apply_deposits=False)
        for day in (datetime.date(2026, 3, 5), datetime.date(2026, 3, 6), datetime.date(2099, 1, 1)):
            try:
                invoice.apply_deposit(deposit, amount=Decimal("100"), on_date=day)
                show("explicit", day, "applied")
            except ValidationError as e:
                show("explicit", day, "refused", str(e)[:90])
        # an auto draw-down for the rest, on an earlier on_date than the deposit's date
        applied = invoice.apply_available_deposits(on_date=datetime.date(2026, 2, 1))
        show("auto with on_date 1 Feb (before both):", [(a.date, a.amount) for a in applied])
