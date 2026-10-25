"""review_books3 probes (O261 fuzz, O260). Each check prints PASS/FAIL; a FAIL fails the test."""
import datetime, random
from decimal import Decimal

from apps.core.models import ExchangeRate
from .models import Payment, PaymentDirection
from .tests_fx_settlement import FxSettlementTestCase

D = Decimal
JAN5 = datetime.date(2026, 1, 5)
FEB = datetime.date(2026, 2, 15)


class O261Fuzz(FxSettlementTestCase):
    def build(self, rnd, side, order, notes, void_refund):
        """side sales|purchasing; order payment_first|note_first. Returns (verdict, detail)."""
        r1 = D(rnd.randint(100000, 130000)) / D(100000)
        r2 = D(rnd.randint(100000, 130000)) / D(100000)
        r1, r2 = D(r1).quantize(D("0.00001")), D(r2).quantize(D("0.00001"))
        ExchangeRate.objects.create(currency=self.eur, rate=r1, valid_from=JAN5)
        ExchangeRate.objects.create(currency=self.eur, rate=r2, valid_from=datetime.date(2026, 2, 1))
        prices = [D(rnd.randint(100, 9999)) / 100 for _ in range(3)]
        sales = side == "sales"
        ctl = self.ar if sales else self.ap
        if sales:
            from apps.sales.models import InvoicePayment as Alloc, SalesOrder, SalesOrderLine
            so = SalesOrder.objects.create(customer=self.customer, order_date=JAN5, currency=self.eur)
            for p in prices:
                SalesOrderLine.objects.create(order=so, item=self.item, uom=self.uom, quantity=D(1), unit_price=p, revenue_account=self.revenue)
            so.confirm()
            doc = so.create_invoice(self.ar, invoice_date=JAN5)
            doc.post()
            lines = list(doc.lines.order_by("pk"))
            mk = lambda q: doc.create_credit_note(quantities=q, on_date=JAN5)
            pay_dir, ref_dir, party = PaymentDirection.RECEIPT, PaymentDirection.DISBURSEMENT, self.customer
        else:
            from apps.purchasing.models import Bill, BillLine, BillPayment as Alloc
            doc = Bill.objects.create(vendor=self.vendor, bill_date=JAN5, payable_account=self.ap, currency=self.eur)
            lines = [BillLine.objects.create(bill=doc, description="P", quantity=D(1), unit_price=p, expense_account=self.expense) for p in prices]
            doc.post()
            mk = lambda q: doc.create_debit_note(quantities=q)
            pay_dir, ref_dir, party = PaymentDirection.DISBURSEMENT, PaymentDirection.RECEIPT, self.vendor
        key = "invoice" if sales else "bill"
        total = doc.total()
        paid = (total * D(rnd.randint(30, 95)) / 100).quantize(D("0.01"))

        def pay():
            doc.refresh_from_db()
            amt = min(paid, doc.amount_due())
            if amt <= 0: return None
            p = Payment.objects.create(party=party, direction=pay_dir, payment_date=FEB, amount=amt, currency=self.eur,
                                       bank_account=self.bank, counterpart_account=ctl)
            p.post()
            Alloc.objects.create(**{key: doc, "payment": p, "amount": amt})
            return p
        made = []
        if order == "payment_first": pay()
        sets = [[lines[0], lines[1]]] if notes == 1 else [[lines[0]], [lines[1], lines[2]]]
        for s in sets:
            made.append(mk({l: D(1) for l in s}))
        if order == "note_first": pay()
        refunds = []
        for n in made:
            n.refresh_from_db()
            due = n.refund_due()
            if due > 0:
                p = Payment.objects.create(party=party, direction=ref_dir, payment_date=FEB, amount=due, currency=self.eur,
                                           bank_account=self.bank, counterpart_account=ctl)
                p.post()
                Alloc.objects.create(**{key: n, "payment": p, "amount": due})
                refunds.append(p)
        if void_refund and refunds:
            refunds[0].void()
        doc.refresh_from_db()
        [n.refresh_from_db() for n in made]
        settled = doc.amount_due() == 0 and all(n.amount_due() == 0 for n in made)
        bal = self.balance(ctl)
        return settled, bal, (r1, r2, prices, paid, doc.amount_due(), [n.amount_due() for n in made])

    def test_fuzz(self):
        rnd = random.Random(261)
        fails = []
        for side in ("sales", "purchasing"):
            for order in ("payment_first", "note_first"):
                for notes in (2,):
                    for void in (False, True):
                        n = tot = 0
                        for i in range(40):
                            from django.db import transaction
                            try:
                                with transaction.atomic():
                                    seed = rnd.random()
                                    ok = self.build(random.Random(seed), side, order, notes, void)
                                    settled, bal, det = ok
                                    tot += 1
                                    if settled and bal != 0:
                                        n += 1
                                        fails.append((side, order, notes, void, bal, det))
                                    raise RuntimeError("rollback")
                            except RuntimeError:
                                pass
                            except Exception as e:  # noqa
                                fails.append((side, order, notes, void, "EXC", repr(e)[:150]))
                                break
                        print(("PASS" if n == 0 else "FAIL"), side, order, "notes=%d" % notes, "void_refund=%s" % void, "settled-with-balance %d/%d" % (n, tot))
        for f in fails[:12]: print("  ", f)
        self.assertFalse(fails)
