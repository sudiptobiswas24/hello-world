"""
Payroll, and the ledger it reaches.

HR modelled people and their days off and posted nothing, ever — which
for most companies in this size range leaves the single largest expense
line out of the accounts entirely.

The assertions that matter here are the balancing ones. The entry must
balance; the company's cost must be gross plus employer contributions
and not what anybody receives; and net pay payable must come back to
zero once people have been paid, because a control account that never
clears grows by a month's wages every month.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import (
    Account,
    AccountType,
    JournalLine,
    Payment,
    PaymentDirection,
)
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
)

from .models import (
    ComponentBasis,
    ComponentKind,
    Department,
    Employee,
    EmployeeCompensation,
    EmploymentStatus,
    LeavePolicy,
    LeaveRequest,
    LeaveType,
    PayComponent,
    PayRun,
    PayRunStatus,
    Payslip,
    PublicHoliday,
)


class PayrollTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.wages = acc("6000", "Wages", AccountType.EXPENSE)
        self.engineering = acc("6010", "Wages - engineering", AccountType.EXPENSE)
        self.employer_tax = acc("6100", "Employer contributions", AccountType.EXPENSE)
        self.tax_payable = acc("2200", "Tax payable", AccountType.LIABILITY)
        self.pension_payable = acc("2210", "Pension payable", AccountType.LIABILITY)
        self.net_pay = acc("2300", "Net pay payable", AccountType.LIABILITY)
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        Company.objects.create(
            name="Test Co", base_currency=self.usd, net_pay_account=self.net_pay
        )

        self.salary = PayComponent.objects.create(
            code="SAL", name="Salary", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.wages,
            reduces_for_unpaid_leave=True, sequence=10,
        )
        self.bonus = PayComponent.objects.create(
            code="BON", name="Bonus", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.wages, sequence=20,
        )
        self.tax = PayComponent.objects.create(
            code="TAX", name="Income tax", kind=ComponentKind.DEDUCTION,
            basis=ComponentBasis.PERCENT_OF_GROSS, liability_account=self.tax_payable,
            sequence=50,
        )
        self.pension = PayComponent.objects.create(
            code="PEN", name="Pension", kind=ComponentKind.DEDUCTION,
            basis=ComponentBasis.PERCENT_OF_GROSS,
            liability_account=self.pension_payable, sequence=60,
        )
        self.employer_pension = PayComponent.objects.create(
            code="EPEN", name="Employer pension", kind=ComponentKind.EMPLOYER_COST,
            basis=ComponentBasis.PERCENT_OF_GROSS, expense_account=self.employer_tax,
            liability_account=self.pension_payable, sequence=70,
        )

    def employee(self, code, hire=datetime.date(2020, 1, 1), **kwargs):
        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(
            party=party, employee_number=code, hire_date=hire, **kwargs
        )

    def pay(self, person, component, amount, since=datetime.date(2020, 1, 1)):
        return EmployeeCompensation.objects.create(
            employee=person, component=component, amount=Decimal(amount),
            effective_from=since,
        )

    # Not called run(): that is unittest.TestCase.run, and shadowing it
    # hands the test runner's TestResult in as a period start date.
    def pay_run(self, start=datetime.date(2026, 6, 1), end=datetime.date(2026, 6, 30),
                pay_date=datetime.date(2026, 6, 30)):
        return PayRun.objects.create(
            period_start=start, period_end=end, pay_date=pay_date, name="June"
        )

    def balance(self, account):
        return sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(account=account)),
            Decimal("0"),
        )


class CalculationTests(PayrollTestCase):
    def test_a_salary_is_the_whole_slip_when_there_is_nothing_else(self):
        person = self.employee("E1")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        slip = run.payslips.get()
        self.assertEqual(slip.gross(), Decimal("5000.00"))
        self.assertEqual(slip.net(), Decimal("5000.00"))

    def test_a_percentage_is_taken_of_what_came_before_it(self):
        person = self.employee("E2")
        self.pay(person, self.salary, "5000")
        self.pay(person, self.bonus, "1000")
        self.pay(person, self.tax, "20")
        run = self.pay_run()
        run.calculate()
        slip = run.payslips.get()
        self.assertEqual(slip.gross(), Decimal("6000.00"))
        self.assertEqual(slip.deductions(), Decimal("1200.00"))
        self.assertEqual(slip.net(), Decimal("4800.00"))

    def test_a_component_that_is_not_taxable_stays_out_of_the_percentage(self):
        reimbursement = PayComponent.objects.create(
            code="EXP", name="Expenses", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, expense_account=self.wages,
            is_taxable=False, sequence=30,
        )
        person = self.employee("E3")
        self.pay(person, self.salary, "5000")
        self.pay(person, reimbursement, "400")
        self.pay(person, self.tax, "20")
        run = self.pay_run()
        run.calculate()
        slip = run.payslips.get()
        self.assertEqual(slip.gross(), Decimal("5400.00"))
        self.assertEqual(slip.taxable_gross(), Decimal("5000.00"))
        self.assertEqual(slip.deductions(), Decimal("1000.00"))

    def test_an_hourly_component_needs_hours(self):
        hourly = PayComponent.objects.create(
            code="HR", name="Hourly", kind=ComponentKind.EARNING,
            basis=ComponentBasis.PER_HOUR, expense_account=self.wages, sequence=5,
        )
        person = self.employee("E4")
        self.pay(person, hourly, "25")
        run = self.pay_run()
        with self.assertRaises(ValidationError) as caught:
            run.calculate()
        self.assertIn("no approved hours", str(caught.exception))

    def test_hours_times_the_rate(self):
        hourly = PayComponent.objects.create(
            code="HR", name="Hourly", kind=ComponentKind.EARNING,
            basis=ComponentBasis.PER_HOUR, expense_account=self.wages, sequence=5,
        )
        person = self.employee("E5")
        self.pay(person, hourly, "25")
        run = self.pay_run()
        run.calculate(hours={person: Decimal("140")})
        self.assertEqual(run.payslips.get().gross(), Decimal("3500.00"))

    def test_the_rate_is_frozen_onto_the_slip(self):
        # A payslip that restates itself after the next rise cannot be
        # reconciled to the entry that paid it.
        person = self.employee("E6")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        line = run.payslips.get().lines.get()
        self.assertEqual(line.rate, Decimal("5000.0000"))
        self.assertEqual(line.amount, Decimal("5000.00"))

    def test_recalculating_replaces_rather_than_adds(self):
        person = self.employee("E7")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        run.calculate()
        self.assertEqual(run.payslips.count(), 1)
        self.assertEqual(run.payslips.get().lines.count(), 1)

    def test_nobody_employed_means_nothing_to_run(self):
        run = self.pay_run()
        with self.assertRaises(ValidationError) as caught:
            run.calculate()
        self.assertIn("Nobody is employed", str(caught.exception))


class ProrationTests(PayrollTestCase):
    def test_a_mid_month_joiner_is_paid_for_the_days_they_were_here(self):
        # June 2026 has 22 working days; joining on the 16th leaves 11.
        person = self.employee("J1", hire=datetime.date(2026, 6, 16))
        self.pay(person, self.salary, "4400", since=datetime.date(2026, 6, 16))
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("2200.00"))

    def test_a_mid_month_leaver_is_paid_up_to_their_last_day(self):
        person = self.employee("J2")
        person.termination_date = datetime.date(2026, 6, 15)
        person.employment_status = EmploymentStatus.TERMINATED
        person.save()
        self.pay(person, self.salary, "4400")
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("2200.00"))

    def test_unpaid_leave_comes_off_the_salary(self):
        # The join between the two halves of the module: leave knows the
        # days, payroll knows what a day costs.
        unpaid = LeavePolicy.objects.create(
            code="UNP", name="Unpaid", leave_type="unpaid",
            annual_days=Decimal("0"), allows_negative=True, is_paid=False,
        )
        person = self.employee("U1")
        boss = self.employee("U2")
        self.pay(person, self.salary, "4400")
        leave = LeaveRequest.objects.create(
            employee=person, policy=unpaid, leave_type=LeaveType.UNPAID,
            start_date=datetime.date(2026, 6, 8), end_date=datetime.date(2026, 6, 12),
        )
        leave.approve(by=boss)
        run = self.pay_run()
        run.calculate()
        # 22 working days less 5 unpaid, of 22.
        self.assertEqual(run.payslips.get().gross(), Decimal("3400.00"))

    def test_paid_leave_does_not_come_off(self):
        holiday = LeavePolicy.objects.create(
            code="HOL", name="Holiday", leave_type="vacation",
            annual_days=Decimal("25"),
        )
        person = self.employee("U3")
        boss = self.employee("U4")
        self.pay(person, self.salary, "4400")
        leave = LeaveRequest.objects.create(
            employee=person, policy=holiday, leave_type=LeaveType.VACATION,
            start_date=datetime.date(2026, 6, 8), end_date=datetime.date(2026, 6, 12),
        )
        leave.approve(by=boss)
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("4400.00"))

    def test_a_pending_unpaid_request_does_not_reduce_pay(self):
        # Nobody's wages are cut for leave a manager has not agreed to.
        unpaid = LeavePolicy.objects.create(
            code="UNP", name="Unpaid", leave_type="unpaid",
            annual_days=Decimal("0"), allows_negative=True, is_paid=False,
        )
        person = self.employee("U5")
        self.pay(person, self.salary, "4400")
        LeaveRequest.objects.create(
            employee=person, policy=unpaid, leave_type=LeaveType.UNPAID,
            start_date=datetime.date(2026, 6, 8), end_date=datetime.date(2026, 6, 12),
        )
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("4400.00"))

    def test_a_public_holiday_is_not_an_unpaid_day(self):
        PublicHoliday.objects.create(name="Summer", date=datetime.date(2026, 6, 8))
        person = self.employee("U6")
        self.pay(person, self.salary, "4400")
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("4400.00"))

    def test_an_allowance_that_is_paid_regardless_is_not_prorated(self):
        unpaid = LeavePolicy.objects.create(
            code="UNP", name="Unpaid", leave_type="unpaid",
            annual_days=Decimal("0"), allows_negative=True, is_paid=False,
        )
        person = self.employee("U7")
        boss = self.employee("U8")
        self.pay(person, self.salary, "4400")
        self.pay(person, self.bonus, "500")
        leave = LeaveRequest.objects.create(
            employee=person, policy=unpaid, leave_type=LeaveType.UNPAID,
            start_date=datetime.date(2026, 6, 8), end_date=datetime.date(2026, 6, 12),
        )
        leave.approve(by=boss)
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("3900.00"))


class PostingTests(PayrollTestCase):
    def payroll(self, gross="5000", tax="20", pension="5", employer="3"):
        person = self.employee("P1")
        self.pay(person, self.salary, gross)
        self.pay(person, self.tax, tax)
        self.pay(person, self.pension, pension)
        self.pay(person, self.employer_pension, employer)
        run = self.pay_run()
        run.calculate()
        return person, run

    def test_the_entry_balances(self):
        _person, run = self.payroll()
        entry = run.post()
        self.assertTrue(entry.is_balanced())

    def test_wages_are_charged_gross(self):
        _person, run = self.payroll()
        run.post()
        self.assertEqual(self.balance(self.wages), Decimal("5000.00"))

    def test_deductions_are_owed_not_paid(self):
        _person, run = self.payroll()
        run.post()
        self.assertEqual(self.balance(self.tax_payable), Decimal("-1000.00"))
        # Employee 250 plus employer 150.
        self.assertEqual(self.balance(self.pension_payable), Decimal("-400.00"))

    def test_net_pay_payable_holds_what_reaches_people(self):
        _person, run = self.payroll()
        run.post()
        self.assertEqual(self.balance(self.net_pay), Decimal("-3750.00"))
        self.assertEqual(run.payslips.get().net(), Decimal("3750.00"))

    def test_the_employer_contribution_is_a_cost_not_a_deduction(self):
        _person, run = self.payroll()
        run.post()
        self.assertEqual(self.balance(self.employer_tax), Decimal("150.00"))
        self.assertEqual(run.payslips.get().net(), Decimal("3750.00"))

    def test_the_company_spends_more_than_anybody_receives(self):
        _person, run = self.payroll()
        run.post()
        self.assertEqual(run.gross(), Decimal("5000.00"))
        self.assertEqual(run.net(), Decimal("3750.00"))
        self.assertEqual(run.total_cost(), Decimal("5150.00"))

    def test_a_department_cost_centre_beats_the_component_account(self):
        engineering = Department.objects.create(
            code="ENG", name="Engineering", cost_centre=self.engineering
        )
        person = self.employee("D1", department=engineering)
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        run.post()
        self.assertEqual(self.balance(self.engineering), Decimal("5000.00"))
        self.assertEqual(self.balance(self.wages), Decimal("0.00"))

    def test_where_a_line_landed_is_frozen(self):
        # Moving a department to another cost centre must not restate the
        # entries it has already produced.
        engineering = Department.objects.create(
            code="ENG", name="Engineering", cost_centre=self.engineering
        )
        person = self.employee("D2", department=engineering)
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        run.post()
        engineering.cost_centre = self.wages
        engineering.save()
        line = run.payslips.get().lines.get()
        self.assertEqual(line.resolve_account(), self.engineering)

    def test_a_run_with_no_payslips_posts_nothing(self):
        run = self.pay_run()
        with self.assertRaises(ValidationError) as caught:
            run.post()
        self.assertIn("no payslips", str(caught.exception))

    def test_it_cannot_post_twice(self):
        _person, run = self.payroll()
        run.post()
        with self.assertRaises(ValidationError):
            run.post()

    def test_a_posted_run_cannot_be_recalculated(self):
        _person, run = self.payroll()
        run.post()
        with self.assertRaises(ValidationError) as caught:
            run.calculate()
        self.assertIn("Void it first", str(caught.exception))

    def test_a_posted_run_cannot_be_edited(self):
        _person, run = self.payroll()
        run.post()
        run.name = "changed"
        with self.assertRaises(ValidationError):
            run.save()

    def test_it_takes_a_number_when_it_posts(self):
        _person, run = self.payroll()
        run.post()
        self.assertTrue(run.number.startswith("PAY-"))

    def test_with_no_net_pay_account_there_is_nowhere_to_owe(self):
        company = Company.get()
        company.net_pay_account = None
        company.save()
        _person, run = self.payroll()
        with self.assertRaises(ValidationError) as caught:
            run.post()
        self.assertIn("net pay payable account", str(caught.exception))


class NobodyIsPaidTwiceTests(PayrollTestCase):
    def test_an_overlapping_run_for_the_same_person_is_refused(self):
        person = self.employee("T1")
        self.pay(person, self.salary, "5000")
        first = self.pay_run()
        first.calculate()
        first.post()
        second = PayRun.objects.create(
            period_start=datetime.date(2026, 6, 15), period_end=datetime.date(2026, 7, 14),
            pay_date=datetime.date(2026, 7, 14),
        )
        second.calculate()
        with self.assertRaises(ValidationError) as caught:
            second.post()
        self.assertIn("overlaps this period", str(caught.exception))

    def test_the_next_month_is_fine(self):
        person = self.employee("T2")
        self.pay(person, self.salary, "5000")
        first = self.pay_run()
        first.calculate()
        first.post()
        second = PayRun.objects.create(
            period_start=datetime.date(2026, 7, 1), period_end=datetime.date(2026, 7, 31),
            pay_date=datetime.date(2026, 7, 31),
        )
        second.calculate()
        second.post()
        self.assertEqual(self.balance(self.wages), Decimal("10000.00"))

    def test_a_voided_run_no_longer_blocks_the_period(self):
        person = self.employee("T3")
        self.pay(person, self.salary, "5000")
        first = self.pay_run()
        first.calculate()
        first.post()
        first.void()
        second = self.pay_run()
        second.calculate()
        second.post()
        self.assertEqual(self.balance(self.wages), Decimal("5000.00"))

    def test_one_payslip_per_person_per_run(self):
        from django.db.utils import IntegrityError

        person = self.employee("T4")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        with self.assertRaises(IntegrityError):
            Payslip.objects.create(run=run, employee=person)

    def test_two_rates_cannot_be_in_force_at_once(self):
        person = self.employee("T5")
        self.pay(person, self.salary, "5000")
        with self.assertRaises(ValidationError) as caught:
            self.pay(person, self.salary, "6000", since=datetime.date(2026, 1, 1))
        self.assertIn("close that off", str(caught.exception))

    def test_a_rise_recorded_properly_takes_effect(self):
        person = self.employee("T6")
        old = self.pay(person, self.salary, "5000")
        old.effective_to = datetime.date(2026, 5, 31)
        old.save()
        self.pay(person, self.salary, "6000", since=datetime.date(2026, 6, 1))
        run = self.pay_run()
        run.calculate()
        self.assertEqual(run.payslips.get().gross(), Decimal("6000.00"))


class VoidingTests(PayrollTestCase):
    def payroll(self):
        person = self.employee("V1")
        self.pay(person, self.salary, "5000")
        self.pay(person, self.tax, "20")
        run = self.pay_run()
        run.calculate()
        run.post()
        return person, run

    def test_voiding_reverses_every_account(self):
        _person, run = self.payroll()
        run.void()
        self.assertEqual(self.balance(self.wages), Decimal("0.00"))
        self.assertEqual(self.balance(self.tax_payable), Decimal("0.00"))
        self.assertEqual(self.balance(self.net_pay), Decimal("0.00"))

    def test_it_records_what_undid_it(self):
        _person, run = self.payroll()
        reversal = run.void()
        self.assertEqual(run.voided_entry, reversal)
        self.assertEqual(reversal.reverses, run.journal_entry)
        self.assertEqual(run.status, PayRunStatus.VOIDED)

    def test_it_cannot_be_voided_twice(self):
        _person, run = self.payroll()
        run.void()
        with self.assertRaises(ValidationError):
            run.void()

    def test_a_draft_cannot_be_voided(self):
        person = self.employee("V2")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        with self.assertRaises(ValidationError) as caught:
            run.void()
        self.assertIn("Only a posted pay run", str(caught.exception))

    def test_a_run_somebody_has_been_paid_from_cannot_be_voided(self):
        person, run = self.payroll()
        slip = run.payslips.get()
        payment = Payment.objects.create(
            party=person.party, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 6, 30), amount=slip.net(),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.net_pay,
        )
        payment.post()
        slip.pay(payment)
        with self.assertRaises(ValidationError) as caught:
            run.void()
        self.assertIn("already been paid", str(caught.exception))


class NetPayClearsTests(PayrollTestCase):
    """
    Net pay payable exists to be emptied. A payroll that posts the
    liability and never clears it leaves a balance growing by a month's
    wages every month that nobody can explain.
    """

    def payroll(self, code="N1"):
        person = self.employee(code)
        self.pay(person, self.salary, "5000")
        self.pay(person, self.tax, "20")
        run = self.pay_run()
        run.calculate()
        run.post()
        return person, run

    def settle(self, person, slip, amount=None):
        payment = Payment.objects.create(
            party=person.party, direction=PaymentDirection.DISBURSEMENT,
            payment_date=datetime.date(2026, 6, 30),
            amount=amount if amount is not None else slip.net(),
            currency=self.usd, bank_account=self.bank, counterpart_account=self.net_pay,
        )
        payment.post()
        return payment

    def test_paying_people_clears_the_control_account(self):
        person, run = self.payroll()
        slip = run.payslips.get()
        self.assertEqual(self.balance(self.net_pay), Decimal("-4000.00"))
        slip.pay(self.settle(person, slip))
        self.assertEqual(self.balance(self.net_pay), Decimal("0.00"))

    def test_the_run_knows_what_it_still_owes(self):
        person, run = self.payroll()
        slip = run.payslips.get()
        self.assertEqual(run.unpaid_net(), Decimal("4000.00"))
        slip.pay(self.settle(person, slip))
        self.assertEqual(run.unpaid_net(), Decimal("0"))

    def test_nobody_is_paid_twice_for_one_slip(self):
        person, run = self.payroll()
        slip = run.payslips.get()
        slip.pay(self.settle(person, slip))
        with self.assertRaises(ValidationError) as caught:
            slip.pay(self.settle(person, slip))
        self.assertIn("already been paid", str(caught.exception))

    def test_the_payment_has_to_be_the_net(self):
        person, run = self.payroll()
        slip = run.payslips.get()
        with self.assertRaises(ValidationError) as caught:
            slip.pay(self.settle(person, slip, amount=Decimal("3000")))
        self.assertIn("net pay is", str(caught.exception))

    def test_an_unposted_run_owes_nobody_anything_yet(self):
        person = self.employee("N2")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        slip = run.payslips.get()
        with self.assertRaises(ValidationError) as caught:
            slip.pay(self.settle(person, slip))
        self.assertIn("must be posted", str(caught.exception))


class ComponentConfigurationTests(PayrollTestCase):
    def test_an_earning_needs_somewhere_to_be_charged(self):
        with self.assertRaises(ValidationError) as caught:
            PayComponent.objects.create(
                code="X1", name="X", kind=ComponentKind.EARNING
            )
        self.assertIn("expense account", str(caught.exception))

    def test_a_deduction_needs_somewhere_to_be_owed(self):
        with self.assertRaises(ValidationError) as caught:
            PayComponent.objects.create(
                code="X2", name="X", kind=ComponentKind.DEDUCTION
            )
        self.assertIn("owed into", str(caught.exception))

    def test_an_employer_cost_needs_both(self):
        with self.assertRaises(ValidationError) as caught:
            PayComponent.objects.create(
                code="X3", name="X", kind=ComponentKind.EMPLOYER_COST,
                expense_account=self.employer_tax,
            )
        self.assertIn("both accounts", str(caught.exception))


class SignConstraintTests(PayrollTestCase):
    """
    Which way a payroll line faces is its kind, not its sign.

    Both of these came from `manage.py audit_invariants` rather than from
    reading the code: a negative earning would be a deduction charged to
    a wages account, and it would post backwards.
    """

    def test_a_rate_cannot_be_negative(self):
        from django.db.utils import IntegrityError

        person = self.employee("S1")
        with self.assertRaises(IntegrityError):
            EmployeeCompensation.objects.create(
                employee=person, component=self.salary, amount=Decimal("-100"),
                effective_from=datetime.date(2026, 1, 1),
            )

    def test_a_payslip_line_cannot_be_negative(self):
        from django.db.utils import IntegrityError

        from .models import PayslipLine

        person = self.employee("S2")
        self.pay(person, self.salary, "5000")
        run = self.pay_run()
        run.calculate()
        slip = run.payslips.get()
        with self.assertRaises(IntegrityError):
            PayslipLine.objects.create(
                payslip=slip, component=self.salary, kind=ComponentKind.EARNING,
                basis=ComponentBasis.FIXED, description="Negative",
                rate=Decimal("1"), amount=Decimal("-50"),
            )
