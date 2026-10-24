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
        # 3,000 m at 0.45 = 1,350.00 is October's wages, and the 880.00 is taken back beside it, not netted
        # (O213; this test asserted one line of 470.00 for 800 m until then): within half the wages, 675.00 now
        # and the other 205.00 from the next run.
        self.woven("3000", datetime.date(2026, 10, 5))
        third = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 15))
        (slip,) = third.payslips.all()
        lines = sorted((line.kind, line.quantity, line.amount) for line in slip.lines.filter(component=self.per_metre))
        self.assertEqual((lines, slip.net(), slip.recoverable),
                         ([(ComponentKind.DEDUCTION, Decimal("2200"), Decimal("675.00")),
                           (ComponentKind.EARNING, Decimal("3000"), Decimal("1350.00"))],
                          Decimal("675.00"), Decimal("0")))
        self.woven("1000", datetime.date(2026, 10, 20))
        fourth = self.pay(datetime.date(2026, 10, 16), datetime.date(2026, 10, 31))
        (slip,) = fourth.payslips.all()
        self.assertEqual((slip.gross(), slip.deductions(), slip.net()),
                         (Decimal("450.00"), Decimal("205.00"), Decimal("245.00")))

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

    def owed_back(self):
        # O28, the owner's decision of 9 October: what the final pay may not
        # take back is a receivable from the former employee.
        self.receivable = Account.objects.create(code="1390", name="Owed by former employees",
                                                 account_type=AccountType.ASSET)
        self.per_metre.recoverable_account = self.receivable
        self.per_metre.save()

    def balance(self, account):
        from apps.accounting.models import JournalLine

        return sum((line.debit - line.credit for line in JournalLine.objects.filter(account=account)), Decimal("0"))

    def test_with_nothing_else_to_take_it_from_it_is_owed_back(self):
        # Was refused ("400.00 deducted against 0.00 earned"), the owner decided
        # otherwise (O28): no wages, so nothing is deducted and all 400.00 is owed.
        self.owed_back()
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(20))
        (slip,) = self.pay(SEP(16), SEP(30)).payslips.all()
        self.assertEqual((slip.net(), slip.recoverable, self.balance(self.receivable), self.wages()),
                         (Decimal("0.00"), Decimal("400.00"), Decimal("400.00"), Decimal("0.00")))

    def test_more_than_half_the_final_pay_is_owed_back_not_deducted(self):
        # An allowance of 600 is the final pay: half, 300.00, is taken back on
        # the slip and the other 100.00 of the 400.00 is owed. Wages: 400 of
        # piece work and 600 of allowance in the first fortnight, 600 in the
        # second, less 300 and 100 taken back: 1,200.00.
        self.owed_back()
        allowance = PayComponent.objects.create(
            code="ALW", name="Attendance allowance", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.per_metre.expense_account)
        self.rate(allowance, "600", SEP(1))
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(30))
        (slip,) = self.pay(SEP(16), SEP(30)).payslips.all()
        taken = slip.lines.get(component=self.per_metre)
        self.assertEqual((taken.kind, taken.amount, slip.recoverable, slip.net(), self.balance(self.receivable),
                          self.wages()),
                         (ComponentKind.DEDUCTION, Decimal("300.00"), Decimal("100.00"), Decimal("300.00"),
                          Decimal("100.00"), Decimal("1200.00")))

    def test_what_they_wove_before_leaving_stays_wages_beside_what_is_taken_back(self):
        # 1,500 m at 0.45 on the 21st, 675.00, and the 400.00 taken back beside
        # it, capped at half the wages: 337.50 deducted, 62.50 owed, 337.50 net.
        # Netted to one line of 275.00 for 500 m, the recovery came off their PF
        # and ESI wages and past the cap (O28; this test asserted that until then).
        self.owed_back()
        self.paid_then_voided()
        self.woven("1500", SEP(21))
        self.weaver.terminate(on_date=SEP(25))
        (slip,) = self.pay(SEP(16), SEP(30)).payslips.all()
        lines = sorted((line.kind, line.quantity, line.amount) for line in slip.lines.filter(component=self.per_metre))
        self.assertEqual(lines, [(ComponentKind.DEDUCTION, Decimal("1000"), Decimal("337.50")),
                                 (ComponentKind.EARNING, Decimal("1500"), Decimal("675.00"))])
        self.assertEqual((slip.gross(), slip.net(), slip.recoverable), (Decimal("675.00"), Decimal("337.50"),
                                                                        Decimal("62.50")))

    def test_a_leaving_date_set_after_the_final_run_still_takes_it_back(self):
        # O31: the second half of September posted before the leaving date was
        # set, so it took nothing back (it waits for a next run), and no run
        # after it included the weaver. October's run does, and with no wages
        # all 400.00 is owed back; November's does not include them again.
        self.owed_back()
        allowance = PayComponent.objects.create(
            code="ALW", name="Attendance allowance", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.per_metre.expense_account)
        self.rate(allowance, "600", SEP(1))
        self.paid_then_voided()
        (second,) = self.pay(SEP(16), SEP(30)).payslips.all()
        self.assertFalse(second.lines.filter(component=self.per_metre).exists())
        self.weaver.terminate(on_date=SEP(30))
        october = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        self.assertEqual([slip.recoverable for slip in october.payslips.all()], [Decimal("400.00")])
        self.assertEqual(self.balance(self.receivable), Decimal("400.00"))
        with self.assertRaisesMessage(ValidationError, "Nobody is employed"):
            self.pay(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))

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


class ATakeBackOutsideAFinalRunTests(PieceworkTestCase):
    """
    O213. A loom weaver paid 400.00 for 1,000 m on 3 September, the roll
    voided after payday, and the second fortnight a run like any other.
    PF 12% of piece work; ESI 0.75% of gross, up to the rupee. What is
    taken back is a deduction beside the fortnight's own earning, and
    each statutory base reads that earning. Figures worked in
    scratchpad/calc_fix_core/o213.py.
    """

    def setUp(self):
        super().setUp()
        from apps.hr.pay_details import EmployeePayDetails
        from apps.hr.payroll import Rounding, Statutory

        owed = Account.objects.create(code="2310", name="PF and ESI payable", account_type=AccountType.LIABILITY)
        self.pf = PayComponent.objects.create(
            code="PF", name="PF", kind=ComponentKind.DEDUCTION, basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=owed, sequence=500, statutory=Statutory.PF)
        self.pf.base_components.set([self.per_metre])
        self.esi = PayComponent.objects.create(
            code="ESI", name="ESI", kind=ComponentKind.DEDUCTION, basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=owed, sequence=510, statutory=Statutory.ESI, rounding=Rounding.RUPEE_UP)
        self.rate(self.pf, "12", SEP(1))
        self.rate(self.esi, "0.75", SEP(1))
        self.weaver.uan, self.weaver.esi_number = "100200300499", "3100200399"
        self.weaver.save()
        EmployeePayDetails.objects.create(employee=self.weaver, date_of_birth=datetime.date(1990, 1, 1))
        roll = self.woven("1000", SEP(3))
        self.pay(SEP(1), SEP(15))
        roll.entry.void()

    def slip_of(self, run):
        (slip,) = run.payslips.all()
        lines = sorted((line.kind, line.quantity, line.amount) for line in slip.lines.filter(component=self.per_metre))
        return slip, lines

    def amount(self, slip, component):
        return slip.lines.get(component=component).amount

    def wages(self):
        from apps.accounting.models import JournalLine

        account = self.per_metre.expense_account
        return sum((line.debit - line.credit for line in JournalLine.objects.filter(account=account)), Decimal("0"))

    def test_pf_and_esi_wages_read_what_was_earned_in_the_period(self):
        # 4,000 m at 0.45 = 1,800.00 earned; 400.00 taken back. PF 216.00, ESI 13.50 up to 14. Net 1,170.00.
        # Netted, it was one line of 1,400.00: PF 168.00, ESI 11, and both files filed 1,400.
        from apps.hr.statutory_files import ecr_rows, esi_rows

        self.woven("4000", SEP(21))
        run = self.pay(SEP(16), SEP(30))
        slip, lines = self.slip_of(run)
        self.assertEqual(lines, [(ComponentKind.DEDUCTION, Decimal("1000"), Decimal("400.00")),
                                 (ComponentKind.EARNING, Decimal("4000"), Decimal("1800.00"))])
        self.assertEqual((self.amount(slip, self.pf), self.amount(slip, self.esi), slip.gross(), slip.net(),
                          slip.recoverable),
                         (Decimal("216.00"), Decimal("14"), Decimal("1800.00"), Decimal("1170.00"), Decimal("0")))
        # The files are one row a member a month (O301, decided): September's row holds both fortnights'
        # earnings, 400 (PF 48, the roll voided after payday) and 1,800 (PF 216), not the 400 taken back
        # (a deduction, R3): EPF wages 2,200, PF 264 = 12% of them; ESI 2,200 (calc_fix_people2/o213_month.py).
        row = ecr_rows(run)[0]
        self.assertEqual((row["epf_wages"], row["epf"], esi_rows(run)[0]["wages"]), (2200, 264, 2200))
        # Taken back from the wages it was charged to: 400 + 1,800 - 400.
        self.assertEqual(self.wages(), Decimal("1800.00"))

    def test_what_the_cap_leaves_waits_for_the_next_run(self):
        # 1,500 m at 0.45 = 675.00. Half is 337.50, less PF 81.00 and ESI 6: 250.50 taken back, 149.50 waits.
        # October's 2,000 m, 900.00, takes back the 149.50 within its own cap of 335.00. Nobody leaves, so
        # nothing is owed as a receivable; the wages end at what was woven and kept, 675 + 900.
        self.woven("1500", SEP(21))
        slip, lines = self.slip_of(self.pay(SEP(16), SEP(30)))
        self.assertEqual(lines, [(ComponentKind.DEDUCTION, Decimal("1000"), Decimal("250.50")),
                                 (ComponentKind.EARNING, Decimal("1500"), Decimal("675.00"))])
        self.assertEqual((self.amount(slip, self.pf), self.amount(slip, self.esi), slip.net(), slip.recoverable),
                         (Decimal("81.00"), Decimal("6"), Decimal("337.50"), Decimal("0")))
        self.woven("2000", datetime.date(2026, 10, 5))
        slip, lines = self.slip_of(self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 15)))
        self.assertEqual([line[2] for line in lines], [Decimal("149.50"), Decimal("900.00")])
        self.assertEqual((self.amount(slip, self.pf), self.amount(slip, self.esi), slip.net()),
                         (Decimal("108.00"), Decimal("7"), Decimal("635.50")))
        self.assertEqual(self.wages(), Decimal("1575.00"))
        self.woven("1000", datetime.date(2026, 10, 20))
        slip, lines = self.slip_of(self.pay(datetime.date(2026, 10, 16), datetime.date(2026, 10, 31)))
        self.assertEqual(lines, [(ComponentKind.EARNING, Decimal("1000"), Decimal("450.00"))])


class AFormerEmployeesShortfallIsSettledTests(PieceworkTestCase):
    """
    O215. 400.00 paid for 1,000 m, the roll voided, and the weaver gone on
    20 September with no wages to take it from: all 400.00 is owed back, a
    receivable from the former employee (O28). 150.00 is paid back and the
    Controller writes off the other 250.00. Figures: calc_fix_core/o215.py.
    """

    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.receivable = Account.objects.create(code="1390", name="Owed by former employees",
                                                 account_type=AccountType.ASSET)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.loss = Account.objects.create(code="6900", name="Bad debts", account_type=AccountType.EXPENSE)
        Company.objects.update(bad_debt_account=self.loss)
        self.per_metre.recoverable_account = self.receivable
        self.per_metre.save()
        roll = self.woven("1000", SEP(3))
        self.pay(SEP(1), SEP(15))
        roll.entry.void()
        self.weaver.terminate(on_date=SEP(20))
        self.final = self.pay(SEP(16), SEP(30))
        (self.slip,) = self.final.payslips.all()

    def balance(self, account):
        from apps.accounting.models import JournalLine

        return sum((line.debit - line.credit for line in JournalLine.objects.filter(account=account, entry__posted=True)),
                   Decimal("0"))

    def paid_back(self, amount, counterpart=None, day=SEP(30)):
        from apps.accounting.models import Payment, PaymentDirection

        payment = Payment.objects.create(party=self.weaver.party, direction=PaymentDirection.RECEIPT,
                                         payment_date=day, amount=Decimal(amount), bank_account=self.bank,
                                         counterpart_account=counterpart or self.receivable)
        payment.post()
        return payment

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def owed(self):
        from apps.hr.shortfalls import owed_back

        return owed_back(self.slip)

    def test_paid_back_then_written_off_through_the_api_as_the_controller(self):
        controller = self.as_("Controller")
        payment = self.paid_back("150.00")
        answer = controller.post("/api/hr/shortfall-settlements/", {
            "payslip": self.slip.pk, "kind": "receipt", "amount": "150.00", "payment": payment.pk}, format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        self.assertEqual((answer.json()["owed_back"], self.owed(), self.balance(self.receivable)),
                         ("250.00", Decimal("250.00"), Decimal("250.00")))
        # The Payroll Officer works the pay out; giving up on money owed is not theirs.
        refused = self.as_("Payroll Officer").post("/api/hr/shortfall-settlements/", {
            "payslip": self.slip.pk, "kind": "write_off", "amount": "250.00", "reason": "Left the state"},
            format="json")
        self.assertEqual(refused.status_code, 403)
        answer = controller.post("/api/hr/shortfall-settlements/", {
            "payslip": self.slip.pk, "kind": "write_off", "amount": "250.00", "reason": "Left the state"},
            format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        self.assertEqual((self.owed(), self.balance(self.receivable), self.balance(self.loss)),
                         (Decimal("0.00"), Decimal("0.00"), Decimal("250.00")))
        # Reversed: the write-off's entry, then the receipt and its payment. Owed again, in full.
        voided = controller.post(f"/api/hr/shortfall-settlements/{answer.json()['id']}/void/", {}, format="json")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual((self.owed(), self.balance(self.loss)), (Decimal("250.00"), Decimal("0.00")))
        receipt = self.slip.settlements.get(kind="receipt")
        self.assertEqual(controller.post(f"/api/hr/shortfall-settlements/{receipt.pk}/void/", {},
                                         format="json").status_code, 200)
        payment.refresh_from_db()
        self.assertEqual((payment.is_voided(), self.owed(), self.balance(self.receivable), self.balance(self.bank)),
                         (True, Decimal("400.00"), Decimal("400.00"), Decimal("0.00")))

    def test_what_it_is_settled_with_is_checked(self):
        from apps.hr.shortfalls import SettlementKind, ShortfallSettlement

        def receipt(payment, amount=None):
            return ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.RECEIPT,
                                                      amount=Decimal(amount or payment.amount), payment=payment)

        with self.assertRaisesMessage(ValidationError, "owes 400.00 back on that slip, not 500.00"):
            receipt(self.paid_back("500.00"))
        elsewhere = Account.objects.create(code="1391", name="Owed by contractors", account_type=AccountType.ASSET)
        with self.assertRaisesMessage(ValidationError, "so the two would never meet"):
            receipt(self.paid_back("100.00", counterpart=elsewhere))
        with self.assertRaisesMessage(ValidationError, "The payment is 100.00 and the receipt 90.00"):
            receipt(self.paid_back("100.00"), amount="90.00")
        with self.assertRaisesMessage(ValidationError, "did not pay back on 2026-09-29"):
            receipt(self.paid_back("100.00", day=SEP(29)))
        with self.assertRaisesMessage(ValidationError, "says why"):
            ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.WRITE_OFF,
                                               amount=Decimal("100.00"), reason=" ")
        self.assertEqual(self.owed(), Decimal("400.00"))

    def test_money_paid_back_and_not_yet_matched_is_not_written_off_as_well(self):
        """
        The g22 gate (pg-b): 300.00 paid back and posted against the receivable, and a write-off of 300.00
        recorded before the payment was matched as the receipt. The write-off asked only the settlements, so
        it stood, the receipt was refused, and the weaver's receivable read -200.00 with 100.00 owed back.
        """
        from apps.hr.shortfalls import SettlementKind, ShortfallSettlement

        payment = self.paid_back("300.00")
        with self.assertRaisesMessage(ValidationError, "the books show 100.00 still owed"):
            ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.WRITE_OFF,
                                               amount=Decimal("300.00"), reason="Cannot be traced")
        ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.WRITE_OFF,
                                           amount=Decimal("100.00"), reason="Cannot be traced")
        ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.RECEIPT,
                                           amount=Decimal("300.00"), payment=payment)
        self.assertEqual((self.owed(), self.balance(self.receivable)), (Decimal("0.00"), Decimal("0.00")))

    def test_a_bounced_receipt_is_owed_again_and_the_run_stands_while_a_settlement_does(self):
        from apps.hr.shortfalls import SettlementKind, ShortfallSettlement

        payment = self.paid_back("150.00")
        settled = ShortfallSettlement.objects.create(payslip=self.slip, kind=SettlementKind.RECEIPT,
                                                     amount=Decimal("150.00"), payment=payment)
        with self.assertRaisesMessage(ValidationError, "Void it before voiding the run"):
            self.final.void()
        with self.assertRaisesMessage(ValidationError, "void it instead"):
            settled.delete()
        payment.void()
        self.assertEqual(self.owed(), Decimal("400.00"))
        # The payment voided on its own leaves the receipt standing for nothing: the run may go.
        self.final.void()
        self.assertEqual((self.owed(), self.balance(self.receivable)), (Decimal("0.00"), Decimal("0.00")))
