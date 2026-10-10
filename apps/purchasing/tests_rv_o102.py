import datetime
from decimal import Decimal as D
from apps.purchasing.tests_tds import TdsTestCase


def show(*a):
    print("\nRV", *a)


class O102(TdsTestCase):
    def test_sequence(self):
        v = self.contractor(pan="AAACL1234F")
        out = []
        for amt in ("20000", "35000", "40000", "10000"):
            try:
                d = self.bill(amt, vendor=v).deduct_tds()
            except Exception:
                d = None
            out.append((amt, None if d is None else (d.base, d.amount, d.covered_bills.count())))
        show("20k,35k,40k,10k ->", out, "total tax", sum((o[1][1] for o in out if o[1]), D(0)))

    def test_reverse_the_alone_deduction_then_next(self):
        v = self.contractor(pan="AAACL1234F")
        b1 = self.bill("20000", vendor=v)
        b2 = self.bill("35000", vendor=v); d2 = b2.deduct_tds()
        d2.reverse()
        d3 = self.bill("90000", vendor=v).deduct_tds()
        show("20k, 35k(reversed), 90k ->", (d3.base, d3.amount, d3.covered_bills.count()), "year of bills 145k (35k reversed counts?)")
