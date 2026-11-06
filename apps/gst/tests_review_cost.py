"""review_cost probes: O187 a job-worker value receipt voided in a later month, both periods' ITC-04."""

import datetime
from decimal import Decimal

from apps.manufacturing.outside import OutsideMovement

from .itc04 import itc04
from .tests_itc04 import H1, Itc04TestCase

D = Decimal
H2 = (datetime.date(2026, 10, 1), datetime.date(2027, 3, 31))


class VoidedLaterProbes(Itc04TestCase):
    def receipt(self, quantity, value, day):
        movement = OutsideMovement.objects.create(
            operation=self.lamination.operations.get(is_outside=True), movement_date=day, is_return=False,
            quantity=D(quantity), value=D(value), credit_account=self.grni)
        movement.post()
        return movement

    def rows(self, period):
        return [(r["original_challan"], r["quantity"], r["date"]) for r in itc04(*period)["received"]]

    def test_two_receipts_the_first_voided_in_the_second_half_year(self):
        """
        R1 700 kg on 1 Jun (600 on challan 1, 100 on challan 2), R2 150 kg on 1 Aug. R1 voided 14 Oct.
        H1 must still read what it reported: 600, 100, 150 (R2 against challan 2). H2: the two withdrawals, negative,
        against the challans R1 was laid on, and nothing else.
        """
        r1 = self.receipt("700", "1400", datetime.date(2026, 6, 1))
        self.receipt("150", "300", datetime.date(2026, 8, 1))
        h1_before = self.rows(H1)
        r1.void(on_date=datetime.date(2026, 10, 14))
        self.assertEqual(self.rows(H1), h1_before)
        self.assertEqual(sorted(self.rows(H2)),
                         sorted([(self.first.number, D("-600"), datetime.date(2026, 10, 14)),
                                 (self.second.number, D("-100"), datetime.date(2026, 10, 14))]))

    def test_a_receipt_booked_and_voided_in_the_second_half_year_is_on_neither(self):
        r1 = self.receipt("700", "1400", datetime.date(2026, 6, 1))
        r2 = self.receipt("200", "400", datetime.date(2026, 10, 2))
        r2.void(on_date=datetime.date(2026, 10, 14))
        self.assertEqual(self.rows(H2), [])
        self.assertEqual(sum(q for _c, q, _d in self.rows(H1)), D("700"))
        del r1

    def test_voided_in_the_second_half_year_and_a_new_receipt_comes_in_that_half_year(self):
        """R1 700 (1 Jun) voided 14 Oct, R3 300 booked 20 Oct: H2 = -600, -100 and +300 laid from the first challan."""
        r1 = self.receipt("700", "1400", datetime.date(2026, 6, 1))
        r1.void(on_date=datetime.date(2026, 10, 14))
        self.receipt("300", "600", datetime.date(2026, 10, 20))
        net = {}
        for challan, quantity, _day in self.rows(H2):
            net[challan] = net.get(challan, D("0")) + quantity
        # Reported over both halves, the challans stand at what is back now: 300 on challan 1.
        total = {}
        for challan, quantity, _day in self.rows(H1) + self.rows(H2):
            total[challan] = total.get(challan, D("0")) + quantity
        self.assertEqual(sum(total.values()), D("300"))
