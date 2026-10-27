"""
The law's figures loaded from statutory/ (setup_statutory), used by nothing
that posts until the Controller confirms them, go_live_check's line per
engine, and the bonus rule (g24 brief fix_statutory, decisions 1, 2 and 5).

Numbers (worked in /home/user/work/calc_fix_statutory/calc.py):
  West Bengal professional tax on 25,000: 130 in September 2026 (the old
  schedule, 15,000 to 25,000), 100 in October (the schedule of 1 October
  2026, 20,000 to 30,000).
  Welfare fund: 3 from the employee and 30 from the employer in June and
  December, nothing in November.
  Bonus at 8.33% of wages capped at the higher of 7,000 and the minimum wage
  (a test row of 9,500 + VDA 1,058 = 10,558), only up to 21,000 a month:
    12,000 -> 8.33% of 10,558 = 879.48;  9,000 -> 749.70;  21,000 -> 879.48;
    21,000.01 -> none;  a minimum wage of 6,500 -> the floor, 583.10;
  the flat 7,000 ceiling a posted August run kept stays 583.10.
"""

import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from apps.accounting.models import TcsRate, TdsRate, TdsSection
from apps.accounting.statutory_load import rows

from .payroll import ComponentBasis, ComponentKind, PayComponent, PayComponentSlab, Rounding, Statutory
from .pay_details import EmployeePayDetails
from .statutory import BONUS_CEILING, FigureKind, MinimumWage, Skill, StatutoryFigure
from .statutory_check import statutory_gaps
from .tests_dated_figures import DatedFiguresTestCase

D = datetime.date


class LoadedTestCase(DatedFiguresTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.wb = PayComponent.objects.create(code="PTWB", name="Professional tax", kind=ComponentKind.DEDUCTION,
                                              basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
                                              sequence=95, statutory=Statutory.PT)
        self.wb.base_components.set([self.basic])
        self.lwf = [PayComponent.objects.create(
            code=code, name=code, kind=kind, basis=ComponentBasis.SLAB, liability_account=self.pt_payable,
            expense_account=self.employer if kind == ComponentKind.EMPLOYER_COST else None, sequence=97,
            statutory=statutory) for code, kind, statutory in (
                ("LWF", ComponentKind.DEDUCTION, Statutory.LWF),
                ("LWF-ER", ComponentKind.EMPLOYER_COST, Statutory.LWF_EMPLOYER))]

    def load(self):
        out = StringIO()
        call_command("setup_statutory", stdout=out)
        return out.getvalue()

    def in_role(self, role):
        user = User.objects.create_user(f"{role.replace(' ', '_')}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def confirm_everything(self):
        controller = User.objects.create_user("controller-all")
        controller.groups.add(Group.objects.get(name="Controller"))
        for model in (StatutoryFigure, MinimumWage, TdsRate, TcsRate):
            for row in model.objects.filter(confirmed_on__isnull=True):
                row.confirm(controller)
        for band in PayComponentSlab.objects.filter(confirmed_on__isnull=True):
            band.confirm(controller)
        return controller


class SetupStatutoryTests(LoadedTestCase):
    def test_every_file_is_loaded_unconfirmed_with_its_source_and_run_again_adds_nothing(self):
        said = self.load()
        from .management.commands.setup_statutory import FIGURE_FILES

        figures = sum(len(rows(name)) for name in FIGURE_FILES)
        self.assertEqual(StatutoryFigure.objects.count(), figures)
        self.assertEqual(MinimumWage.objects.count(), len(rows("minimum_wages")))
        self.assertEqual(TdsRate.objects.count(), len(rows("tds_rates")))
        self.assertEqual(TcsRate.objects.count(), len(rows("tcs_rates")))
        self.assertEqual(self.wb.slabs.count(), len(rows("professional_tax")))
        self.assertEqual([component.slabs.count() for component in self.lwf], [2, 2])
        for model in (StatutoryFigure, MinimumWage, TdsRate, TcsRate, PayComponentSlab):
            self.assertFalse(model.objects.filter(confirmed_on__isnull=False).exclude(
                pk__in=PayComponentSlab.objects.filter(component=self.pt).values("pk")
                if model is PayComponentSlab else []).exists(), model)
        self.assertFalse(StatutoryFigure.objects.filter(source="").exists())
        contractor = TdsSection.objects.get(code="194C")
        self.assertEqual((contractor.section_1961, contractor.section_2025), ("194C", "393(1)"))
        self.assertIn("waiting for the Controller to confirm", said)
        again = self.load()
        self.assertIn("0 loaded", again)
        self.assertEqual(StatutoryFigure.objects.count(), figures)

    def test_a_levy_with_no_component_is_named_not_loaded(self):
        self.wb.is_active = False
        self.wb.save()
        self.assertIn("Professional tax: no active pay component", self.load())
        self.assertFalse(PayComponentSlab.objects.filter(component=self.wb).exists())

    def test_a_loaded_schedule_is_paid_at_by_no_run_until_the_controller_confirms_it(self):
        self.load()
        person = self.worker("M", work_state="WB")
        self.paid(person, self.basic, "25000")
        self.paid(person, self.wb, "1")
        with self.assertRaisesMessage(ValidationError, "of WB from 01 Oct 2026 is not confirmed, so nothing that "
                                                       "posts may use it yet: the Controller confirms it"):
            self.month_run(2026, 10, post=False, employees=[person])
        band = self.wb.slabs.filter(effective_from=D(2026, 10, 1)).first()
        url = f"/api/hr/pay-component-slabs/{band.pk}/confirm/"
        self.assertEqual(self.in_role("Payroll Officer").post(url).status_code, 403)
        answer = self.in_role("Controller").post(url)
        self.assertEqual(answer.status_code, 200, answer.content)
        self.assertEqual(len(answer.json()), 5)
        self.assertFalse(self.wb.slabs.filter(effective_from=D(2026, 10, 1), confirmed_on__isnull=True).exists())
        october = self.month_run(2026, 10, employees=[person])
        self.assertEqual(self.line(october, person, self.wb).amount, Decimal("100.00"))
        with self.assertRaisesMessage(ValidationError, "of WB from 01 Apr 2008 is not confirmed"):
            self.month_run(2026, 9, post=False, employees=[person])
        self.wb.slabs.filter(effective_from=D(2008, 4, 1)).first().confirm_schedule(User.objects.get(
            username__startswith="Controller"))
        september = self.month_run(2026, 9, employees=[person])
        self.assertEqual(self.line(september, person, self.wb).amount, Decimal("130.00"))

    def test_a_loaded_ceiling_is_paid_at_by_no_run_until_the_controller_confirms_it(self):
        # 12% of basic 20,000 capped at the ceiling standing on 31 October 2026: 25,000 once confirmed, 2,400.00.
        self.load()
        self.pf.base_ceiling, self.pf.ceiling_figure, self.pf.statutory = None, FigureKind.EPF_WAGE_CEILING, \
            Statutory.PF
        self.pf.save()
        person = self.worker("P", work_state="WB")
        self.paid(person, self.basic, "20000")
        self.paid(person, self.pf, "12")
        with self.assertRaisesMessage(ValidationError, "EPF wage ceiling, a month's wages of 25000 from 17 Sep 2026 "
                                                       "is not confirmed, so nothing that posts may use it yet"):
            self.month_run(2026, 10, post=False, employees=[person])
        controller = User.objects.create_user("controller-pf")
        for kind in (FigureKind.EPF_WAGE_CEILING, FigureKind.EPS_WAGE_CEILING):
            StatutoryFigure.objects.get(kind=kind, effective_from=D(2026, 9, 17)).confirm(controller)
        october = self.month_run(2026, 10, employees=[person])
        self.assertEqual(self.line(october, person, self.pf).amount, Decimal("2400.00"))

    def test_the_welfare_fund_is_june_and_december_at_three_and_thirty(self):
        self.load()
        self.confirm_everything()
        person = self.worker("W", work_state="WB")
        self.paid(person, self.basic, "9000")
        for component in self.lwf:
            self.paid(person, component, "1")
        june = self.month_run(2026, 6, employees=[person])
        november = self.month_run(2026, 11, post=False, employees=[person])
        self.assertEqual([self.line(june, person, c).amount for c in self.lwf], [Decimal("3.00"), Decimal("30.00")])
        self.assertFalse(november.payslips.get(employee=person).lines.filter(component__in=self.lwf).exists())

    def test_a_figure_is_confirmed_by_the_controller_only_and_changed_it_waits_again(self):
        self.load()
        rise = StatutoryFigure.objects.get(kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 9, 17))
        url = f"/api/hr/statutory-figures/{rise.pk}/confirm/"
        seen = {"kind": rise.kind, "state": "", "effective_from": "2026-09-17", "value": "25000.0000"}
        self.assertEqual(self.in_role("Payroll Officer").post(url, seen, format="json").status_code, 403)
        moved = self.in_role("Controller").post(url, {**seen, "value": "21000"}, format="json")
        self.assertEqual(moved.status_code, 400)
        answer = self.in_role("Controller").post(url, seen, format="json")
        self.assertEqual(answer.status_code, 200, answer.content)
        rise.refresh_from_db()
        self.assertIsNotNone(rise.confirmed_by)
        self.assertIsNotNone(rise.confirmed_on)
        rise.value = Decimal("24000")
        rise.save()
        rise.refresh_from_db()
        self.assertIsNone(rise.confirmed_on)


class GoLiveStatutoryTests(LoadedTestCase):
    def go_live(self):
        out = StringIO()
        try:
            call_command("go_live_check", stdout=out)
        except CommandError as error:
            return out.getvalue() + str(error)
        return out.getvalue()

    def test_each_engine_needs_a_confirmed_figure_covering_today_and_a_planted_gap_fails_by_name(self):
        for component, marked in ((self.pf, Statutory.PF), (self.esi, Statutory.ESI)):
            self.mark(component, marked)
        bonus = PayComponent.objects.create(
            code="BONUS", name="Bonus", kind=ComponentKind.EMPLOYER_COST, basis=ComponentBasis.PERCENT_OF_GROSS,
            expense_account=self.employer, liability_account=self.pf_payable, sequence=99,
            ceiling_figure=BONUS_CEILING)
        bonus.base_components.set([self.basic])
        from .models import Employee

        Employee.objects.update(employment_status="terminated")
        person = self.worker("G", work_state="WB")
        EmployeePayDetails.objects.create(employee=person, scheduled_employment="Bottling and packaging",
                                          wage_zone="A", skill=Skill.UNSKILLED)
        self.load()
        said = self.go_live()
        for engine in ("TDS", "TCS", "PF", "ESI", "PT", "LWF", "Bonus", "Minimum wage"):
            self.assertIn(f"FAIL  Statutory: {engine}: No confirmed figure covers today", said)
        self.confirm_everything()
        said = self.go_live()
        for engine in ("TDS", "TCS", "PF", "ESI", "PT", "LWF", "Bonus", "Minimum wage"):
            self.assertIn(f"ok    Statutory: {engine}: confirmed figures cover today", said)
        # The planted gap: the EPF ceiling standing today loses its confirmation.
        StatutoryFigure.objects.filter(kind=FigureKind.EPF_WAGE_CEILING, effective_from=D(2026, 9, 17)).update(
            confirmed_on=None, confirmed_by=None)
        said = self.go_live()
        self.assertIn("FAIL  Statutory: PF: No confirmed figure covers today for EPF wage ceiling, a month's wages "
                      "(loaded, not confirmed).", said)
        self.assertIn("ok    Statutory: ESI", said)
        self.assertEqual([engine for engine, used, missing in statutory_gaps(D(2026, 10, 26)) if missing], ["PF"])


class BonusRuleTests(LoadedTestCase):
    def setUp(self):
        super().setUp()
        controller = User.objects.create_user("controller-bonus")
        self.bonus_payable = self.pf_payable
        self.bonus = PayComponent.objects.create(
            code="BONUS", name="Bonus", kind=ComponentKind.EMPLOYER_COST, basis=ComponentBasis.PERCENT_OF_GROSS,
            expense_account=self.employer, liability_account=self.bonus_payable, sequence=99,
            rounding=Rounding.PAISA, ceiling_figure=BONUS_CEILING)
        self.bonus.base_components.set([self.basic])
        for kind, value in ((FigureKind.BONUS_FLOOR, "7000"), (FigureKind.BONUS_ELIGIBILITY, "21000"),
                            (FigureKind.BONUS_MINIMUM_PERCENT, "8.33"), (FigureKind.BONUS_MAXIMUM_PERCENT, "20")):
            StatutoryFigure.objects.create(kind=kind, effective_from=D(2025, 11, 21), value=Decimal(value),
                                           source="test row").confirm(controller)
        self.wage = MinimumWage.objects.create(state="WB", employment="Bottling and packaging", zone="A",
                                               skill=Skill.UNSKILLED, effective_from=D(2026, 7, 1),
                                               basic=Decimal("9500"), vda=Decimal("1058"), source="test row")
        self.wage.confirm(controller)
        MinimumWage.objects.create(state="WB", employment="Bottling and packaging", zone="A", skill=Skill.SKILLED,
                                   effective_from=D(2026, 7, 1), basic=Decimal("6500"),
                                   source="test row").confirm(controller)
        self.controller = controller

    def worker_on(self, code, basic, skill=Skill.UNSKILLED):
        person = self.worker(code, work_state="WB")
        EmployeePayDetails.objects.create(employee=person, scheduled_employment="Bottling and packaging",
                                          wage_zone="A", skill=skill)
        self.paid(person, self.basic, basic)
        self.paid(person, self.bonus, "8.33")
        return person

    def test_the_ceiling_is_the_higher_of_the_floor_and_the_minimum_wage_up_to_the_eligibility(self):
        people = {
            "12000": self.worker_on("A1", "12000"), "9000": self.worker_on("A2", "9000"),
            "21000": self.worker_on("A3", "21000"), "21000.01": self.worker_on("A4", "21000.01"),
            "floor": self.worker_on("A5", "12000", skill=Skill.SKILLED)}
        october = self.month_run(2026, 10, employees=list(people.values()))
        amounts = {key: [line.amount for line in october.payslips.get(employee=person).lines.filter(
            component=self.bonus)] for key, person in people.items()}
        self.assertEqual(amounts, {"12000": [Decimal("879.48")], "9000": [Decimal("749.70")],
                                   "21000": [Decimal("879.48")], "21000.01": [], "floor": [Decimal("583.10")]})
        line = self.line(october, people["12000"], self.bonus)
        self.assertEqual(line.base_ceiling, Decimal("10558.00"))
        self.assertEqual(line.kept_terms["minimum_wage"], [self.wage.pk, "2026-07-01", "10558"])
        self.assertEqual(line.kept_terms["bonus_floor"][2], "7000")
        self.assertEqual(line.kept_terms["bonus_eligibility"][2], "21000")
        self.wage.vda = Decimal("1100")
        with self.assertRaisesMessage(ValidationError, "is what"):
            self.wage.save()

    def test_a_vda_revision_waiting_for_confirmation_stops_the_month_it_covers_only(self):
        person = self.worker_on("B1", "12000")
        MinimumWage.objects.create(state="WB", employment="Bottling and packaging", zone="A",
                                   skill=Skill.UNSKILLED, effective_from=D(2027, 1, 1), basic=Decimal("9500"),
                                   vda=Decimal("1200"), source="test row")
        december = self.month_run(2026, 12, post=False, employees=[person])
        self.assertEqual(self.line(december, person, self.bonus).amount, Decimal("879.48"))
        with self.assertRaisesMessage(ValidationError, "The minimum wage for Bottling and packaging, unskilled, in "
                                                       "WB A of 10700 from 01 Jan 2027 is not confirmed"):
            self.month_run(2027, 1, post=False, employees=[person])

    def test_without_a_scheduled_employment_or_skill_the_bonus_is_refused_in_words(self):
        person = self.worker("C1", work_state="WB")
        self.paid(person, self.basic, "12000")
        self.paid(person, self.bonus, "8.33")
        with self.assertRaisesMessage(ValidationError, "state, scheduled employment or skill is not set"):
            self.month_run(2026, 10, post=False, employees=[person])

    def test_a_run_posted_at_the_flat_ceiling_keeps_it_when_the_component_takes_the_rule(self):
        person = self.worker_on("D1", "12000")
        self.bonus.ceiling_figure, self.bonus.base_ceiling = "", Decimal("7000")
        self.bonus.save()
        august = self.month_run(2026, 8, employees=[person])
        self.bonus.ceiling_figure, self.bonus.base_ceiling = BONUS_CEILING, None
        self.bonus.save()
        september = self.month_run(2026, 9, employees=[person])
        kept = self.line(august, person, self.bonus)
        self.assertEqual((kept.amount, kept.base_ceiling), (Decimal("583.10"), Decimal("7000.00")))
        self.assertEqual(self.line(september, person, self.bonus).amount, Decimal("879.48"))
        kept.refresh_from_db()
        self.assertEqual((kept.amount, kept.base_ceiling), (Decimal("583.10"), Decimal("7000.00")))


    def test_a_bonus_rate_outside_the_laws_least_and_most_is_refused_and_the_terms_are_kept(self):
        person = self.worker_on("E1", "12000")
        october = self.month_run(2026, 10, post=False, employees=[person])
        kept = self.line(october, person, self.bonus).kept_terms
        self.assertEqual((kept["bonus_minimum_percent"][2], kept["bonus_maximum_percent"][2]), ("8.33", "20"))
        high = self.worker_on("E2", "12000")
        from .payroll import EmployeeCompensation

        EmployeeCompensation.objects.filter(employee=high, component=self.bonus).update(amount=Decimal("25"))
        with self.assertRaisesMessage(ValidationError, "at 25%, and the law's bonus on 31 Oct 2026 is from 8.33% "
                                                       "to 20% of wages"):
            self.month_run(2026, 10, post=False, employees=[high])


class EsiReadsTheDatedRatesTests(LoadedTestCase):
    """
    ESI at the dated rates: 0.75% and 3.25% (test rows from 2000), the employee's share 1% from
    1 October 2026 (an illustrative change, not the gazette's). Basic 12,000, rounded up to the rupee:
    September 90 and 390, October 120 and 390. 22,000 is past the dated 21,000: no ESI.
    """

    def setUp(self):
        super().setUp()
        self.mark(self.esi, Statutory.ESI)
        self.mark(self.esi_er, Statutory.ESI_EMPLOYER)
        self.rise = StatutoryFigure.objects.create(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from=D(2026, 10, 1),
                                                   value=Decimal("1"), source="test row", confirmed_on=D(2026, 9, 1))
        self.insured = self.worker("I", work_state="WB")
        self.paid(self.insured, self.basic, "12000")
        for component in (self.esi, self.esi_er):
            self.paid(self.insured, component, "1")

    def test_a_run_before_the_change_and_a_run_after_it_each_read_their_own_rate(self):
        september = self.month_run(2026, 9, employees=[self.insured])
        october = self.month_run(2026, 10, employees=[self.insured])
        lines = lambda run: [(line.amount, line.rate) for line in (  # noqa: E731
            self.line(run, self.insured, self.esi), self.line(run, self.insured, self.esi_er))]
        self.assertEqual(lines(september), [(Decimal("90.00"), Decimal("0.7500")), (Decimal("390.00"),
                                                                                       Decimal("3.2500"))])
        self.assertEqual(lines(october), [(Decimal("120.00"), Decimal("1.0000")), (Decimal("390.00"),
                                                                                     Decimal("3.2500"))])
        kept = self.line(september, self.insured, self.esi).kept_terms
        self.assertEqual(kept["esi_employee_rate"][2], "0.75")
        self.assertEqual(kept["esi_ceiling"][2], "21000")
        old = StatutoryFigure.objects.get(kind=FigureKind.ESI_EMPLOYEE_RATE, effective_from=D(2000, 1, 1))
        old.value = Decimal("0.8")
        with self.assertRaisesMessage(ValidationError, "is what"):
            old.save()
        self.assertEqual(self.line(september, self.insured, self.esi).rate, Decimal("0.7500"))

    def test_past_the_dated_ceiling_there_is_no_esi_and_a_typed_rate_is_refused(self):
        rich = self.worker("R", work_state="WB")
        self.paid(rich, self.basic, "22000")
        self.paid(rich, self.esi, "1")
        october = self.month_run(2026, 10, post=False, employees=[rich])
        self.assertFalse(october.payslips.get(employee=rich).lines.filter(component=self.esi).exists())
        with self.assertRaisesMessage(ValidationError, "Give it as 1: they are insured"):
            self.paid(self.worker("T"), self.esi, "0.75")
        self.esi.coverage_ceiling = Decimal("21000")
        with self.assertRaisesMessage(ValidationError, "not a ceiling of its own"):
            self.esi.save()

    def test_an_unconfirmed_rate_stops_the_month_it_covers(self):
        StatutoryFigure.objects.filter(pk=self.rise.pk).update(confirmed_on=None)
        self.month_run(2026, 9, post=False, employees=[self.insured])
        with self.assertRaisesMessage(ValidationError, "ESI, the employee's share, per cent of wages of 1 from "
                                                       "01 Oct 2026 is not confirmed"):
            self.month_run(2026, 10, post=False, employees=[self.insured])


class EveryFigureIsReadTests(LoadedTestCase):
    def test_audit_invariants_reports_a_figure_no_engine_reads(self):
        from apps.core.management.commands.audit_invariants import Command

        command = Command()
        self.assertEqual(command.unread_figures("\n".join(f"FigureKind.{kind.name}" for kind in FigureKind), ""), [])
        planted = "\n".join(f"FigureKind.{kind.name}" for kind in FigureKind if kind != FigureKind.ESI_EMPLOYER_RATE)
        self.assertEqual([detail for _shape, detail in command.unread_figures(planted, "")],
                         ["The statutory figure \"ESI, the employer's share, per cent of wages\" is loaded and "
                          "confirmed but no engine reads it."])
        # A mention in the go-live check alone is not a read.
        self.assertEqual(len(command.unread_figures(planted + "\nFigureKind.ESI_EMPLOYER_RATE",
                                                    "FigureKind.ESI_EMPLOYER_RATE")), 1)


class TheStatutoryLinkIsTheControllersTests(LoadedTestCase):
    """Review finding 1 (p03): unmarking a levy walked round the unconfirmed gate and hid it from go-live."""

    def test_a_payroll_officer_cannot_relink_and_the_controllers_relink_is_recorded(self):
        from apps.core.history import RecordEvent

        url = f"/api/hr/pay-components/{self.wb.pk}/"
        refused = self.in_role("Payroll Officer").patch(url, {"statutory": ""}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("is the Controller's to change", refused.content.decode())
        self.assertEqual(self.in_role("Payroll Officer").post(url + "relink/", {"statutory": ""},
                                                              format="json").status_code, 403)
        renamed = self.in_role("Payroll Officer").patch(url, {"name": "Professional tax (WB)"}, format="json")
        self.assertEqual(renamed.status_code, 200, renamed.content)
        answer = self.in_role("Controller").post(url + "relink/", {"statutory": ""}, format="json")
        self.assertEqual(answer.status_code, 200, answer.content)
        self.wb.refresh_from_db()
        self.assertEqual(self.wb.statutory, "")
        self.assertTrue(RecordEvent.objects.filter(object_id=self.wb.pk, action="relink",
                                                   summary="statutory pt to none").exists())

    def test_go_live_counts_professional_tax_from_where_people_work_not_from_the_link(self):
        from .models import Employee

        Employee.objects.update(employment_status="terminated")
        self.worker("Q", work_state="WB")
        PayComponent.objects.filter(pk=self.wb.pk).update(statutory="")
        [(used, missing)] = [(used, missing) for engine, used, missing in statutory_gaps(D(2026, 10, 26))
                             if engine == "PT"]
        self.assertTrue(used)
        self.assertEqual(missing, ["no active pay component marked 'Professional tax' for WB"])


class EveryEngineAsksWhatItReadsTests(LoadedTestCase):
    """Review finding 2 (p04): the bonus line asked two of the four figures the bonus reads."""

    def test_every_figure_the_pay_run_reads_is_in_its_engines_go_live_line(self):
        import re
        from pathlib import Path

        from .statutory import ENGINE_FIGURES, read_figure

        source = (Path(__file__).parent / "payroll.py").read_text() + (Path(__file__).parent / "statutory.py").read_text()
        for engine, kind in re.findall(r'read_figure\(\s*"(\w+)", FigureKind\.(\w+)', source):
            self.assertIn(FigureKind[kind], ENGINE_FIGURES[engine], (engine, kind))
        with self.assertRaisesMessage(AssertionError, "which ENGINE_FIGURES does not list for it"):
            read_figure("ESI", FigureKind.BONUS_FLOOR, D(2026, 10, 31))
