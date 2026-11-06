"""review_stat2 probes: O165/O158 void of a job-worker receipt; plus pure earlier_of conservation and query counts."""
import datetime
import random
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounting.models import AccountingPeriod
from apps.gst.tests_itc04 import Itc04TestCase, H1
from apps.gst.itc04 import itc04


def show(*a):
    print("RV2", *a)


class O165(Itc04TestCase):
    def test_voids(self):
        m_val = self.back(self.lamination, "100", "1400")
        m_zero = self.back(self.lamination, "50", "0")
        show("receipt dates", m_val.movement_date, m_zero.movement_date, "number", m_val.number)
        before = m_val.movement_date - datetime.timedelta(days=1)
        for label, m, day in (("value, day before", m_val, before), ("zero, day before", m_zero, before),
                              ("value, day to come", m_val, datetime.date(2099, 1, 1))):
            try:
                m.void(on_date=day)
                show(label, "voided?!")
            except ValidationError as e:
                show(label, "refused:", str(e)[:100])
            except TypeError as e:
                show(label, "TypeError", e)
        p = AccountingPeriod.objects.create(name="Jun", start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 6, 30))
        p.close()
        n = len(itc04(*H1)["received"])
        for label, m in (("value receipt in closed June, void today", m_val), ("zero receipt in closed June, void today", m_zero)):
            try:
                m.void()
                show(label, "voided; ITC-04 received rows", n, "->", len(itc04(*H1)["received"]))
            except ValidationError as e:
                show(label, "refused:", str(e)[:110])
        # a void in an OPEN month of a closed-month booking is the ordinary correction: is there another route?
        show("hint: the movement's entry", m_val.journal_entry_id)


class PureAndCounts(O165):
    def test_earlier_of_conserves(self):
        from apps.purchasing.msme import earlier_of
        rnd = random.Random(7)
        base = datetime.date(2026, 6, 1)
        bad = 0
        for trial in range(3000):
            total = D(rnd.randint(1, 500000)) / 100
            def split(total, n):
                cuts = sorted(rnd.randint(0, int(total * 100)) for _ in range(n - 1))
                parts, last = [], 0
                for c in cuts + [int(total * 100)]:
                    parts.append(D(c - last) / 100)
                    last = c
                return parts
            n1, n2 = rnd.randint(1, 4), rnd.randint(1, 4)
            rows = [{"due_date": base + datetime.timedelta(days=30 * (i + 1)), "amount": a} for i, a in enumerate(split(total, n1))]
            days = sorted(rnd.randint(10, 75) for _ in range(n2))
            act = [(base + datetime.timedelta(days=x) if rnd.random() > 0.15 else None, a) for x, a in zip(days, split(total, n2))]
            act.sort(key=lambda r: (r[0] is None, r[0] or datetime.date.max))
            out = earlier_of(rows, act, settled=D(rnd.randint(0, int(total * 100))) / 100)
            s = sum(r["amount"] for r in out)
            sorted_ok = all(out[i]["due_date"] <= out[i + 1]["due_date"] for i in range(len(out) - 1))
            if s != total or not sorted_ok:
                bad += 1
                if bad <= 3:
                    show("earlier_of FAIL total", total, "sum", s, "sorted", sorted_ok, "rows", [(r["due_date"], r["amount"]) for r in rows], "act", act, "out", [(r["due_date"], r["amount"]) for r in out])
        show("earlier_of random trials failing:", bad, "of 3000")
