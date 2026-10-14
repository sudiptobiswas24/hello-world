"""
review_cost probes: O28/O31 a leaver's recovery. Hand figures in scratchpad/calc_review_cost/pay.py.

Fixture (tests_piecework): weaver at 0.40 a metre to 15 Sep and 0.45 from the 16th. 1,000 m on 3 Sep paid 400.00
in the first fortnight, then the roll voided: 400.00 to take back.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType, JournalLine
from apps.hr.payroll import ComponentBasis, ComponentKind, PayComponent, PayRun, Statutory

from .tests_piecework import SEP, PieceworkTestCase

D = Decimal


class LeaverProbes(PieceworkTestCase):
    def setUp(self):
        super().setUp()
        self.receivable = Account.objects.create(code="1390", name="Owed by former employees",
                                                 account_type=AccountType.ASSET)
        self.per_metre.recoverable_account = self.receivable
        self.per_metre.save()
        self.advance_payable = Account.objects.create(code="2400", name="Advances", account_type=AccountType.LIABILITY)
        self.pf_payable = Account.objects.create(code="2310", name="PF payable", account_type=AccountType.LIABILITY)

    def bal(self, account):
        return sum((row.debit - row.credit for row in JournalLine.objects.filter(account=account, entry__posted=True)),
                   D("0"))

    def allowance(self, amount):
        component = PayComponent.objects.create(
            code="ALW", name="Allowance", kind=ComponentKind.EARNING, basis=ComponentBasis.FIXED,
            expense_account=self.per_metre.expense_account, sequence=5)
        self.rate(component, amount, SEP(1))
        return component

    def paid_then_voided(self):
        roll = self.woven("1000", SEP(3))
        self.pay(SEP(1), SEP(15))
        roll.entry.void()

    def test_other_deductions_count_against_the_cap(self):
        """
        Allowance 600 is the final pay; an advance of 150 is also taken. Half of 600 is 300: 150 of it is the
        advance, 150 of the 400 is taken back, 250 is owed. Net 600 - 150 - 150 = 300; receivable 250.
        """
        self.allowance("600")
        advance = PayComponent.objects.create(
            code="ADV", name="Advance recovery", kind=ComponentKind.DEDUCTION, basis=ComponentBasis.FIXED,
            liability_account=self.advance_payable, sequence=90)
        self.rate(advance, "150", SEP(1))
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(30))
        (slip,) = self.pay(SEP(16), SEP(30)).payslips.all()
        taken = slip.lines.get(component=self.per_metre)
        self.assertEqual((taken.amount, slip.recoverable, slip.net(), self.bal(self.receivable)),
                         (D("150.00"), D("250.00"), D("300.00"), D("250.00")))

    def test_the_cap_is_never_above_half_of_an_odd_paisa(self):
        """Allowance 333.33: half is 166.665. Taking 166.67 takes more than half the wages."""
        self.allowance("333.33")
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(30))
        (slip,) = self.pay(SEP(16), SEP(30)).payslips.all()
        taken = slip.lines.get(component=self.per_metre)
        self.assertLessEqual(taken.amount * 2, slip.gross())

    def test_voiding_the_final_run_reverses_the_receivable_and_a_rerun_takes_it_again(self):
        self.allowance("600")
        self.paid_then_voided()
        self.weaver.terminate(on_date=SEP(30))
        final = self.pay(SEP(16), SEP(30))
        self.assertEqual(self.bal(self.receivable), D("100.00"))
        final.void()
        self.assertEqual(self.bal(self.receivable), D("0.00"))
        again = self.pay(SEP(16), SEP(30))
        (slip,) = again.payslips.all()
        self.assertEqual((slip.lines.get(component=self.per_metre).amount, slip.recoverable, self.bal(self.receivable)),
                         (D("300.00"), D("100.00"), D("100.00")))

    def test_voiding_the_october_run_of_a_late_leaver_puts_it_back_to_be_owed_again(self):
        self.allowance("600")
        self.paid_then_voided()
        self.pay(SEP(16), SEP(30))
        self.weaver.terminate(on_date=SEP(30))
        october = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        self.assertEqual(self.bal(self.receivable), D("400.00"))
        october.void()
        self.assertEqual(self.bal(self.receivable), D("0.00"))
        again = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        self.assertEqual([slip.recoverable for slip in again.payslips.all()], [D("400.00")])

    def test_pf_wages_of_a_final_slip_are_the_earning_not_earning_plus_what_was_taken_back(self):
        """
        PF 12% of piece work. 1,500 m at 0.45 = 675.00 on the 21st, 400.00 to take back. Earning 675.00, PF 81.00 on
        it. Cap: half of 675 = 337.50 less PF 81.00 = 256.50 taken back, 143.50 owed. The ECR's PF wages must read
        675, the base PF was taken of; summed with the deduction line (256.50) it reads 931.50.
        """
        from apps.hr.statutory_files import ecr_rows

        pf = PayComponent.objects.create(
            code="PF", name="PF", kind=ComponentKind.DEDUCTION, basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=self.pf_payable, sequence=500, statutory=Statutory.PF)
        pf.base_components.set([self.per_metre])
        self.rate(pf, "12", SEP(1))
        self.weaver.uan = "100200300499"
        self.weaver.save()
        self.paid_then_voided()
        self.woven("1500", SEP(21))
        self.weaver.terminate(on_date=SEP(25))
        run = self.pay(SEP(16), SEP(30))
        (slip,) = run.payslips.all()
        self.assertEqual(slip.lines.get(component=pf).amount, D("81.00"))
        (row,) = ecr_rows(run)
        self.assertEqual(row["epf_wages"], 675)

    def test_a_no_wage_slip_does_not_break_the_run_s_files_and_registers(self):
        """O31's slip has no lines; the run's register, bank advice and ECR must still read."""
        self.allowance("600")
        self.paid_then_voided()
        self.pay(SEP(16), SEP(30))
        self.weaver.terminate(on_date=SEP(30))
        october = self.pay(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        (slip,) = october.payslips.all()
        self.assertEqual((slip.gross(), slip.net(), slip.recoverable), (D("0.00"), D("0.00"), D("400.00")))
        from apps.hr.statutory_files import ecr_rows, esi_rows

        ecr_rows(october)
        esi_rows(october)
