"""Probes of the second audit, trade lane (purchasing)."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounting.models import Account, AccountType
from .models import Bill, BillLine
from .tests_lifecycle import PurchasingLifecycleTestCase


class HandKeyedDebitNote(PurchasingLifecycleTestCase):
    def posted_bill(self, day=datetime.date(2026, 1, 10)):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable)
        BillLine.objects.create(bill=bill, description="Spares", quantity=Decimal("10"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        bill.post()
        return bill

    def note(self, bill, **extra):
        return Bill.objects.create(
            vendor=bill.vendor, bill_date=extra.pop("bill_date", bill.bill_date),
            payable_account=extra.pop("payable_account", bill.payable_account), debits=bill, **extra)

    def test_P1_a_note_by_hand_on_another_control_account_is_refused(self):
        bill = self.posted_bill()
        other = Account.objects.create(code="2001", name="AP 2", account_type=AccountType.LIABILITY)
        note = self.note(bill, payable_account=other)
        BillLine.objects.create(bill=note, debits_line=bill.lines.get(), description="Spares", quantity=Decimal("2"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        with self.assertRaises(ValidationError):
            note.post()

    def test_P2_a_note_by_hand_dated_before_its_bill_is_refused(self):
        bill = self.posted_bill(datetime.date(2026, 1, 10))
        note = self.note(bill, bill_date=datetime.date(2026, 1, 1))
        BillLine.objects.create(bill=note, debits_line=bill.lines.get(), description="Spares", quantity=Decimal("2"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        with self.assertRaises(ValidationError):
            note.post()

    def test_P2b_a_note_by_hand_a_year_ahead_is_refused(self):
        bill = self.posted_bill()
        note = self.note(bill, bill_date=timezone.localdate() + datetime.timedelta(days=400))
        BillLine.objects.create(bill=note, debits_line=bill.lines.get(), description="Spares", quantity=Decimal("2"),
                                unit_price=Decimal("100"), expense_account=self.expense)
        with self.assertRaises(ValidationError):
            note.post()

    def test_P3_a_typed_debit_line_cannot_debit_more_than_the_bill(self):
        bill = self.posted_bill()
        note = self.note(bill)
        BillLine.objects.create(bill=note, description="oops", quantity=Decimal("1"), unit_price=Decimal("5000"),
                                expense_account=self.expense)
        with self.assertRaises(ValidationError):
            note.post()

    def test_P4_a_debit_note_on_a_future_dated_bill_is_not_dated_before_it(self):
        day = timezone.localdate() + datetime.timedelta(days=10)
        bill = self.posted_bill(day)
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("2")})
        self.assertGreaterEqual(note.bill_date, bill.bill_date)


from .tests_money import MoneyCase  # noqa: E402


class PolicyInTheBaseCurrency(MoneyCase):
    def test_P5_a_euro_order_worth_4_5_million_base_asks_the_1_million_limit(self):
        from apps.core.models import Currency, ExchangeRate
        from .models import PurchaseApprovalPolicy, PurchaseOrder, PurchaseOrderLine

        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(currency=eur, rate=Decimal("90"), valid_from=datetime.date(2026, 1, 1))
        PurchaseApprovalPolicy.objects.create(code="P", name="Limit", max_order_value=Decimal("1000000"))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 5), currency=eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                         unit_price=Decimal("5000"))
        with self.assertRaises(ValidationError):
            order.confirm()
