"""
A weaver on the company's own loom L-30, paid 0.40 a metre to 15 Sep and
0.45 from 16 Sep. At 104.4 g a metre on a 2.4 kg core, a roll of N
metres weighs 0.1044 N + 2.4 kg gross.

  Run 1, 1-15 Sep: 1,000 m on the 3rd and 1,200 m on the 10th,
  2,200 m at 0.40 = 880.00.
  After it posts, the 1,200 m roll is voided and weighed again at
  1,150 m, a 300 m roll from the 14th turns up late, and 1,000 m is
  woven on the 21st at 0.45.
  Run 2, 16-30 Sep: earned to date 400 + 460 + 120 + 450 = 1,430.00,
  less 880.00 paid = 550.00, for 3,450 - 2,200 = 1,250 m.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from apps.core.models import Company
from apps.hr.models import EmploymentStatus
from apps.hr.payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, PayRun

from .machines import Machine
from .tests_station import StationTestCase, at
from .tests_station_api import StationApiTestCase

SEP = lambda day: datetime.date(2026, 9, day)  # noqa: E731


def gross(metres):
    return str(Decimal("0.1044") * Decimal(metres) + Decimal("2.4"))


class PieceworkTestCase(StationTestCase):
    def setUp(self):
        super().setUp()
        self.l30 = Machine.objects.create(work_centre=self.centre, code="L-30")
        self.station.machines.add(self.l30)
        self.weaver = self.employee("EMP-0300", "Weaver")
        wages = Account.objects.create(code="6000", name="Wages",
                                       account_type=AccountType.EXPENSE)
        net_pay = Account.objects.create(code="2300", name="Net pay",
                                         account_type=AccountType.LIABILITY)
        Company.objects.update(net_pay_account=net_pay)
        self.per_metre = PayComponent.objects.create(
            code="PC-M", name="Weaving, per metre", kind=ComponentKind.EARNING,
            basis=ComponentBasis.PER_UNIT, measure="metres_woven", expense_account=wages)
        self.per_kg = PayComponent.objects.create(
            code="PC-KG", name="Weaving, per kg", kind=ComponentKind.EARNING,
            basis=ComponentBasis.PER_UNIT, measure="kilograms_woven", expense_account=wages)
        self.rate(self.per_metre, "0.40", SEP(1), SEP(15))
        self.rate(self.per_metre, "0.45", SEP(16))

    def rate(self, component, amount, start, end=None, employee=None):
        EmployeeCompensation.objects.create(
            employee=employee or self.weaver, component=component, amount=Decimal(amount),
            effective_from=start, effective_to=end)

    def woven(self, metres, day, loom=None, weaver=None, declared=None):
        return self.weigh(gross=gross(metres), declared=str(declared or metres),
                          machine=loom or self.l30, when=at(day, 10),
                          weaver=weaver or self.weaver)

    def pay(self, start, end, post=True):
        run = PayRun.objects.create(period_start=start, period_end=end, pay_date=end)
        run.calculate(employees=[self.weaver])
        if post:
            run.post()
        return run

    def line(self, run, component=None):
        (slip,) = run.payslips.all()
        found = slip.lines.filter(component=component or self.per_metre).first()
        return None if found is None else (found.quantity, found.amount, found.rate)


class PaidByTheMetreTests(PieceworkTestCase):
    def test_a_fortnight_then_corrections_and_late_rolls_in_the_next(self):
        self.woven("1000", SEP(3))
        wrong = self.woven("1200", SEP(10))
        first = self.pay(SEP(1), SEP(15))
        self.assertEqual(self.line(first), (Decimal("2200"), Decimal("880.00"), Decimal("0.40")))
        wrong.entry.void()
        self.woven("1150", SEP(10))
        self.woven("300", SEP(14))
        self.woven("1000", SEP(21))
        second = self.pay(SEP(16), SEP(30))
        self.assertEqual(self.line(second), (Decimal("1250"), Decimal("550.00"), Decimal("0.45")))

    def test_more_taken_back_than_earned_waits_for_the_next_run(self):
        rolls = [self.woven("1000", SEP(3)), self.woven("1200", SEP(10))]
        self.pay(SEP(1), SEP(15))
        for roll in rolls:
            roll.entry.void()
        # Owed nothing, so nothing to post: 880.00 is still to come back.
        second = self.pay(SEP(16), SEP(30), post=False)
        self.assertIsNone(self.line(second))
        second.delete()
        # 3,000 m at 0.45 = 1,350.00, less 880.00 = 470.00, for 3,000 - 2,200 m.
        self.woven("3000", datetime.date(2026, 10, 5))
        third = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 15))
        self.assertEqual(self.line(third)[:2], (Decimal("800"), Decimal("470.00")))

    def test_a_voided_run_is_paid_again_by_the_next(self):
        self.woven("1000", SEP(3))
        first = self.pay(SEP(1), SEP(15))
        first.void()
        self.woven("1000", SEP(21))
        self.assertEqual(self.line(self.pay(SEP(16), SEP(30)))[:2],
                         (Decimal("2000"), Decimal("850.00")))

    def test_a_month_across_the_rate_change_is_one_line_at_each_days_rate(self):
        # 1,000 m at 0.40 + 1,000 m at 0.45 = 850.00, once.
        self.woven("1000", SEP(3))
        self.woven("1000", SEP(21))
        run = self.pay(SEP(1), SEP(30))
        (slip,) = run.payslips.all()
        (line,) = slip.lines.all()
        self.assertEqual((line.quantity, line.amount), (Decimal("2000"), Decimal("850.00")))

    def test_what_is_woven_after_the_period_waits_for_its_own(self):
        self.woven("1000", SEP(3))
        self.woven("1000", SEP(21))
        self.assertEqual(self.line(self.pay(SEP(1), SEP(15)))[:2],
                         (Decimal("1000"), Decimal("400.00")))
        self.assertEqual(self.line(self.pay(SEP(16), SEP(30)))[:2],
                         (Decimal("1000"), Decimal("450.00")))

    def test_two_fortnights_calculated_before_either_posts_pay_september_once(self):
        # 1,000 m on the 3rd at 0.40 and 1,000 m on the 21st at 0.45: 850.00 in all. Each run
        # calculated from nothing posted paid 400.00 and 850.00.
        self.woven("1000", SEP(3))
        self.woven("1000", SEP(21))
        first = self.pay(SEP(1), SEP(15), post=False)
        second = self.pay(SEP(16), SEP(30), post=False)
        first.post()
        with self.assertRaisesMessage(ValidationError, "was worked out at 850.00 and comes to 450.00 now"):
            second.post()
        second.calculate(employees=[self.weaver])
        second.post()
        self.assertEqual((self.line(second), first.gross() + second.gross()),
                         ((Decimal("1000"), Decimal("450.00"), Decimal("0.45")), Decimal("850.00")))

    def test_the_later_fortnight_posted_first_stops_the_earlier(self):
        self.woven("1000", SEP(3))
        self.woven("1000", SEP(21))
        first = self.pay(SEP(1), SEP(15), post=False)
        second = self.pay(SEP(16), SEP(30), post=False)
        second.post()
        with self.assertRaisesMessage(ValidationError, "paid in order"):
            first.post()
        self.assertEqual(second.gross(), Decimal("850.00"))

    def test_paid_in_order(self):
        self.woven("1000", SEP(21))
        self.pay(SEP(16), SEP(30))
        with self.assertRaisesMessage(ValidationError, "paid in order"):
            self.pay(SEP(1), SEP(15), post=False)

    def test_before_the_rate_began_it_was_not_piece_work(self):
        self.woven("500", datetime.date(2026, 8, 31))
        self.woven("1000", SEP(3))
        self.assertEqual(self.line(self.pay(SEP(1), SEP(15)))[:2],
                         (Decimal("1000"), Decimal("400.00")))

    def test_metres_the_station_doubted_are_paid_at_what_the_weight_supports(self):
        roll = self.woven("900", SEP(3), declared="1000")
        self.assertTrue(roll.is_metres_exception)
        self.assertEqual(self.line(self.pay(SEP(1), SEP(15)))[:2],
                         (Decimal("900.00"), Decimal("360.00")))

    def test_by_the_kilogram(self):
        self.rate(self.per_kg, "4", SEP(1))
        self.woven("1000", SEP(3))
        self.assertEqual(self.line(self.pay(SEP(1), SEP(15)), self.per_kg)[:2],
                         (Decimal("104.4000"), Decimal("417.60")))


class ALeaversFinalRunTests(PieceworkTestCase):
    """
    Piece work is paid by difference, so a roll voided after payday is
    taken back from the next run, and somebody leaving has none after the
    run their leaving date falls in. 1,000 m woven on 3 September and
    paid at 0.40, 400.00, then voided.
    """

    def paid_then_voided(self):
        roll = self.woven("1000", SEP(3))
        self.pay(SEP(1), SEP(15))
        roll.entry.void()

    def wages(self):
        from apps.accounting.models import JournalLine

        account = self.per_metre.expense_account
        return sum((line.debit - line.credit for line in JournalLine.objects.filter(account=account)), Decimal("0"))

    def test_with_nothing_else_to_take_it_from_the_final_run_is_refused_by_name(self):
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(20))
        with self.assertRaisesMessage(ValidationError, "400.00 deducted against 0.00 earned"):
            self.pay(SEP(16), SEP(30))

    def test_what_they_wove_before_leaving_pays_it_back_first(self):
        # 1,500 m at 0.45 on the 21st, 675.00, less the 400.00: 275.00 for 500 m.
        self.paid_then_voided()
        self.woven("1500", SEP(21))
        self.weaver.terminate(on_date=SEP(25))
        self.assertEqual(self.line(self.pay(SEP(16), SEP(30))), (Decimal("500"), Decimal("275.00"), Decimal("0.45")))

    def test_the_rest_comes_off_their_final_pay_once_and_a_void_puts_it_back(self):
        from apps.hr.payroll import PayslipLine

        allowance = PayComponent.objects.create(
            code="ALW", name="Attendance allowance", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.per_metre.expense_account)
        self.rate(allowance, "1000", SEP(1))
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(20))
        final = self.pay(SEP(16), SEP(30))
        (slip,) = final.payslips.all()
        taken = slip.lines.get(component=self.per_metre)
        self.assertEqual((taken.kind, taken.quantity, taken.amount, slip.net(), self.wages()),
                         (ComponentKind.DEDUCTION, Decimal("1000"), Decimal("400.00"), Decimal("600.00"),
                          Decimal("2000.00")))
        # Wages taken back are owed to nobody: the liabilities report does not list them.
        self.assertIsNone(PayslipLine.objects.get(pk=taken.pk).posted_liability_account)
        final.void()
        self.assertEqual(self.wages(), Decimal("1400.00"))
        again = self.pay(SEP(16), SEP(30))
        self.assertEqual((again.payslips.get().lines.get(component=self.per_metre).amount, self.wages()),
                         (Decimal("400.00"), Decimal("2000.00")))


class WhoIsPaidTests(PieceworkTestCase):
    def test_the_weaver_not_the_weigher_nor_another_weaver(self):
        other = self.employee("EMP-0301", "Other weaver")
        self.rate(self.per_metre, "0.40", SEP(1), employee=self.operator)
        self.woven("1000", SEP(3))
        self.woven("1200", SEP(3), weaver=other)
        run = PayRun.objects.create(period_start=SEP(1), period_end=SEP(15), pay_date=SEP(15))
        run.calculate(employees=[self.weaver, self.operator])
        slips = {slip.employee: slip for slip in run.payslips.all()}
        self.assertEqual(slips[self.weaver].lines.get().quantity, Decimal("1000"))
        self.assertFalse(slips[self.operator].lines.exists())

    def test_nobody_of_ours_is_credited_with_a_contractors_cloth(self):
        with self.assertRaisesMessage(ValidationError, "pays its own weavers"):
            self.woven("1000", SEP(3), loom=self.l17)

    def test_not_somebody_who_has_left(self):
        leaver = self.employee("EMP-0302", "Leaver", termination_date=SEP(1),
                               employment_status=EmploymentStatus.TERMINATED)
        with self.assertRaisesMessage(ValidationError, "not weaving"):
            self.woven("1000", SEP(3), weaver=leaver)


class WhatCanBeCountedTests(PieceworkTestCase):
    def test_a_measure_nothing_records_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "counts bales_stitched"):
            PayComponent.objects.create(code="PC-B", name="Bales", kind=ComponentKind.EARNING,
                                        basis=ComponentBasis.PER_UNIT, measure="bales_stitched",
                                        expense_account=self.per_metre.expense_account)
        with self.assertRaisesMessage(ValidationError, "read by nobody"):
            PayComponent.objects.create(code="PC-F", name="Flat", kind=ComponentKind.EARNING,
                                        measure="metres_woven",
                                        expense_account=self.per_metre.expense_account)
        with self.assertRaisesMessage(ValidationError, "something earned"):
            PayComponent.objects.create(code="PC-D", name="Docked",
                                        kind=ComponentKind.DEDUCTION,
                                        basis=ComponentBasis.PER_UNIT, measure="metres_woven",
                                        liability_account=self.per_metre.expense_account)


class WeaverAtTheStationTests(StationApiTestCase):
    def test_the_weaver_signs_the_roll_with_their_own_pin(self):
        loom = Machine.objects.create(work_centre=self.centre, code="L-30")
        self.station.machines.add(loom)
        weaver = self.employee("EMP-0300", "Weaver")
        weaver_pin = weaver.issue_pin()
        self.sign_in()
        response = self.roll(loom="L-30", declared_m="1000", weaver_pin=weaver_pin)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["woven_by"]["number"], "EMP-0300")
        response = self.roll(loom="L-30", declared_m="1000", weaver_pin="999999")
        self.assertEqual(response.status_code, 400)
