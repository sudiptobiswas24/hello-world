"""
review_yearend probes: what SHOULD be true of the closing-rate restatement. Figures in
/home/user/work/calc_review_yearend/calc.py.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import Account, AccountType, AccountingPeriod, PaymentDirection
from .restatement import Restatement
from .tests_restatement import YEAR_END, D, RestatementTestCase


class Probes(RestatementTestCase):
    def pay_and_check(self, invoice, made):
        """Pay in full 15 Apr at 84.55: the receivable is empty and the exchange is only what money moved."""
        self.apply(self.receipt("1000", D(2026, 4, 15)), invoice, "1000", D(2026, 4, 15))
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_P01_one_restatement_a_period_a_second_in_the_same_period_does_not_stack(self):
        # 15 Mar @83.9 (diff 563.30) then 31 Mar @84.1 (763.30), both in March, both reverse 1 Apr.
        self.invoice()
        self.march()
        Restatement.make(D(2026, 3, 15), rates={"USD": "83.9"})
        Restatement.make(YEAR_END)
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("84100.00"),
                         "receivable at the year end must read 1,000 x 84.1")
        self.assertEqual(self.gains(YEAR_END), Decimal("763.30"))

    def test_P02_paid_in_the_restated_period_after_the_day_leaves_no_unrealised_behind(self):
        # restated 25 Mar @84.1; paid 28 Mar at 83.9 (realised 563.30); the entry reverses only 1 Apr.
        invoice = self.invoice()
        self.march()
        Restatement.make(D(2026, 3, 25), rates={"USD": "84.1"})
        self.apply(self.receipt("1000", D(2026, 3, 28)), invoice, "1000", D(2026, 3, 28))
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("0"), "nothing is open at 31 Mar")
        self.assertEqual(self.gains(YEAR_END), Decimal("563.30"), "only the realised difference at 31 Mar")

    def test_P03_part_paid_across_the_reversal(self):
        invoice = self.invoice()
        self.march()
        Restatement.make(YEAR_END)
        self.apply(self.receipt("400", D(2026, 4, 15)), invoice, "400", D(2026, 4, 15))
        self.assertEqual(self.gains() - self.gains(YEAR_END), Decimal("-763.30") + Decimal("485.32"))
        self.apply(self.receipt("600", D(2026, 4, 20)), invoice, "600", D(2026, 4, 20))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("485.32") + Decimal("727.98"))

    def test_P04_paid_after_a_credit_note(self):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=D(2026, 3, 5), currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                      unit_price=Decimal("100"), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=D(2026, 3, 5))
        invoice.post()
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("4")}, on_date=D(2026, 3, 12))
        self.march()
        before = self.gains()
        made = Restatement.make(YEAR_END)
        line = made.lines.get(item_type="sales.invoice", item_id=invoice.pk)
        self.assertEqual(line.open_amount, Decimal("600.00"))
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("50460.00"))
        self.apply(self.receipt("600", D(2026, 4, 15)), invoice, "600", D(2026, 4, 15))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains() - before, Decimal("50730.00") - line.carried_base)

    def test_P05_unrealised_nets_to_zero_once_reversed_every_side(self):
        invoice = self.invoice()
        self.march()
        Restatement.make(YEAR_END)
        self.assertEqual(self.gains(D(2026, 4, 1)), Decimal("0"))
        self.assertEqual(self.balance(self.ar, D(2026, 4, 1)), Decimal("83336.70"))
        self.pay_and_check(invoice, None)
        self.assertEqual(self.gains(), Decimal("1213.30"))

    def test_P06_payable_paid_after_reversal(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment

        expense = Account.objects.create(code="5000", name="Purchases", account_type=AccountType.EXPENSE)
        bill = Bill.objects.create(vendor=self.vendor, bill_date=D(2026, 3, 5), payable_account=self.ap,
                                   currency=self.usd)
        BillLine.objects.create(bill=bill, description="Parts", quantity=Decimal("1"), unit_price=Decimal("1000"),
                                expense_account=expense)
        bill.post()
        self.march()
        Restatement.make(YEAR_END)
        payment = self.receipt("1000", D(2026, 4, 15), direction=PaymentDirection.DISBURSEMENT)
        BillPayment.objects.create(bill=bill, payment=payment, amount=Decimal("1000"), date=D(2026, 4, 15))
        self.assertEqual(self.balance(self.ap), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("-1213.30"))

    def test_P07_eefc_spent_after_reversal_leaves_no_residue(self):
        self.receipt("1000", D(2026, 3, 10), bank=self.eefc)
        self.march()
        before = self.balance(self.eefc)
        Restatement.make(YEAR_END)
        self.assertEqual(self.balance(self.eefc, D(2026, 4, 1)), before)
        self.assertEqual(self.gains(), Decimal("0"))

    def test_P08_unapplied_receipt_applied_after_reversal(self):
        invoice = self.invoice()
        receipt = self.receipt("1000", D(2026, 3, 10))
        self.march()
        Restatement.make(YEAR_END)
        self.apply(receipt, invoice, "1000", D(2026, 4, 15))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("563.30"))

    def test_P09_void_after_a_payment_leaves_the_books_as_if_never_made(self):
        invoice = self.invoice()
        self.march()
        made = Restatement.make(YEAR_END)
        self.apply(self.receipt("1000", D(2026, 4, 15)), invoice, "1000", D(2026, 4, 15))
        made.void(on_date=YEAR_END)
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("83336.70"))
        self.assertEqual(self.balance(self.ar), Decimal("0"))
        self.assertEqual(self.gains(), Decimal("1213.30"))

    def test_P10_void_in_a_period_closed_since(self):
        self.invoice()
        period = self.march()
        made = Restatement.make(YEAR_END)
        period.close()
        with self.assertRaisesMessage(ValidationError, "March 2026 is closed"):
            made.void(on_date=YEAR_END)
        made.refresh_from_db()
        self.assertIsNone(made.voided_on)  # refused whole: nothing half-posted
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("84100.00"))
        self.assertEqual(self.balance(self.ar, D(2026, 4, 1)), Decimal("83336.70"))

    def test_P11_a_note_dated_after_the_day_does_not_make_the_year_end_unrestatable_silently(self):
        # the author's limit 1. Decided (coordinator, D): replayed to the day as payments are, not refused:
        # on 31 Mar the whole 1,000 was open, so it is restated at 763.30.
        invoice = self.invoice()
        invoice.create_credit_note(on_date=D(2026, 4, 2))
        line = Restatement.make(YEAR_END).lines.get(item_type="sales.invoice", item_id=invoice.pk)
        self.assertEqual((line.open_amount, line.difference), (Decimal("1000.00"), Decimal("763.30")))

    def test_P12_an_invoice_posted_into_a_restated_day_is_refused_or_restated(self):
        # limit 4: the year end misses a USD invoice dated on or before the day, posted after it.
        self.invoice()
        Restatement.make(YEAR_END)
        late = self.invoice("500")  # dated 5 March, posted now, March still open
        self.assertEqual(self.balance(self.ar, YEAR_END), Decimal("84100.00") + Decimal("500") * Decimal("84.1"),
                         f"{late.number} dated 5 Mar is open at 31 Mar and is not at the closing rate")
