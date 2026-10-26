"""
The figures the law sets, as dated data kept on the run (g21 items 5 to 8,
O222). Every figure here is illustrative test data, not the gazette's.

O222, basic 20,000, the EPF ceiling 15,000 until a row of 25,000 from
17 September 2026: August's PF is 12% of 15,000 = 1,800.00, October's 12%
of 20,000 = 2,400.00. The EPS ceiling stays at its default 15,000: EPS
8.33% of 15,000 = 1,249.50, 1,250 to the rupee, and the EPF difference
2,400 - 1,250 = 1,150. With a pension ceiling of 25,000 from November,
November's EPS is 8.33% of 20,000 = 1,666 and the difference 734;
October's file, kept, stays at 1,250.

Professional tax: an undated West Bengal schedule (12,000 pays 110) and
one from 1 October 2026 (12,000 pays nothing; 20,000 pays 130 in both).

Labour welfare fund: 3 from the employee and 15 from the employer in June
and December, nothing in July.

Overtime at twice the ordinary rate (October 2026, Monday to Saturday, 27
working days, 8 hours a day):
  staff,  basic 13,500 ordinary, HRA 2,000 not:  6 h x 13,500/27/8 x 2 = 750.00
  shift,  wage 10,800 + DA 1,350, by the register: 4 h x 12,150/27/8 x 2 = 450.00
  weaver, 1.25 a metre, September 380+420+400+400 m on 4 days = 500.00 a day:
          3 h x 500/8 x 2 = 375.00
  total first: 13,333 over June's 26 days, 7 h: 897.41 (897.40 rounding the hour first)
  2.5 from November: 6 h x 13,500/25/8 x 2.5 = 1,012.50, October still 750.00
(worked in scratchpad/calc_fix_people/scenarios.py).
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .attendance import AttendanceDay, AttendanceStatus
from .payroll import (
    PIECE_MEASURES,
    ComponentBasis,
    ComponentKind,
    EmployeeCompensation,
    PayComponent,
    PayComponentSlab,
    PayRun,
    Statutory,
    Rounding,
    register_piece_measure,
)
from .pay_details import EmployeePayDetails
from .statutory import FigureKind, StatutoryFigure
from .statutory_files import ecr_rows, lwf_register, pt_register
from .tests_statutory import StatutoryTestCase

D = datetime.date


def month(year, number):
    first = D(year, number, 1)
    return first, (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)


class DatedFiguresTestCase(StatutoryTestCase):
    def setUp(self):
        super().setUp()
        self.output = []
        register_piece_measure("test_metres", "metres", lambda employee, up_to: [
            (day, quantity) for who, day, quantity in self.output if who == employee.pk and day <= up_to])
        self.addCleanup(PIECE_MEASURES.pop, "test_metres", None)

    def worker(self, code, hire=D(2020, 1, 1), **fields):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        from .models import Employee

        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(party=party, employee_number=code, hire_date=hire, **fields)

    def month_run(self, year, number, post=True, employees=None):
        start, end = month(year, number)
        run = PayRun.objects.create(period_start=start, period_end=end, pay_date=end)
        run.calculate(employees=employees)
        if post:
            run.post()
        return run

    def line(self, run, employee, component):
        return run.payslips.get(employee=employee).lines.get(component=component)


class TheWageCeilingIsKeptForTheMonthTests(DatedFiguresTestCase):
    """O222: EPS_CEILING was a constant and the PF component's 15,000 a setting read live."""

    def setUp(self):
        super().setUp()
        for component, marked in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER)):
            component.base_ceiling, component.ceiling_figure = None, FigureKind.EPF_WAGE_CEILING
            component.statutory = marked
            component.save()
        self.c = self.person("C", basic="20000")
        for number, person in enumerate((self.a, self.b, self.c)):
            person.uan = f"10000000000{number}"
            person.save()
            EmployeePayDetails.objects.create(employee=person, date_of_birth=D(1990, 1, 1))
        self.rise = StatutoryFigure.objects.create(confirmed_on=datetime.date(2000, 1, 1), kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 9, 17),
                                                   value=Decimal("25000"), source="S.O. 5109(E), to confirm")

    def test_each_month_is_paid_at_the_ceiling_that_stood_on_its_last_day(self):
        august, october = self.month_run(2026, 8), self.month_run(2026, 10)
        before, after = self.line(august, self.c, self.pf), self.line(october, self.c, self.pf)
        self.assertEqual((before.amount, before.base_ceiling), (Decimal("1800.00"), Decimal("15000.00")))
        self.assertEqual((after.amount, after.base_ceiling), (Decimal("2400.00"), Decimal("25000.00")))
        self.assertEqual(before.kept_terms["epf_ceiling"], [None, None, "15000"])
        self.assertEqual(after.kept_terms["epf_ceiling"], [self.rise.pk, "2026-09-17", "25000"])

    def test_the_ecr_files_pension_wages_at_the_ceiling_the_run_kept(self):
        october = self.month_run(2026, 10)
        StatutoryFigure.objects.create(confirmed_on=datetime.date(2000, 1, 1), kind=FigureKind.EPS_WAGE_CEILING, effective_from=D(2026, 10, 1),
                                       value=Decimal("25000"))
        row = next(row for row in ecr_rows(october) if row["name"] == "C")
        self.assertEqual((row["epf_wages"], row["eps_wages"], row["eps"], row["epf_diff"]), (20000, 15000, 1250, 1150))
        november = self.month_run(2026, 11)
        row = next(row for row in ecr_rows(november) if row["name"] == "C")
        self.assertEqual((row["eps_wages"], row["eps"], row["epf_diff"]), (20000, 1666, 734))

    def test_a_figure_a_posted_run_was_paid_at_is_not_changed_until_the_run_is_voided(self):
        september = self.month_run(2026, 9)
        self.rise.value = Decimal("21000")
        with self.assertRaisesMessage(ValidationError, "is what"):
            self.rise.save()
        with self.assertRaisesMessage(ValidationError, "is what"):
            StatutoryFigure.objects.get(pk=self.rise.pk).delete()
        september.void(on_date=D(2026, 10, 1))
        self.rise.save()
        self.assertEqual(StatutoryFigure.objects.get(pk=self.rise.pk).value, Decimal("21000"))

    def test_a_figure_entered_after_the_run_was_calculated_stops_its_post(self):
        october = self.month_run(2026, 10, post=False)
        StatutoryFigure.objects.create(confirmed_on=datetime.date(2000, 1, 1), kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 10, 1),
                                       value=Decimal("21000"))
        with self.assertRaisesMessage(ValidationError, "pay has changed since this run was calculated"):
            october.post()

    def test_one_ceiling_or_the_other(self):
        self.pf.base_ceiling = Decimal("15000")
        with self.assertRaisesMessage(ValidationError, "not both"):
            self.pf.save()


class ThePensionShareStopsAfter58Tests(DatedFiguresTestCase):
    """
    Basic 20,000, PF wages capped at 15,000: in the month a member turns 58, EPS 1,250 and the EPF
    difference 550; from the next month EPS wages and EPS nothing, the whole 1,800 EPF's. EDLI wages
    stay 15,000. Turning 58 on the 1st, the 15th or the 31st of October 2026 alike.
    """

    def setUp(self):
        super().setUp()
        for component, marked in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER)):
            component.statutory = marked
            component.save()
        self.c = self.person("C", basic="20000")
        self.c.uan = "100000000009"
        self.c.save()

    def row(self, number, person=None):
        run = self.month_run(2026, number, employees=[person or self.c])
        found = ecr_rows(run)[0]
        return found["eps_wages"], found["eps"], found["epf_diff"], found["edli_wages"]

    def test_from_the_month_after_the_58th_birthday(self):
        for number, born in enumerate((D(1968, 10, 1), D(1968, 10, 15), D(1968, 10, 31))):
            with self.subTest(born=born):
                person = self.person(f"P{number}", basic="20000")
                person.uan = f"10000000002{number}"
                person.save()
                EmployeePayDetails.objects.create(employee=person, date_of_birth=born)
                self.assertEqual(self.row(10, person), (15000, 1250, 550, 15000))
                self.assertEqual(self.row(11, person), (0, 0, 1800, 15000))

    def test_the_date_read_is_kept_and_a_member_with_none_is_named(self):
        october = self.month_run(2026, 10, employees=[self.c])
        with self.assertRaisesMessage(ValidationError, "No date of birth on C"):
            ecr_rows(october)
        details = EmployeePayDetails.objects.create(employee=self.c, date_of_birth=D(1968, 9, 20))
        # October's run read none: entered since, it is read now. 58 in September: no pension in October.
        self.assertEqual(ecr_rows(october)[0]["eps"], 0)
        self.assertEqual(self.row(11)[:2], (0, 0))
        # November's run read 20 September 1968, and keeps it when the date is corrected.
        details.date_of_birth = D(1990, 1, 1)
        details.save()
        self.assertEqual(ecr_rows(PayRun.objects.get(period_end=D(2026, 11, 30)))[0]["eps"], 0)
        self.assertEqual(self.line(PayRun.objects.get(period_end=D(2026, 11, 30)), self.c, self.pf).kept_terms["born"],
                         "1968-09-20")

    def test_a_pension_share_of_its_own_goes_to_the_provident_fund_past_58(self):
        eps = self.percent("EPS", 65, ComponentKind.EMPLOYER_COST, self.pf_payable, base=[self.basic, self.special],
                           ceiling="15000", rounding=Rounding.RUPEE)
        eps.statutory = Statutory.EPS
        eps.save()
        # Employer EPF 3.67% and pension 8.33% of 15,000: the 12% is 1,800, rounded once; pension 1,250 to the
        # rupee and the EPF share the 550 left (O237; rounded apart they were 551 + 1,250).
        member = self.worker("D", uan="100000000030")
        for component, rate in ((self.basic, "20000"), (self.pf, "12"), (self.pf_er, "3.67"), (eps, "8.33")):
            self.paid(member, component, rate)
        with self.assertRaisesMessage(ValidationError, "No date of birth on D"):
            self.month_run(2026, 11, post=False, employees=[member])
        EmployeePayDetails.objects.create(employee=member, date_of_birth=D(1968, 10, 15))
        found = ecr_rows(self.month_run(2026, 10, employees=[member]))[0]
        self.assertEqual((found["eps"], found["epf_diff"]), (1250, 550))
        november = self.month_run(2026, 11, employees=[member])
        line = self.line(november, member, eps)
        self.assertEqual((line.kept_statutory, line.description), (Statutory.PF_EMPLOYER, "EPS, to provident fund past 58"))
        found = ecr_rows(november)[0]
        self.assertEqual((found["eps_wages"], found["eps"], found["epf_diff"]), (0, 0, 1800))

    def test_past_58_the_pension_line_is_owed_where_the_employers_provident_fund_share_is(self):
        """O236: November's 1,250 is credited to PF payable with the EPF share, not to EPS payable."""
        from apps.accounting.models import Account, AccountType, JournalLine

        eps_payable = Account.objects.create(code="2340", name="EPS payable", account_type=AccountType.LIABILITY)
        eps = self.percent("EPS", 65, ComponentKind.EMPLOYER_COST, eps_payable, base=[self.basic, self.special],
                           ceiling="15000", rounding=Rounding.RUPEE)
        eps.statutory = Statutory.EPS
        eps.save()
        member = self.worker("D", uan="100000000030")
        for component, rate in ((self.basic, "20000"), (self.pf, "12"), (self.pf_er, "3.67"), (eps, "8.33")):
            self.paid(member, component, rate)
        EmployeePayDetails.objects.create(employee=member, date_of_birth=D(1968, 10, 15))
        credited = {}
        for number in (10, 11):
            run = self.month_run(2026, number, employees=[member])
            credited[number] = [sum((l.credit for l in JournalLine.objects.filter(entry=run.journal_entry, account=a)),
                                    Decimal("0")) for a in (self.pf_payable, eps_payable)]
        self.assertEqual(credited, {10: [Decimal("2350.00"), Decimal("1250.00")], 11: [Decimal("3600.00"), Decimal("0")]})


class ProfessionalTaxSchedulesTests(DatedFiguresTestCase):
    def setUp(self):
        super().setUp()
        self.wb = PayComponent.objects.create(code="PTWB", name="Professional tax", kind=ComponentKind.DEDUCTION,
                                              basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                                              sequence=95, statutory=Statutory.PT)
        self.wb.base_components.set([self.basic])
        for above, up_to, amount, since in [
                ("0", "10000", "0", None), ("10000", "15000", "110", None), ("15000", "25000", "130", None),
                ("25000", None, "200", None),
                ("0", "15000", "0", D(2026, 10, 1)), ("15000", "25000", "130", D(2026, 10, 1)),
                ("25000", None, "200", D(2026, 10, 1))]:
            PayComponentSlab.objects.create(confirmed_on=datetime.date(2000, 1, 1), component=self.wb, state="WB", above=Decimal(above),
                                            up_to=Decimal(up_to) if up_to else None, amount=Decimal(amount),
                                            effective_from=since)
        self.low, self.mid = self.worker("L"), self.worker("M")
        for person, basic in ((self.low, "12000"), (self.mid, "20000")):
            self.paid(person, self.basic, basic)
            self.paid(person, self.wb, "1")

    def test_a_run_takes_the_schedule_that_stood_for_its_month(self):
        people = [self.low, self.mid]
        september, october = self.month_run(2026, 9, employees=people), self.month_run(2026, 10, employees=people)
        self.assertEqual(self.line(september, self.low, self.wb).amount, Decimal("110.00"))
        self.assertFalse(october.payslips.get(employee=self.low).lines.filter(component=self.wb).exists())
        self.assertEqual(self.line(october, self.mid, self.wb).amount, Decimal("130.00"))
        self.assertEqual([(row["employee"], row["wages"], row["tax"], row["state"], row["from"])
                          for row in pt_register(D(2026, 9, 1)) if row["employee"] in ("L", "M")],
                         [("L", Decimal("12000.00"), Decimal("110.00"), "WB", ""),
                          ("M", Decimal("20000.00"), Decimal("130.00"), "WB", "")])
        self.assertEqual([(row["employee"], row["from"]) for row in pt_register(D(2026, 10, 1))
                          if row["employee"] in ("L", "M")], [("M", "2026-10-01")])

    def test_a_band_a_posted_run_kept_is_not_edited(self):
        self.month_run(2026, 10, employees=[self.mid])
        band = PayComponentSlab.objects.get(component=self.wb, effective_from=D(2026, 10, 1), amount=Decimal("130"))
        band.amount = Decimal("150")
        with self.assertRaisesMessage(ValidationError, "is what"):
            band.save()

    def test_slabs_of_two_states_ask_which_state_the_person_works_in(self):
        PayComponentSlab.objects.create(confirmed_on=datetime.date(2000, 1, 1), component=self.wb, state="MH", above=Decimal("0"), amount=Decimal("175"))
        with self.assertRaisesMessage(ValidationError, "Set the state they work in"):
            self.month_run(2026, 10, post=False, employees=[self.mid])
        self.mid.work_state = "WB"
        self.mid.save()
        october = self.month_run(2026, 10, post=False, employees=[self.mid])
        self.assertEqual(self.line(october, self.mid, self.wb).amount, Decimal("130.00"))


class LabourWelfareFundTests(DatedFiguresTestCase):
    def test_taken_in_june_and_december_only_with_the_employers_share(self):
        shares = []
        for code, kind, statutory, amount in (("LWF", ComponentKind.DEDUCTION, Statutory.LWF, "3"),
                                              ("LWF-ER", ComponentKind.EMPLOYER_COST, Statutory.LWF_EMPLOYER, "15")):
            component = PayComponent.objects.create(
                code=code, name=code, kind=kind, basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                expense_account=self.employer if kind == ComponentKind.EMPLOYER_COST else None,
                sequence=97, statutory=statutory)
            for when in (6, 12):
                PayComponentSlab.objects.create(confirmed_on=datetime.date(2000, 1, 1), component=component, state="WB", month=when, amount=Decimal(amount))
            shares.append(component)
        weaver = self.worker("W")
        self.paid(weaver, self.basic, "9000")
        for component in shares:
            self.paid(weaver, component, "1")
        june = self.month_run(2026, 6, employees=[weaver])
        july, december = (self.month_run(2026, n, post=False, employees=[weaver]) for n in (7, 12))
        self.assertEqual([self.line(june, weaver, c).amount for c in shares], [Decimal("3.00"), Decimal("15.00")])
        self.assertFalse(july.payslips.get(employee=weaver).lines.filter(component__in=shares).exists())
        self.assertEqual([self.line(december, weaver, c).amount for c in shares], [Decimal("3.00"), Decimal("15.00")])
        self.assertEqual([(row["employee"], row["employee_share"], row["employer_share"], row["state"])
                          for row in lwf_register(D(2026, 6, 1))], [("W", Decimal("3.00"), Decimal("15.00"), "WB")])
        self.assertEqual(lwf_register(D(2026, 7, 1)), [])


class OvertimeAtTheOrdinaryRateTests(DatedFiguresTestCase):
    """Loom weavers on piece rates, shift workers by the register and staff on a salary, each at twice the ordinary rate."""

    def setUp(self):
        super().setUp()
        # A salary, paid for the days paid: a joiner's and an absentee's come down, their hour does not.
        self.basic.is_ordinary_wages = self.basic.reduces_for_unpaid_leave = True
        self.basic.save()
        self.da = PayComponent.objects.create(code="DA", name="Dearness allowance", kind=ComponentKind.EARNING,
                                              expense_account=self.wages, sequence=15, is_ordinary_wages=True,
                                              reduces_for_unpaid_leave=True)
        self.overtime = PayComponent.objects.create(
            code="OTO", name="Overtime", kind=ComponentKind.EARNING, basis=ComponentBasis.OVERTIME_ORDINARY,
            expense_account=self.wages, sequence=45)
        self.weaving = PayComponent.objects.create(
            code="WEAVE", name="Weaving", kind=ComponentKind.EARNING, basis=ComponentBasis.PER_UNIT,
            measure="test_metres", expense_account=self.wages, sequence=12, is_ordinary_wages=True)

    def mark(self, person, day, overtime="0", status=AttendanceStatus.PRESENT):
        AttendanceDay.objects.create(employee=person, on=day, status=status, overtime_hours=Decimal(overtime))

    def overtime_on(self, run, person):
        return self.line(run, person, self.overtime)

    def test_staff_on_a_salary(self):
        staff = self.worker("S", working_days="123456")
        self.paid(staff, self.basic, "13500")
        self.paid(staff, self.hra, "2000")
        self.paid(staff, self.overtime, "1")
        self.mark(staff, D(2026, 10, 8), "4")
        self.mark(staff, D(2026, 10, 9), "2")
        line = self.overtime_on(self.month_run(2026, 10, post=False, employees=[staff]), staff)
        self.assertEqual((line.amount, line.quantity, line.rate), (Decimal("750.00"), Decimal("6.00"), Decimal("125.0000")))
        self.assertEqual(line.kept_terms["overtime_multiplier"], [None, None, "2"])

    def test_shift_workers_paid_by_the_register(self):
        shift = self.worker("SH", working_days="123456", paid_by_attendance=True)
        self.paid(shift, self.basic, "10800")
        self.paid(shift, self.da, "1350")
        self.paid(shift, self.overtime, "1")
        day = D(2026, 10, 1)
        while day.month == 10:
            if day.isoweekday() != 7:
                self.mark(shift, day, "4" if day == D(2026, 10, 12) else "0")
            day += datetime.timedelta(days=1)
        slip = self.month_run(2026, 10, post=False, employees=[shift]).payslips.get()
        self.assertEqual(slip.lines.get(component=self.overtime).amount, Decimal("450.00"))
        self.assertEqual(slip.gross(), Decimal("12600.00"))

    def test_loom_weavers_on_piece_rates_at_last_months_daily_average(self):
        weaver = self.worker("WV", working_days="123456")
        self.paid(weaver, self.weaving, "1.25")
        self.paid(weaver, self.overtime, "1")
        for day, metres in ((2, 380), (3, 420), (4, 400), (5, 400)):
            self.output.append((weaver.pk, D(2026, 9, day), Decimal(metres)))
        self.output.append((weaver.pk, D(2026, 10, 6), Decimal("600")))
        self.mark(weaver, D(2026, 10, 6), "3")
        line = self.overtime_on(self.month_run(2026, 10, post=False, employees=[weaver]), weaver)
        self.assertEqual(line.amount, Decimal("375.00"))
        self.assertEqual(line.kept_terms["piece_days_of"], "2026-09")

    def test_the_rate_is_the_agreed_one_whenever_they_joined(self):
        """Joined on the 1st, the 16th or the 31st of October: 13,500 is 62.50 an hour, 2 h at twice it 250.00."""
        for joined, salary in ((1, "13500.00"), (16, "7000.00"), (31, "500.00")):
            with self.subTest(joined=joined):
                staff = self.worker(f"J{joined}", hire=D(2026, 10, joined), working_days="123456")
                self.paid(staff, self.basic, "13500", since=D(2026, 10, joined))
                self.paid(staff, self.overtime, "1", since=D(2026, 10, joined))
                self.mark(staff, D(2026, 10, 31), "2")
                slip = self.month_run(2026, 10, post=False, employees=[staff]).payslips.get()
                self.assertEqual((slip.lines.get(component=self.basic).amount,
                                  slip.lines.get(component=self.overtime).amount), (Decimal(salary), Decimal("250.00")))

    def test_the_rate_is_the_agreed_one_when_unpaid_days_cut_the_month(self):
        staff = self.worker("U1", working_days="123456")
        self.paid(staff, self.basic, "13500")
        self.paid(staff, self.overtime, "1")
        for day in (5, 6):
            self.mark(staff, D(2026, 10, day), status=AttendanceStatus.ABSENT)
        self.mark(staff, D(2026, 10, 31), "2")
        slip = self.month_run(2026, 10, post=False, employees=[staff]).payslips.get()
        self.assertEqual((slip.lines.get(component=self.basic).amount, slip.lines.get(component=self.overtime).amount),
                         (Decimal("12500.00"), Decimal("250.00")))

    def test_a_shift_worker_joining_mid_month_is_paid_overtime_at_the_days_agreed_wage(self):
        shift = self.worker("SJ", hire=D(2026, 10, 16), working_days="123456", paid_by_attendance=True)
        self.paid(shift, self.basic, "10800", since=D(2026, 10, 16))
        self.paid(shift, self.da, "1350", since=D(2026, 10, 16))
        self.paid(shift, self.overtime, "1", since=D(2026, 10, 16))
        day = D(2026, 10, 16)
        while day.month == 10:
            if day.isoweekday() != 7:
                self.mark(shift, day, "2" if day.day == 31 else "0")
            day += datetime.timedelta(days=1)
        slip = self.month_run(2026, 10, post=False, employees=[shift]).payslips.get()
        self.assertEqual((slip.lines.get(component=self.overtime).amount, slip.gross()),
                         (Decimal("225.00"), Decimal("6525.00")))

    def test_a_rate_changed_on_the_first_mid_month_or_the_last_day_is_read_on_the_day_worked(self):
        """2 h on the 10th and 2 h on the 31st, 13,500 rising to 16,200: from the 1st 600.00, else 550.00."""
        for since, owed in ((1, "600.00"), (16, "550.00"), (31, "550.00")):
            with self.subTest(since=since):
                staff = self.worker(f"R{since}", working_days="123456")
                EmployeeCompensation.objects.create(employee=staff, component=self.basic, amount=Decimal("13500"),
                                                    effective_from=D(2020, 1, 1),
                                                    effective_to=D(2026, 10, since) - datetime.timedelta(days=1))
                self.paid(staff, self.basic, "16200", since=D(2026, 10, since))
                self.paid(staff, self.overtime, "1")
                self.mark(staff, D(2026, 10, 10), "2")
                self.mark(staff, D(2026, 10, 31), "2")
                line = self.overtime_on(self.month_run(2026, 10, post=False, employees=[staff]), staff)
                self.assertEqual(line.amount, Decimal(owed))

    def test_the_total_is_worked_out_first_and_rounded_once(self):
        staff = self.worker("S2", working_days="123456")
        self.paid(staff, self.basic, "13333")
        self.paid(staff, self.overtime, "1")
        self.mark(staff, D(2026, 6, 10), "7")
        self.assertEqual(self.overtime_on(self.month_run(2026, 6, post=False, employees=[staff]), staff).amount,
                         Decimal("897.41"))

    def test_the_multiple_is_dated_data(self):
        staff = self.worker("S3", working_days="123456")
        self.paid(staff, self.basic, "13500")
        self.paid(staff, self.overtime, "1")
        StatutoryFigure.objects.create(confirmed_on=datetime.date(2000, 1, 1), kind=FigureKind.OVERTIME_MULTIPLIER, effective_from=D(2026, 11, 1),
                                       value=Decimal("2.5"))
        self.mark(staff, D(2026, 10, 8), "6")
        self.mark(staff, D(2026, 11, 9), "6")
        october = self.month_run(2026, 10, post=False, employees=[staff])
        november = self.month_run(2026, 11, post=False, employees=[staff])
        self.assertEqual(self.overtime_on(october, staff).amount, Decimal("750.00"))
        self.assertEqual(self.overtime_on(november, staff).amount, Decimal("1012.50"))

    def test_a_working_day_of_no_hours_is_refused_and_a_run_says_so(self):
        """Review RP7: 0 hours a day is refused at save; a row from before that is refused by the run in words."""
        with self.assertRaisesMessage(ValidationError, "more than none and at most 24"):
            self.worker("Z0", hours_a_day=Decimal("0"))
        staff = self.worker("Z1", working_days="123456")
        self.paid(staff, self.basic, "13500")
        self.paid(staff, self.overtime, "1")
        self.mark(staff, D(2026, 10, 8), "2")
        type(staff).objects.filter(pk=staff.pk).update(hours_a_day=Decimal("0"))
        staff.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "has a working day of 0.00 hours"):
            self.month_run(2026, 10, post=False, employees=[staff])

    def test_the_rate_is_not_typed_per_person(self):
        staff = self.worker("S4")
        with self.assertRaisesMessage(ValidationError, "not a rate typed for them"):
            EmployeeCompensation.objects.create(employee=staff, component=self.overtime, amount=Decimal("125"),
                                                effective_from=D(2026, 1, 1))

    def test_only_fixed_hourly_and_piece_earnings_are_ordinary_wages(self):
        self.pf.is_ordinary_wages = True
        with self.assertRaisesMessage(ValidationError, "ordinary rate of wages is made of"):
            self.pf.save()


class TheMonthsFiguresAreOwedOnceTests(DatedFiguresTestCase):
    """
    Review RP5, decided 15 October: professional tax per month and the welfare fund per its months are owed
    once, on the month's wages, however many runs pay the month. June paid in two fortnights of 6,000: the
    welfare fund 3 and 15 in the first, nothing in the second; PT nothing on 6,000, then 110 on the month's
    12,000 in the second (the undated West Bengal-shaped bands, illustrative).
    """

    def setUp(self):
        super().setUp()
        made = {}
        for code, kind, statutory, slabs in (
                ("LWF", ComponentKind.DEDUCTION, Statutory.LWF, [(None, None, "3", 6), (None, None, "3", 12)]),
                ("LWF-ER", ComponentKind.EMPLOYER_COST, Statutory.LWF_EMPLOYER,
                 [(None, None, "15", 6), (None, None, "15", 12)]),
                ("PTM", ComponentKind.DEDUCTION, Statutory.PT,
                 [("0", "10000", "0", None), ("10000", "15000", "110", None), ("15000", None, "130", None)])):
            component = PayComponent.objects.create(
                code=code, name=code, kind=kind, basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                expense_account=self.employer if kind == ComponentKind.EMPLOYER_COST else None,
                sequence=97, statutory=statutory)
            component.base_components.set([self.basic])
            for above, up_to, amount, when in slabs:
                PayComponentSlab.objects.create(confirmed_on=datetime.date(2000, 1, 1), component=component, state="WB", month=when, amount=Decimal(amount),
                                                above=Decimal(above or "0"), up_to=Decimal(up_to) if up_to else None)
            made[code] = component
        self.lwf, self.lwf_er, self.ptm = made["LWF"], made["LWF-ER"], made["PTM"]
        self.weaver = self.worker("W", work_state="WB")
        self.paid(self.weaver, self.basic, "6000")
        for component in made.values():
            self.paid(self.weaver, component, "1")

    def fortnight(self, first, post=True):
        last = D(2026, 6, 15) if first == 1 else D(2026, 6, 30)
        run = PayRun.objects.create(period_start=D(2026, 6, first), period_end=last, pay_date=last)
        run.calculate(employees=[self.weaver])
        if post:
            run.post()
        return run

    def taken(self, run):
        slip = run.payslips.get()
        return {line.component.code: line.amount for line in slip.lines.all() if line.kind != ComponentKind.EARNING}

    def june_register(self):
        rows = lwf_register(D(2026, 6, 1))
        return sum((r["employee_share"] for r in rows), Decimal("0")), sum((r["employer_share"] for r in rows), Decimal("0"))

    def test_two_runs_in_june_take_the_months_figures_once(self):
        first, second = self.fortnight(1), self.fortnight(16)
        self.assertEqual(self.taken(first), {"LWF": Decimal("3.00"), "LWF-ER": Decimal("15.00")})
        self.assertEqual(self.taken(second), {"PTM": Decimal("110.00")})
        pt = second.payslips.get().lines.get(component=self.ptm)
        self.assertEqual(pt.kept_terms["month_before"], ["6000.00", "0.00"])
        self.assertEqual(self.june_register(), (Decimal("3.00"), Decimal("15.00")))
        self.assertEqual([(r["wages"], r["tax"]) for r in pt_register(D(2026, 6, 1))], [(Decimal("12000.00"), Decimal("110.00"))])

    def test_a_run_voided_between_them_reopens_the_month(self):
        first = self.fortnight(1)
        first.void(on_date=D(2026, 7, 1))
        second = self.fortnight(16)
        self.assertEqual(self.taken(second), {"LWF": Decimal("3.00"), "LWF-ER": Decimal("15.00")})
        self.assertEqual(self.june_register(), (Decimal("3.00"), Decimal("15.00")))

    def test_the_earlier_run_voided_after_the_later_one_leaves_the_month_to_the_next_run_of_it(self):
        """The later run keeps what it took (nothing) and why; the month reads nothing until a run of June takes it."""
        first, second = self.fortnight(1), self.fortnight(16)
        first.void(on_date=D(2026, 7, 1))
        self.assertEqual(self.june_register(), (Decimal("0"), Decimal("0")))
        again = self.fortnight(1)
        # PT is not taken again: the second run took the month's 110 on 12,000 (its 6,000 and the first's),
        # and the first again reads the same month, 12,000, less the 110 taken. June's PT stays 110.
        self.assertEqual(self.taken(again), {"LWF": Decimal("3.00"), "LWF-ER": Decimal("15.00")})
        self.assertEqual(self.june_register(), (Decimal("3.00"), Decimal("15.00")))
        self.assertEqual(sum((r["tax"] for r in pt_register(D(2026, 6, 1))), Decimal("0")), Decimal("110.00"))

    def test_a_leavers_final_run_in_the_month_takes_only_what_is_left(self):
        self.fortnight(1)
        self.weaver.termination_date, self.weaver.employment_status = D(2026, 6, 20), "terminated"
        self.weaver.save()
        final = self.fortnight(16)
        self.assertNotIn("LWF", self.taken(final))
        self.assertEqual(self.june_register(), (Decimal("3.00"), Decimal("15.00")))
