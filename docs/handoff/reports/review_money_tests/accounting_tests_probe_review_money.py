"""
review_money probes (gate g20). Numbers are worked in
$SP/calc_review_money/calc.py, not read off the code under test.
"""
import datetime
from decimal import Decimal as D

from apps.accounting.models import Account, AccountType, Payment, PaymentDirection
from apps.core.models import Company, ExchangeRate

from .tests_fx_settlement import FxSettlementTestCase

JAN5 = datetime.date(2026, 1, 5)
FEB = datetime.date(2026, 2, 15)


class PartPaymentResidueProbe(FxSettlementTestCase):
    """100.09 EUR booked at 1.12345 (112.45), paid at 1.09876 in two parts 50 + 50.09."""

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.09876"), valid_from=datetime.date(2026, 2, 1))

    def invoice(self, amount):
        from apps.sales.models import SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("1"),
                                      unit_price=D(amount), revenue_account=self.revenue)
        order.confirm()
        inv = order.create_invoice(self.ar, invoice_date=JAN5)
        inv.post()
        return inv

    def bill(self, amount):
        from apps.purchasing.models import Bill, BillLine
        bill = Bill.objects.create(vendor=self.vendor, bill_date=JAN5, payable_account=self.ap, currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D(amount),
                                expense_account=self.expense)
        bill.post()
        return bill

    def test_two_receipts_one_after_the_other_clear_the_receivable(self):
        # calc.py case 1b: receipts 54.94 + 55.04, AR = 112.45 - 109.98 - 2.46 = 0.01; expected 0
        from apps.sales.models import InvoicePayment
        inv = self.invoice("100.09")
        for amount in ("50", "50.09"):
            receipt = self.euro_payment(PaymentDirection.RECEIPT, amount)
            InvoicePayment.objects.create(invoice=inv, payment=receipt, amount=D(amount))
        self.assertEqual((inv.amount_due(), self.balance(self.ar)), (D("0"), D("0")), "(due, AR)")

    def test_the_payable_mirror(self):
        from apps.purchasing.models import BillPayment
        bill = self.bill("100.09")
        for amount in ("50", "50.09"):
            pay = self.euro_payment(PaymentDirection.DISBURSEMENT, amount)
            BillPayment.objects.create(bill=bill, payment=pay, amount=D(amount))
        self.assertEqual((bill.amount_due(), self.balance(self.ap)), (D("0"), D("0")), "(due, AP)")

    def test_bounced_then_paid_again_at_another_rate_clears_and_keeps_only_the_second_difference(self):
        # 100 EUR @1.12345 = 112.35 (112.345 half up). First receipt @1.09876 = 109.88 (109.876), voided.
        # Second @1.2 on 1 March = 120.00. Difference on the second: 112.35 - 120.00 = -7.65 (a gain).
        from apps.sales.models import InvoicePayment
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 1))
        inv = self.invoice("100")
        first = self.euro_payment(PaymentDirection.RECEIPT, "100")
        InvoicePayment.objects.create(invoice=inv, payment=first, amount=D("100"))
        first.void()
        second = self.euro_payment(PaymentDirection.RECEIPT, "100", on=datetime.date(2026, 3, 2))
        InvoicePayment.objects.create(invoice=inv, payment=second, amount=D("100"))
        self.assertEqual((self.balance(self.ar), self.balance(self.gain), self.balance(self.loss)),
                         (D("0"), D("-7.65"), D("0")), "(AR, gain, loss)")


class HeldMoneyCycleProbe(FxSettlementTestCase):
    """A deposit and a prepayment of 100 EUR held at 1.12345 (112.35), drawn in three, with a note."""

    def setUp(self):
        super().setUp()
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.12345"), valid_from=JAN5)
        self.deposits = Account.objects.create(code="2300", name="Deposits", account_type=AccountType.LIABILITY)
        self.prepaid = Account.objects.create(code="1400", name="Prepaid", account_type=AccountType.ASSET)
        Company.objects.update(customer_deposit_account=self.deposits, vendor_prepayment_account=self.prepaid)

    def deposit(self, amount="100"):
        from apps.sales.models import SalesOrder, SalesOrderLine
        order = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                      unit_price=D("100"), revenue_account=self.revenue)
        order.confirm()
        dep = order.create_down_payment_invoice(self.ar, amount=D(amount), invoice_date=JAN5)
        dep.post()
        from apps.sales.models import InvoicePayment
        receipt = self.euro_payment(PaymentDirection.RECEIPT, amount, on=JAN5)
        InvoicePayment.objects.create(invoice=dep, payment=receipt, amount=D(amount))
        return dep

    def final_invoice(self, amount, on):
        from apps.sales.models import Invoice, InvoiceLine
        inv = Invoice.objects.create(customer=self.customer, invoice_date=on, receivable_account=self.ar,
                                     currency=self.eur)
        InvoiceLine.objects.create(invoice=inv, description="Sacks", quantity=D("1"), unit_price=D(amount),
                                   revenue_account=self.revenue)
        inv.post()
        return inv

    def test_deposit_drawn_in_three_at_a_later_rate_leaves_nothing_held(self):
        # calc.py case 2: held 112.35; draws take 37.44, 37.44, 37.47; invoices booked at 1.2: 40.00, 40.00, 40.01
        from apps.sales.models import Invoice
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        dep = self.deposit()
        for amount in ("33.33", "33.33", "33.34"):
            inv = self.final_invoice(amount, datetime.date(2026, 2, 2))
            inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D(amount))
        self.assertEqual((self.balance(self.deposits), self.balance(self.ar), self.balance(self.loss)),
                         (D("0"), D("0"), D("7.66")), "(deposits, AR, FX loss)")

    def test_deposit_credited_back_in_part_then_drawn_to_nothing(self):
        # 100 held (112.35). Note 20 EUR (22.47 at 1.12345: 22.469). Drawn 80 at 1.2: takes 112.35-22.47 = 89.88.
        from apps.sales.models import Invoice
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        dep = self.deposit()
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("20"))
        inv = self.final_invoice("80", datetime.date(2026, 2, 2))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("80"))
        self.assertEqual((self.balance(self.deposits), Invoice.objects.get(pk=dep.pk).deposit_unapplied()),
                         (D("0"), D("0")), "(deposits, left)")

    def test_deposit_drawn_then_noted_in_the_rest(self):
        # draw 60 first (takes round(60*1.12345)=67.41), then credit the other 40: note takes what is left?
        from apps.sales.models import Invoice
        dep = self.deposit()
        inv = self.final_invoice("60", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("60"))
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("40"))
        self.assertEqual(self.balance(self.deposits), D("0"), "deposits after the last 40 is credited back")

    def test_prepayment_drawn_in_three_leaves_nothing_held(self):
        from apps.purchasing.models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 2, 1))
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        from apps.purchasing.models import BillPayment
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        for amount in ("33.33", "33.33", "33.34"):
            bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 2, 2),
                                       payable_account=self.ap, currency=self.eur)
            BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D(amount),
                                    expense_account=self.expense)
            bill.post()
            bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D(amount))
        self.assertEqual((self.balance(self.prepaid), self.balance(self.ap)), (D("0"), D("0")), "(prepaid, AP)")

    def test_deposit_drawn_in_half_then_the_other_half_credited_back_leaves_nothing_held(self):
        # held 112.35. Draw 50 takes round(56.1725)=56.17. The last 50 credited back books round(56.1725)=56.17.
        # 112.35 - 56.17 - 56.17 = 0.01 stays on Deposits unless the note, as the last, takes what is left.
        from apps.sales.models import Invoice
        dep = self.deposit()
        inv = self.final_invoice("50", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("50"))
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("50"))
        self.assertEqual((Invoice.objects.get(pk=dep.pk).deposit_unapplied(), self.balance(self.deposits)),
                         (D("0"), D("0")), "(left in EUR, Deposits in base)")

    def test_prepayment_drawn_in_half_then_the_other_half_debited_back_leaves_nothing_held(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment, PurchaseOrder, PurchaseOrderLine
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 6), payable_account=self.ap,
                                   currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D("50"),
                                expense_account=self.expense)
        bill.post()
        bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D("50"))
        Bill.objects.get(pk=pre.pk).create_debit_note(amount=D("50"))
        self.assertEqual(self.balance(self.prepaid), D("0"), "Prepaid in base")

    def test_setting_moved_after_the_deposit_then_credited_back_and_drawn_leaves_both_accounts_empty(self):
        from apps.sales.models import Invoice
        dep = self.deposit()
        moved = Account.objects.create(code="2310", name="Advances", account_type=AccountType.LIABILITY)
        Company.objects.update(customer_deposit_account=moved)
        Invoice.objects.get(pk=dep.pk).create_credit_note(amount=D("20"))
        inv = self.final_invoice("80", datetime.date(2026, 1, 6))
        inv.apply_deposit(Invoice.objects.get(pk=dep.pk), amount=D("80"))
        self.assertEqual((self.balance(self.deposits), self.balance(moved)), (D("0"), D("0")), "(old, new)")

    def test_setting_moved_after_the_prepayment_then_debited_back_and_drawn_leaves_both_accounts_empty(self):
        from apps.purchasing.models import Bill, BillLine, BillPayment, PurchaseOrder, PurchaseOrderLine
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JAN5, currency=self.eur)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("3"),
                                         unit_price=D("100"))
        order.confirm()
        pre = order.create_prepayment_bill(self.ap, amount=D("100"))
        pre.post()
        pay = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=pre, payment=pay, amount=D("100"))
        moved = Account.objects.create(code="1401", name="Advances", account_type=AccountType.ASSET)
        Company.objects.update(vendor_prepayment_account=moved)
        Bill.objects.get(pk=pre.pk).create_debit_note(amount=D("20"))
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 6), payable_account=self.ap,
                                   currency=self.eur)
        BillLine.objects.create(bill=bill, description="Parts", quantity=D("1"), unit_price=D("80"),
                                expense_account=self.expense)
        bill.post()
        bill.apply_prepayment(Bill.objects.get(pk=pre.pk), amount=D("80"))
        self.assertEqual((self.balance(self.prepaid), self.balance(moved)), (D("0"), D("0")), "(old, new)")


class BouncedForeignRefundProbe(PartPaymentResidueProbe):
    test_two_receipts_one_after_the_other_clear_the_receivable = None
    test_the_payable_mirror = None
    test_bounced_then_paid_again_at_another_rate_clears_and_keeps_only_the_second_difference = None

    def test_debit_note_refund_bounced_and_refunded_again_at_another_rate(self):
        # EUR 100 @1.12345 = 112.35 billed and paid same day. Debit note 100 (112.35 receivable from the vendor).
        # Refund 1 @1.09876 = 109.88: loss 2.47; bounced -> released. Refund 2 @1.2 = 120.00: gain 7.65.
        from apps.purchasing.models import BillPayment
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 1))
        bill = self.bill("100")
        paid = self.euro_payment(PaymentDirection.DISBURSEMENT, "100", on=JAN5)
        BillPayment.objects.create(bill=bill, payment=paid, amount=D("100"))
        note = bill.create_debit_note(quantities={bill.lines.get(): D("1")})

        def refund(day):
            r = Payment.objects.create(party=self.vendor, direction=PaymentDirection.RECEIPT, payment_date=day,
                                       amount=D("100"), currency=self.eur, bank_account=self.bank,
                                       counterpart_account=self.ap)
            r.post()
            BillPayment.objects.create(bill=type(note).objects.get(pk=note.pk), payment=r, amount=D("100"))
            return r
        first = refund(datetime.date(2026, 2, 15))
        self.assertEqual(self.balance(self.loss), D("2.47"))
        first.void()
        self.assertEqual((type(note).objects.get(pk=note.pk).amount_due(), self.balance(self.loss)), (D("100.00"), D("0")))
        refund(datetime.date(2026, 3, 2))
        self.assertEqual((self.balance(self.ap), self.balance(self.loss), self.balance(self.gain),
                          type(note).objects.get(pk=note.pk).amount_due()),
                         (D("0"), D("0"), D("-7.65"), D("0")), "(AP, loss, gain, note due)")

    def test_credit_note_refund_bounced_and_refunded_again_at_another_rate(self):
        # Mirror, sales. 100 EUR @1.12345 (112.35) billed and received; credit note 100. Refund 1 @1.09876 = 109.88:
        # gain 2.47; bounced -> released. Refund 2 @1.2 = 120.00: loss 7.65. AR 0, gain 0 net, loss 7.65.
        from apps.sales.models import Invoice, InvoicePayment
        ExchangeRate.objects.create(currency=self.eur, rate=D("1.2"), valid_from=datetime.date(2026, 3, 1))
        inv = self.invoice("100")
        got = self.euro_payment(PaymentDirection.RECEIPT, "100", on=JAN5)
        InvoicePayment.objects.create(invoice=inv, payment=got, amount=D("100"))
        note = Invoice.objects.get(pk=inv.pk).create_credit_note(quantities={inv.lines.get(): D("1")})

        def refund(day):
            r = Payment.objects.create(party=self.customer, direction=PaymentDirection.DISBURSEMENT, payment_date=day,
                                       amount=D("100"), currency=self.eur, bank_account=self.bank,
                                       counterpart_account=self.ar)
            r.post()
            InvoicePayment.objects.create(invoice=Invoice.objects.get(pk=note.pk), payment=r, amount=D("100"))
            return r
        first = refund(datetime.date(2026, 2, 15))
        self.assertEqual(self.balance(self.gain), D("-2.47"))
        first.void()
        self.assertEqual((Invoice.objects.get(pk=note.pk).amount_due(), self.balance(self.gain)), (D("100.00"), D("0")))
        refund(datetime.date(2026, 3, 2))
        self.assertEqual((self.balance(self.ar), self.balance(self.gain), self.balance(self.loss),
                          Invoice.objects.get(pk=note.pk).amount_due()),
                         (D("0"), D("0"), D("7.65"), D("0")), "(AR, gain, loss, note due)")
