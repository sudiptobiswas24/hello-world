"""
The statutory bonus for an accounting year (known limit 19): thirty days'
work, the yearly statement, its declaration, the payout and each reverse.

Numbers worked in /home/user/work/calc_g38_hr/bonus.py. The accounting year
1 April 2025 to 31 March 2026, everyone Monday to Friday, basic 12,000 a
month, the calculation ceiling 7,000 (the dated floor; the minimum wage here
is 6,500), eligibility 21,000, accrued at 8.33%: 583.10 a month.

  A  the whole year: 261 days, wages counted 84,000.00, accrued 6,997.20;
     owed at 8.33% 6,997.20 (difference 0.00), at 10% 8,400.00 (1,402.80)
  B  joined 2 March 2026: 22 days, under thirty: accrued 583.10, owed 0.00,
     difference -583.10
  E  joined 18 February: 8 + 22 = 30 days: February's wages 12,000 x 8/20 =
     4,800.00 (accrued 399.84), March 7,000.00 (583.10): counted 11,800.00,
     accrued 982.94, owed at 10% 1,180.00, difference 197.06
  F  joined 19 February: 29 days: accrued 349.86 + 583.10 = 932.96, owed 0.00
  The declaration at 10% books 1,402.80 - 583.10 + 197.06 - 932.96 = 83.80,
  and bonus payable stands at 9,496.20 + 83.80 = 9,580.00 = 8,400.00 + 1,180.00.

A rate changed inside December 2025 (23 working days), 8.33% to 10%:
  on the 1st:  700.00;  on the 16th: 278.87 + 365.22 = 644.09 (11 and 12
  days);  on the 31st: 557.75 + 30.43 = 588.18 (22 days and 1). Both in
  full would be 1,283.10. The year at 10% declared, owed 8,400.00: accrued
  7,464.80, 7,408.89 and 7,352.98; differences 935.20, 991.11 and 1,047.02.
"""

import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType

from .bonus import BonusDeclaration, bonus_owed, bonus_statement
from .pay_details import EmployeePayDetails
from .payroll import ComponentBasis, ComponentKind, EmployeeCompensation, PayComponent, PayRun, Rounding
from .statutory import BONUS_CEILING, FigureKind, MinimumWage, Skill, StatutoryFigure
from .statutory_check import statutory_gaps
from .tests_dated_figures import DatedFiguresTestCase

D = datetime.date
YEAR = [(2025, month) for month in range(4, 13)] + [(2026, 1), (2026, 2), (2026, 3)]


class BonusTestCase(DatedFiguresTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.basic.reduces_for_unpaid_leave = True
        self.basic.save()
        self.bonus_payable = Account.objects.create(code="2450", name="Bonus payable",
                                                    account_type=AccountType.LIABILITY)
        self.bonus_expense = Account.objects.create(code="6110", name="Bonus", account_type=AccountType.EXPENSE)
        self.bonus = PayComponent.objects.create(
            code="BONUS", name="Statutory bonus, accrued", kind=ComponentKind.EMPLOYER_COST,
            basis=ComponentBasis.PERCENT_OF_GROSS, expense_account=self.bonus_expense,
            liability_account=self.bonus_payable, sequence=110, rounding=Rounding.PAISA,
            ceiling_figure=BONUS_CEILING)
        self.bonus.base_components.set([self.basic])
        self.bonus.coverage_components.set([self.basic])
        self.payout = PayComponent.objects.create(
            code="BONUS-PAID", name="Statutory bonus, paid", kind=ComponentKind.EARNING,
            basis=ComponentBasis.FIXED, is_taxable=False, expense_account=self.bonus_payable, sequence=40)
        self.controller = User.objects.create_user("controller-bonus")
        self.controller.groups.add(Group.objects.get(name="Controller"))
        for kind, value in ((FigureKind.BONUS_FLOOR, "7000"), (FigureKind.BONUS_ELIGIBILITY, "21000"),
                            (FigureKind.BONUS_MINIMUM_PERCENT, "8.33"), (FigureKind.BONUS_MAXIMUM_PERCENT, "20")):
            StatutoryFigure.objects.create(kind=kind, effective_from=D(2025, 4, 1), value=Decimal(value),
                                           source="test").confirm(self.controller)
        MinimumWage.objects.create(state="WB", employment="Sacks", skill=Skill.UNSKILLED,
                                   effective_from=D(2025, 4, 1), basic=Decimal("6500"),
                                   source="test").confirm(self.controller)

    def earner(self, code, hire=D(2020, 1, 1), rate="8.33"):
        person = self.worker(code, hire=hire, work_state="WB")
        EmployeePayDetails.objects.create(employee=person, scheduled_employment="Sacks", skill=Skill.UNSKILLED)
        self.paid(person, self.basic, "12000")
        if rate:
            self.paid(person, self.bonus, rate)
        return person

    def months(self, months, people):
        return [self.month_run(year, month, employees=people) for year, month in months]

    def as_role(self, role):
        user = User.objects.create_user(f"{role.replace(' ', '-').lower()}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def row(self, statement, person):
        found = next(row for row in statement["rows"] if row["number"] == person.employee_number)
        return (found["days"], found["wages"], found["owed"], found["accrued"], found["difference"])


class TheYearsBonusTests(BonusTestCase):
    def test_under_thirty_days_nothing_is_owed_and_the_least_bonus_is_a_hundred_rupees(self):
        self.assertEqual(bonus_owed(Decimal("29"), Decimal("11200"), Decimal("10")), Decimal("0.00"))
        self.assertEqual(bonus_owed(Decimal("30"), Decimal("11800"), Decimal("10")), Decimal("1180.00"))
        self.assertEqual(bonus_owed(Decimal("30"), Decimal("1000"), Decimal("8.33")), Decimal("100.00"))
        self.assertEqual(bonus_owed(Decimal("261"), Decimal("0"), Decimal("8.33")), Decimal("0.00"))

    def test_the_statement_the_declaration_its_payout_and_each_reverse(self):
        a, b = self.earner("BA"), self.earner("BB", hire=D(2026, 3, 2))
        e, f = self.earner("BE", hire=D(2026, 2, 18)), self.earner("BF", hire=D(2026, 2, 19))
        people = [a, b, e, f]
        runs = self.months(YEAR, people)
        payable = lambda: self.balance(self.bonus_payable)  # noqa: E731
        self.assertEqual(payable(), Decimal("-9496.20"))

        # The statement, derived: at the dated least, and at 10%.
        self.assertEqual(self.row(bonus_statement(2025), a),
                         (Decimal("261"), Decimal("84000.00"), Decimal("6997.20"), Decimal("6997.20"), Decimal("0.00")))
        at_ten = bonus_statement(2025, Decimal("10"))
        self.assertEqual(self.row(at_ten, a), (Decimal("261"), Decimal("84000.00"), Decimal("8400.00"),
                                               Decimal("6997.20"), Decimal("1402.80")))
        self.assertEqual(self.row(at_ten, b), (Decimal("22"), Decimal("7000.00"), Decimal("0.00"),
                                               Decimal("583.10"), Decimal("-583.10")))
        self.assertEqual(self.row(at_ten, e), (Decimal("30"), Decimal("11800.00"), Decimal("1180.00"),
                                               Decimal("982.94"), Decimal("197.06")))
        self.assertEqual(self.row(at_ten, f), (Decimal("29"), Decimal("11200.00"), Decimal("0.00"),
                                               Decimal("932.96"), Decimal("-932.96")))
        self.assertEqual(at_ten["total"]["difference"], Decimal("83.80"))

        # Who may: the payroll officer reads the statement and cannot declare; a line manager and HR read neither.
        officer, controller = self.as_role("Payroll Officer"), self.as_role("Controller")
        read = officer.get("/api/hr/bonus-statement/", {"year": 2025, "percent": "10"})
        self.assertEqual(read.status_code, 200, read.content)
        mine = next(row for row in read.json()["rows"] if row["number"] == "BE")
        self.assertEqual((mine["days"], mine["wages"], mine["owed"], mine["accrued"], mine["difference"], mine["why"]),
                         ("30.00", "11800.00", "1180.00", "982.94", "197.06", ""))
        self.assertEqual(next(row for row in read.json()["rows"] if row["number"] == "BF")["why"],
                         "Worked under 30 days")
        self.assertEqual(read.json()["pay_by"], "2026-11-30")
        for role in ("Line Manager", "HR Admin"):
            self.assertEqual(self.as_role(role).get("/api/hr/bonus-statement/", {"year": 2025}).status_code, 403)
            self.assertEqual(self.as_role(role).get("/api/hr/bonus-declarations/").status_code, 403)
        url = "/api/hr/bonus-declarations/"
        self.assertEqual(officer.post(url, {"year": 2025, "percent": "10"}, format="json").status_code, 403)

        # Refused before anything is declared: outside the dated range, and a year still open.
        for percent in ("8", "21"):
            refused = controller.post(url, {"year": 2025, "percent": percent}, format="json")
            self.assertEqual(refused.status_code, 400)
            self.assertIn("from 8.33% to 20% of wages", str(refused.json()))
        today = timezone.localdate()
        this_year = today.year if today.month >= 4 else today.year - 1
        still_open = controller.post(url, {"year": this_year, "percent": "10"}, format="json")
        self.assertEqual(still_open.status_code, 400)
        self.assertIn("a bonus is declared on a year that has closed", str(still_open.json()))
        self.assertFalse(BonusDeclaration.objects.exists())

        # Declared at 10%: owed less accrued is booked, and every figure kept.
        early = controller.post(url, {"year": 2025, "percent": "10", "date": "2026-03-31"}, format="json")
        self.assertEqual(early.status_code, 400)
        self.assertIn("is not declared on", str(early.json()))
        made = controller.post(url, {"year": 2025, "percent": "10", "date": "2026-09-30"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["date"], "2026-09-30")
        declaration = BonusDeclaration.objects.get(pk=made.json()["id"])
        kept = {line.employee.employee_number: (line.days_worked, line.wages_counted, line.owed, line.accrued,
                                                 line.difference) for line in declaration.lines.all()}
        self.assertEqual(kept["BA"], (Decimal("261.00"), Decimal("84000.00"), Decimal("8400.00"), Decimal("6997.20"),
                                      Decimal("1402.80")))
        self.assertEqual(kept["BF"], (Decimal("29.00"), Decimal("11200.00"), Decimal("0.00"), Decimal("932.96"),
                                      Decimal("-932.96")))
        self.assertEqual([(line.account.code, line.debit, line.credit) for line in
                          declaration.journal_entry.lines.order_by("pk")],
                         [("6110", Decimal("83.80"), Decimal("0.00")), ("2450", Decimal("0.00"), Decimal("83.80"))])
        self.assertEqual(payable(), Decimal("-9580.00"))
        after = bonus_statement(2025)
        self.assertEqual(after["percent"], Decimal("10.0000"))
        self.assertEqual(self.row(after, a)[2:], (Decimal("8400.00"), Decimal("8400.00"), Decimal("0.00")))
        self.assertEqual(self.row(after, b)[2:], (Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))

        # It stands: not declared twice, not edited, not deleted, and its year's runs stay as they were.
        twice = controller.post(url, {"year": 2025, "percent": "12"}, format="json")
        self.assertEqual(twice.status_code, 400)
        self.assertIn("stands. Void it before declaring 2025-26 again", str(twice.json()))
        for method in (controller.patch, controller.put, controller.delete):
            # Nobody holds the permission to change or delete one, and the address takes neither.
            self.assertIn(method(f"{url}{declaration.pk}/", {"percent": "12"}, format="json").status_code, (403, 405))
        declaration.percent = Decimal("12")
        with self.assertRaisesMessage(ValidationError, "void it and declare again"):
            declaration.save()
        with self.assertRaisesMessage(ValidationError, "is posted: void it instead"):
            declaration.delete()
        with self.assertRaisesMessage(ValidationError, "is posted with its declaration"):
            declaration.lines.first().delete()
        with self.assertRaisesMessage(ValidationError, "it is not voided while that stands"):
            runs[-1].void()
        late = PayRun.objects.create(period_start=D(2026, 3, 30), period_end=D(2026, 3, 31), pay_date=D(2026, 3, 31))
        late.calculate(employees=[a])
        with self.assertRaisesMessage(ValidationError, "it is not posted while that stands"):
            late.post()
        late.delete()

        # The payout: on October's pay, by the payroll officer; twice is refused; off again and on again.
        declaration = BonusDeclaration.objects.get(pk=declaration.pk)
        early = officer.post(f"{url}{declaration.pk}/put_on_pay/", {"month": "2026-03-15"}, format="json")
        self.assertEqual(early.status_code, 400)
        self.assertIn("it is not paid on the pay of March 2026, before it", str(early.json()))
        put = officer.post(f"{url}{declaration.pk}/put_on_pay/", {"month": "2026-10-15"}, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        rows = lambda: sorted(EmployeeCompensation.objects.filter(component=self.payout).values_list(  # noqa: E731
            "employee__employee_number", "amount", "effective_from", "effective_to"))
        on_pay = [("BA", Decimal("8400.0000"), D(2026, 10, 1), D(2026, 10, 31)),
                  ("BE", Decimal("1180.0000"), D(2026, 10, 1), D(2026, 10, 31))]
        self.assertEqual(rows(), on_pay)
        again = officer.post(f"{url}{declaration.pk}/put_on_pay/", {"month": "2026-11-15"}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("Nothing of Bonus for 2025-26 at 10% is left to put on pay", str(again.json()))
        self.assertEqual(officer.post(f"{url}{declaration.pk}/take_off_pay/", {}, format="json").status_code, 200)
        self.assertEqual(rows(), [])
        self.assertEqual(officer.post(f"{url}{declaration.pk}/take_off_pay/", {}, format="json").status_code, 400)
        self.assertEqual(officer.post(f"{url}{declaration.pk}/put_on_pay/", {"month": "2026-10-01"},
                                      format="json").status_code, 200)
        self.assertEqual(rows(), on_pay)

        # October's run pays it out of bonus payable: what is left there is October's own accrual, 2 x 583.10.
        october = self.month_run(2026, 10, employees=[a, e])
        self.assertEqual(self.line(october, a, self.payout).amount, Decimal("8400.00"))
        self.assertEqual(self.line(october, e, self.payout).posted_account, self.bonus_payable)
        self.assertEqual(payable(), Decimal("-1166.20"))
        paid = officer.get("/api/hr/bonus-statement/", {"year": 2025}).json()
        self.assertEqual([(row["number"], row["paid"], row["to_pay"]) for row in paid["rows"]],
                         [("BA", "8400.00", "0.00"), ("BB", "0.00", "0.00"), ("BE", "1180.00", "0.00"),
                          ("BF", "0.00", "0.00")])

        # Reverses. Paid, the declaration is not voided; the payout run voided, it is owed again.
        self.assertEqual(officer.post(f"{url}{declaration.pk}/void/", {}, format="json").status_code, 403)
        held = controller.post(f"{url}{declaration.pk}/void/", {}, format="json")
        self.assertEqual(held.status_code, 400)
        self.assertIn("Void that run before voiding the declaration it paid", str(held.json()))
        october.void()
        self.assertEqual(payable(), Decimal("-9580.00"))
        owed_again = bonus_statement(2025)
        self.assertEqual([(row["number"], row["paid"], row["to_pay"]) for row in owed_again["rows"]][0],
                         ("BA", Decimal("0.00"), Decimal("8400.00")))
        voided = controller.post(f"{url}{declaration.pk}/void/", {}, format="json")
        self.assertEqual(voided.status_code, 200, voided.content)
        self.assertEqual(rows(), [])
        self.assertEqual(payable(), Decimal("-9496.20"))
        self.assertEqual(self.balance(self.bonus_expense), Decimal("9496.20"))
        self.assertEqual(self.row(bonus_statement(2025, Decimal("10")), a)[2:],
                         (Decimal("8400.00"), Decimal("6997.20"), Decimal("1402.80")))
        self.assertEqual(controller.post(f"{url}{declaration.pk}/void/", {}, format="json").status_code, 400)
        # And the year's runs can be corrected again, and the bonus declared again after.
        runs[-1].void()
        self.assertEqual(controller.post(url, {"year": 2025, "percent": "8.33"}, format="json").status_code, 201)

    def test_the_statement_opens_while_the_least_per_cent_waits_and_says_why(self):
        person = self.earner("BW")
        self.months(YEAR[:2], [person])
        StatutoryFigure.objects.filter(kind=FigureKind.BONUS_MINIMUM_PERCENT).update(confirmed_on=None,
                                                                                      confirmed_by=None)
        read = self.as_role("Payroll Officer").get("/api/hr/bonus-statement/", {"year": 2025})
        self.assertEqual(read.status_code, 200, read.content)
        self.assertIn("Nothing owed is shown: Bonus, the least per cent of wages of 8.33", read.json()["note"])
        row = read.json()["rows"][0]
        self.assertEqual((row["wages"], row["accrued"], row["owed"], row["difference"]),
                         ("14000.00", "1166.20", None, None))

    def test_a_rate_changed_inside_a_month_accrues_each_rate_for_its_days(self):
        cases = {1: (["700.00"], "7464.80", "935.20"), 16: (["278.87", "365.22"], "7408.89", "991.11"),
                 31: (["557.75", "30.43"], "7352.98", "1047.02")}
        people = {day: self.earner(f"R{day:02d}") for day in cases}
        self.months(YEAR[:8], list(people.values()))
        for day, person in people.items():
            old = EmployeeCompensation.objects.get(employee=person, component=self.bonus)
            old.effective_to = D(2025, 12, day) - datetime.timedelta(days=1)
            old.save()
            self.paid(person, self.bonus, "10", since=D(2025, 12, day))
        december = self.months(YEAR[8:], list(people.values()))[0]
        statement = bonus_statement(2025, Decimal("10"))
        for day, (lines, accrued, difference) in cases.items():
            with self.subTest(changed_on=day):
                slip = december.payslips.get(employee=people[day])
                found = list(slip.lines.filter(component=self.bonus).order_by("rate"))
                self.assertEqual([f"{line.amount}" for line in found], lines)
                # The month's wages counted once, whatever number of rates it was accrued at.
                self.assertEqual({line.kept_terms["bonus_wages"] for line in found}, {"7000.00"})
                self.assertEqual(self.row(statement, people[day]),
                                 (Decimal("261"), Decimal("84000.00"), Decimal("8400.00"), Decimal(accrued),
                                  Decimal(difference)))


class SetupGivesTheBonusTests(TestCase):
    def said(self, command):
        out = StringIO()
        call_command(command, stdout=out)
        return out.getvalue()

    def test_setup_seeds_the_bonus_and_gives_it_to_everyone_in_service_paid_its_wages(self):
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        from .models import Employee

        self.said("setup_indian_books")
        self.said("setup_payroll")
        bonus, paid = PayComponent.objects.get(code="BONUS"), PayComponent.objects.get(code="BONUS-PAID")
        self.assertEqual((bonus.kind, bonus.basis, bonus.ceiling_figure, bonus.expense_account.code,
                          bonus.liability_account.code, sorted(bonus.base_components.values_list("code", flat=True)),
                          sorted(bonus.coverage_components.values_list("code", flat=True))),
                         (ComponentKind.EMPLOYER_COST, ComponentBasis.PERCENT_OF_GROSS, BONUS_CEILING, "6110", "2450",
                          ["BASIC", "DA"], ["BASIC", "DA"]))
        self.assertEqual((paid.kind, paid.basis, paid.is_taxable, paid.expense_account.code),
                         (ComponentKind.EARNING, ComponentBasis.FIXED, False, "2450"))
        today = timezone.localdate()
        people = {}
        for code, left in (("S1", None), ("S2", None), ("GONE", today - datetime.timedelta(days=40))):
            party = Party.objects.create(code=f"P-{code}", name=code)
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
            people[code] = Employee.objects.create(party=party, employee_number=code, hire_date=D(2024, 1, 1))
            EmployeeCompensation.objects.create(employee=people[code], component=PayComponent.objects.get(code="BASIC"),
                                                amount=Decimal("12000"), effective_from=D(2024, 1, 1))
            if left:
                people[code].terminate(left)
        gaps = lambda: dict((engine, missing) for engine, used, missing in statutory_gaps(today))["Bonus"]  # noqa: E731
        self.assertIn("BONUS on the pay of S1, S2 (not given: python manage.py setup_payroll gives it)", gaps())
        # Before the law's figures are loaded there is no least per cent to give it at, and it says so.
        self.assertIn("given: BONUS is given to nobody yet: the bonus's least per cent is not loaded",
                      self.said("setup_payroll"))
        self.assertIn("given: BONUS at 8.33% given to 2: S1, S2", self.said("setup_statutory"))
        given = sorted(EmployeeCompensation.objects.filter(component=bonus).values_list(
            "employee__employee_number", "amount", "effective_from", "effective_to"))
        self.assertEqual(given, [("S1", Decimal("8.3300"), today.replace(day=1), None),
                                 ("S2", Decimal("8.3300"), today.replace(day=1), None)])
        self.assertFalse([line for line in gaps() if "not given" in line])
        # Run again, nobody is given it twice.
        self.assertNotIn("given:", self.said("setup_payroll"))
        self.assertNotIn("given:", self.said("setup_statutory"))
        self.assertEqual(EmployeeCompensation.objects.filter(component=bonus).count(), 2)
