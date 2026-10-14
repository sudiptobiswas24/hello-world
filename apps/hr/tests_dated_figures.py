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
    register_piece_measure,
)
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

    def worker(self, code, **fields):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        from .models import Employee

        party = Party.objects.create(code=f"P-{code}", name=code)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        return Employee.objects.create(party=party, employee_number=code, hire_date=D(2020, 1, 1), **fields)

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
        self.rise = StatutoryFigure.objects.create(kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 9, 17),
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
        StatutoryFigure.objects.create(kind=FigureKind.EPS_WAGE_CEILING, effective_from=D(2026, 10, 1),
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
        StatutoryFigure.objects.create(kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 10, 1),
                                       value=Decimal("21000"))
        with self.assertRaisesMessage(ValidationError, "pay has changed since this run was calculated"):
            october.post()

    def test_one_ceiling_or_the_other(self):
        self.pf.base_ceiling = Decimal("15000")
        with self.assertRaisesMessage(ValidationError, "not both"):
            self.pf.save()


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
            PayComponentSlab.objects.create(component=self.wb, state="WB", above=Decimal(above),
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
        PayComponentSlab.objects.create(component=self.wb, state="MH", above=Decimal("0"), amount=Decimal("175"))
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
                PayComponentSlab.objects.create(component=component, state="WB", month=when, amount=Decimal(amount))
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
        self.basic.is_ordinary_wages = True
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
        StatutoryFigure.objects.create(kind=FigureKind.OVERTIME_MULTIPLIER, effective_from=D(2026, 11, 1),
                                       value=Decimal("2.5"))
        self.mark(staff, D(2026, 10, 8), "6")
        self.mark(staff, D(2026, 11, 9), "6")
        october = self.month_run(2026, 10, post=False, employees=[staff])
        november = self.month_run(2026, 11, post=False, employees=[staff])
        self.assertEqual(self.overtime_on(october, staff).amount, Decimal("750.00"))
        self.assertEqual(self.overtime_on(november, staff).amount, Decimal("1012.50"))

    def test_the_rate_is_not_typed_per_person(self):
        staff = self.worker("S4")
        with self.assertRaisesMessage(ValidationError, "not a rate typed for them"):
            EmployeeCompensation.objects.create(employee=staff, component=self.overtime, amount=Decimal("125"),
                                                effective_from=D(2026, 1, 1))

    def test_only_fixed_hourly_and_piece_earnings_are_ordinary_wages(self):
        self.pf.is_ordinary_wages = True
        with self.assertRaisesMessage(ValidationError, "ordinary rate of wages is made of"):
            self.pf.save()
