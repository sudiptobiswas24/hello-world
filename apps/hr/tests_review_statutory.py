"""
Review probes for g24 (fix_statutory, d76ef6f..0cca92e): each test asserts what SHOULD be true.
Hand numbers are in /home/user/work/calc_review_statutory/calc.py.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .payroll import PayComponent, Statutory
from .pay_details import EmployeePayDetails
from .statutory import BONUS_CEILING, FigureKind, MinimumWage, Skill, StatutoryFigure
from .statutory_check import statutory_gaps
from .tests_statutory_seed import D, LoadedTestCase, PayComponentSlab
from .payroll import ComponentBasis, ComponentKind, Rounding


class ConfirmingProbes(LoadedTestCase):
    def test_p01_only_the_controller_confirms_each_kind_over_the_api_and_only_once(self):
        self.load()
        figure = StatutoryFigure.objects.filter(kind=FigureKind.EPF_WAGE_CEILING).first()
        wage = MinimumWage.objects.first()
        body = {"kind": figure.kind, "state": figure.state, "effective_from": str(figure.effective_from),
                "value": str(figure.value)}
        wbody = {"state": wage.state, "employment": wage.employment, "zone": wage.zone, "skill": wage.skill,
                 "effective_from": str(wage.effective_from), "basic": str(wage.basic), "vda": str(wage.vda)}
        for role in ("Payroll Officer", "HR Admin", "Bookkeeper", "AP Manager", "Line Manager",
                     "Employee Self Service", "Sales Rep"):
            client = self.in_role(role)
            self.assertEqual(client.post(f"/api/hr/statutory-figures/{figure.pk}/confirm/", body,
                                         format="json").status_code, 403, role)
            self.assertEqual(client.post(f"/api/hr/minimum-wages/{wage.pk}/confirm/", wbody,
                                         format="json").status_code, 403, role)
        controller = self.in_role("Controller")
        self.assertEqual(controller.post(f"/api/hr/statutory-figures/{figure.pk}/confirm/", {}, format="json"
                                         ).status_code, 400, "a confirm that says nothing it read")
        ok = controller.post(f"/api/hr/statutory-figures/{figure.pk}/confirm/", body, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        again = controller.post(f"/api/hr/statutory-figures/{figure.pk}/confirm/", body, format="json")
        self.assertEqual(again.status_code, 400)
        figure.refresh_from_db()
        self.assertIsNotNone(figure.confirmed_by_id)

    def test_p02_a_schedule_is_confirmed_as_it_was_read_not_as_it_is(self):
        self.load()
        band = self.wb.slabs.filter(effective_from=D(2026, 10, 1)).order_by("above").first()
        seen = {"amount": str(band.amount)}
        # the Payroll Officer edits the band after the Controller's screen read it
        edited = self.in_role("Payroll Officer").patch(f"/api/hr/pay-component-slabs/{band.pk}/",
                                                       {"amount": "999"}, format="json")
        self.assertEqual(edited.status_code, 200, edited.content)
        answer = self.in_role("Controller").post(f"/api/hr/pay-component-slabs/{band.pk}/confirm/", seen,
                                                 format="json")
        self.assertEqual(answer.status_code, 400, "confirmed 999 on the strength of a screen that showed "
                                                  f"{seen['amount']}: {answer.content}")

    def test_p03_a_payroll_officer_cannot_walk_a_levy_round_the_gate_by_unmarking_it(self):
        self.load()
        person = self.worker("U", work_state="WB")
        self.paid(person, self.basic, "25000")
        self.paid(person, self.wb, "1")
        client = self.in_role("Payroll Officer")
        answer = client.patch(f"/api/hr/pay-components/{self.wb.pk}/", {"statutory": ""}, format="json")
        if answer.status_code >= 400:
            return
        try:
            run = self.month_run(2026, 10, post=False, employees=[person])
        except ValidationError:
            return
        paid = run.payslips.get(employee=person).lines.filter(component=self.wb).first()
        self.fail(f"PT of {paid.amount if paid else None} calculated at a schedule nobody confirmed, after "
                  f"the component was unmarked ({answer.status_code})")


class BonusWorld:
    def bonus_world(self, unconfirmed):
        controller = User.objects.get_or_create(username="c-bonus")[0]
        self.bonus = PayComponent.objects.create(
            code="BONUS", name="Bonus", kind=ComponentKind.EMPLOYER_COST, basis=ComponentBasis.PERCENT_OF_GROSS,
            expense_account=self.employer, liability_account=self.pf_payable, sequence=99,
            rounding=Rounding.PAISA, ceiling_figure=BONUS_CEILING)
        self.bonus.base_components.set([self.basic])
        for kind, value in ((FigureKind.BONUS_FLOOR, "7000"), (FigureKind.BONUS_ELIGIBILITY, "21000"),
                            (FigureKind.BONUS_MINIMUM_PERCENT, "8.33"), (FigureKind.BONUS_MAXIMUM_PERCENT, "20")):
            row = StatutoryFigure.objects.create(kind=kind, effective_from=D(2025, 11, 21), value=Decimal(value),
                                                 source="probe")
            if kind != unconfirmed:
                row.confirm(controller)
        wage = MinimumWage.objects.create(state="WB", employment="Bottling", zone="A", skill=Skill.UNSKILLED,
                                          effective_from=D(2026, 7, 1), basic=Decimal("9500"), source="probe")
        wage.confirm(controller)
        person = self.worker("G1", work_state="WB")
        EmployeePayDetails.objects.create(employee=person, scheduled_employment="Bottling", wage_zone="A",
                                          skill=Skill.UNSKILLED)
        self.paid(person, self.basic, "12000")
        self.paid(person, self.bonus, "8.33")
        return person


class GoLiveProbes(BonusWorld, LoadedTestCase):
    def test_p04_go_live_fails_bonus_when_the_rates_the_run_reads_are_unconfirmed(self):
        for kind in (FigureKind.BONUS_MINIMUM_PERCENT, FigureKind.BONUS_MAXIMUM_PERCENT):
            with self.subTest(kind=kind):
                StatutoryFigure.objects.all().delete()
                MinimumWage.objects.all().delete()
                from .payroll import EmployeeCompensation
                EmployeeCompensation.objects.filter(component__code="BONUS").delete()
                PayComponent.objects.filter(code="BONUS").delete()
                person = self.bonus_world(unconfirmed=kind)
                with self.assertRaisesMessage(ValidationError, "is not confirmed"):
                    self.month_run(2026, 10, post=False, employees=[person])
                gaps = dict((engine, missing) for engine, used, missing in statutory_gaps(D(2026, 10, 26)))
                self.assertTrue(gaps["Bonus"], "the run is refused but go_live_check's Bonus line is clear")

    def test_p05_a_confirmed_old_row_does_not_hide_an_unconfirmed_standing_one(self):
        self.mark(self.esi, Statutory.ESI)
        StatutoryFigure.objects.create(kind=FigureKind.ESI_WAGE_CEILING, effective_from=D(2026, 6, 1),
                                       value=Decimal("30000"), source="probe")
        gaps = dict((engine, missing) for engine, used, missing in statutory_gaps(D(2026, 10, 26)))
        self.assertTrue(gaps["ESI"])


class EsiProbes(LoadedTestCase):
    def setUp(self):
        super().setUp()
        self.mark(self.esi, Statutory.ESI)
        self.mark(self.esi_er, Statutory.ESI_EMPLOYER)
        self.p = self.worker("E", work_state="WB")
        self.paid(self.p, self.basic, "12000")
        for c in (self.esi, self.esi_er):
            self.paid(self.p, c, "1")

    def test_p06_a_rate_changing_on_the_1st_mid_month_and_last_day_for_a_whole_month_run(self):
        # hand: 12,000 x 1% = 120; x 0.75% = 90 (calc.py). The figure standing on the period's last day governs
        # the whole run (the rule O222 states); this records what each date does.
        got = {}
        for day in (D(2026, 10, 1), D(2026, 10, 16), D(2026, 10, 31), D(2026, 11, 1)):
            StatutoryFigure.objects.filter(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from__gt=D(2000, 1, 1)
                                           ).delete()
            StatutoryFigure.objects.create(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from=day,
                                           value=Decimal("1"), source="probe", confirmed_on=D(2026, 9, 1))
            run = self.month_run(2026, 10, post=False, employees=[self.p])
            got[str(day)] = self.line(run, self.p, self.esi).amount
            run.delete()
        self.assertEqual(got, {"2026-10-01": Decimal("120.00"), "2026-10-16": Decimal("120.00"),
                               "2026-10-31": Decimal("120.00"), "2026-11-01": Decimal("90.00")})

    def test_p07_two_half_month_runs_across_a_mid_month_change_each_take_their_own_rate(self):
        StatutoryFigure.objects.create(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from=D(2026, 10, 16),
                                       value=Decimal("1"), source="probe", confirmed_on=D(2026, 9, 1))
        from .payroll import PayRun, EmployeeCompensation

        EmployeeCompensation.objects.filter(employee=self.p, component=self.basic).update(amount=Decimal("6000"))
        amounts = []
        for start, end in ((D(2026, 10, 1), D(2026, 10, 15)), (D(2026, 10, 16), D(2026, 10, 31))):
            run = PayRun.objects.create(period_start=start, period_end=end, pay_date=end)
            run.calculate(employees=[self.p])
            run.post()
            amounts.append(self.line(run, self.p, self.esi).amount)
        # a fortnight's basic is paid whole per run: 6,000 x 0.75% = 45.00; 6,000 x 1% = 60.00 (calc.py)
        self.assertEqual(amounts, [Decimal("45.00"), Decimal("60.00")])

    def test_p08_a_figure_edit_after_the_run_posted_is_refused_over_the_api_and_the_files_still_read_it(self):
        from .statutory_files import esi_rows

        self.p.esi_number = "1234567890"
        self.p.save()
        run = self.month_run(2026, 10, employees=[self.p])
        row = StatutoryFigure.objects.get(kind=FigureKind.ESI_EMPLOYEE_RATE)
        client = self.in_role("Payroll Officer")
        for change in ({"value": "2"}, {"effective_from": "1999-01-01"}):
            self.assertEqual(client.patch(f"/api/hr/statutory-figures/{row.pk}/", change, format="json"
                                          ).status_code, 400, change)
        self.assertEqual(client.delete(f"/api/hr/statutory-figures/{row.pk}/").status_code, 400)
        row.refresh_from_db()
        self.assertIsNotNone(row.confirmed_on)
        # a later row appears: the posted run's line and file are unmoved
        StatutoryFigure.objects.create(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from=D(2026, 10, 20),
                                       value=Decimal("5"), source="probe", confirmed_on=D(2026, 10, 21))
        self.assertEqual(self.line(run, self.p, self.esi).amount, Decimal("90.00"))
        self.assertEqual(esi_rows(run)[0]["wages"], 12000)

    def test_p09_the_ceiling_is_read_on_the_period_not_where_the_slab_of_a_voided_run_left_it(self):
        # 22,000 against 21,000 until a 30,000 ceiling from 1 November: October none, November covered
        # (the ceiling then standing; recorded for the reader, not a finding).
        rich = self.worker("R", work_state="WB")
        self.paid(rich, self.basic, "22000")
        self.paid(rich, self.esi, "1")
        StatutoryFigure.objects.create(kind=FigureKind.ESI_WAGE_CEILING, effective_from=D(2026, 11, 1),
                                       value=Decimal("30000"), source="probe", confirmed_on=D(2026, 9, 1))
        october = self.month_run(2026, 10, employees=[rich])
        self.assertFalse(october.payslips.get(employee=rich).lines.filter(component=self.esi).exists())


class BonusProbes(BonusWorld, LoadedTestCase):
    def test_p10_bonus_crossing_21000_mid_year_and_a_floor_of_7000(self):
        person = self.bonus_world(unconfirmed=None)
        # basic 20,000 to 31 Jan, 22,000 from 1 Feb (a new compensation row)
        from .payroll import EmployeeCompensation

        EmployeeCompensation.objects.filter(employee=person, component=self.basic).update(
            effective_to=D(2027, 1, 31))
        self.paid(person, self.basic, "22000", since=D(2027, 2, 1))
        EmployeeCompensation.objects.filter(employee=person, component=self.basic, amount=Decimal("12000")
                                            ).update(amount=Decimal("20000"))
        jan = self.month_run(2027, 1, employees=[person])
        feb = self.month_run(2027, 2, employees=[person])
        # ceiling = max(7,000, 9,500) = 9,500 -> 8.33% = 791.35
        self.assertEqual(self.line(jan, person, self.bonus).amount, Decimal("791.35"))
        self.assertFalse(feb.payslips.get(employee=person).lines.filter(component=self.bonus).exists())

    def test_p11_a_bonus_rate_outside_the_dated_range_is_refused(self):
        person = self.bonus_world(unconfirmed=None)
        from .payroll import EmployeeCompensation

        EmployeeCompensation.objects.filter(employee=person, component=self.bonus).update(amount=Decimal("25"))
        with self.assertRaises(ValidationError):
            self.month_run(2026, 10, post=False, employees=[person])
        EmployeeCompensation.objects.filter(employee=person, component=self.bonus).update(amount=Decimal("8"))
        with self.assertRaises(ValidationError):
            self.month_run(2026, 10, post=False, employees=[person])
