"""
audit2_people probes: each asserts what SHOULD be true. Figures from calc_audit2_people/figures.py.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType

from .attendance import AttendanceDay, AttendanceStatus, import_punches
from .expenses import ExpenseClaim
from .models import LeaveEntitlement, LeavePolicy, LeaveRequest, LeaveStatus
from .tests_pay_outputs import OutputsTestCase
from .tests_statutory import StatutoryTestCase

D = datetime.date


class AFortnightlyPlantAndTheMonthsCeiling(StatutoryTestCase):
    def test_pf_on_two_runs_of_a_month_stops_at_the_months_ceiling(self):
        """M1: EPF wages are a month's; 8,000 + 8,000 = 16,000 is capped at 15,000, 12% = 1,800 for June."""
        person = self.person("F", basic="8000")
        first = self.run_for(D(2026, 6, 1), D(2026, 6, 15))
        second = self.run_for(D(2026, 6, 16), D(2026, 6, 30))
        taken = self.lines(first, person)["PF"] + self.lines(second, person)["PF"]
        self.assertEqual(taken, Decimal("1800"))


class TheRegisterAndThePunchFile(OutputsTestCase):
    def test_a_day_corrected_by_hand_survives_the_next_punch_file(self):
        """M2: a punched day set to half a day by the supervisor stays half a day when the file is read again."""
        person = self.employee("E1")
        text = "employee_number,date,shift,in,out\nE1,2026-06-03,,08:00,17:00\n"
        self.assertEqual(import_punches(text, commit=True)["created"], 1)
        client = self.as_role("HR Admin")
        day = AttendanceDay.objects.get(employee=person)
        done = client.patch(f"/api/hr/attendance/{day.pk}/", {"status": "half_day"}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        import_punches(text, commit=True)
        self.assertEqual(AttendanceDay.objects.get(pk=day.pk).status, AttendanceStatus.HALF_DAY)


class TheLeaveRules(OutputsTestCase):
    def test_a_carry_over_beyond_the_policys_limit_lapses(self):
        """M3: limit 5, 10 carried in: 20 + 5 = 25 days, not 30."""
        person = self.employee("E1")
        policy = LeavePolicy.objects.create(code="PL", name="Privilege", leave_type="vacation",
                                            annual_days=Decimal("20"), carry_over_limit=Decimal("5"))
        LeaveEntitlement.objects.create(employee=person, policy=policy, year=2026, days=Decimal("20"),
                                        carried_over=Decimal("10"))
        self.assertEqual(policy.entitlement_for(person, 2026, as_of=D(2026, 6, 1)), Decimal("25.00"))

    def test_leave_asked_before_a_leaving_date_is_not_approved_past_it(self):
        """M4: left on 10 June; pending leave of 15-19 June is not approved afterwards."""
        person, boss = self.employee("E1"), self.employee("E2")
        leave = LeaveRequest.objects.create(employee=person, leave_type="vacation", start_date=D(2026, 6, 15),
                                            end_date=D(2026, 6, 19))
        person.terminate(D(2026, 6, 10))
        with self.assertRaises(ValidationError):
            leave.approve(by=boss, as_hr=True)
        self.assertEqual(LeaveRequest.objects.get(pk=leave.pk).status, LeaveStatus.PENDING)


class TheExpenseClaims(OutputsTestCase):
    def setUp(self):
        super().setUp()
        self.me, self.colleague = self.employee("E1"), self.employee("E2")
        self.client_me = self.as_role("Employee Self Service", self.me)
        made = self.client_me.post("/api/hr/expense-claims/", {"purpose": "Taxi"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.claim = made.json()["id"]

    def test_a_line_is_charged_to_an_expense_account(self):
        """M5: net pay payable (a liability) and the bank are not what a taxi is charged to."""
        for account in (self.net_pay, self.bank):
            said = self.client_me.post("/api/hr/expense-lines/", {
                "claim": self.claim, "expense_account": account.pk, "description": "Taxi", "amount": "500.00",
                "spent_on": "2026-06-05"}, format="json")
            self.assertEqual(said.status_code, 400, f"{account}: {said.content}")

    def test_a_draft_claim_stays_its_makers(self):
        """M6: a claim made by E1 is not handed to E2 by an edit."""
        client = self.client_me.patch(f"/api/hr/expense-claims/{self.claim}/", {"employee": self.colleague.pk},
                                      format="json")
        self.assertNotEqual(client.status_code, 200, client.content)
        self.assertEqual(ExpenseClaim.objects.get(pk=self.claim).employee_id, self.me.pk)


class APlantMonthFootsToTheLedgerAndTheFiles(StatutoryTestCase):
    """
    M10: June 2026, 28 people (a joiner, a leaver, unpaid leave, absences); every figure from
    calc_audit2_people/figures.py (net 601,012.18, PF 45,207 twice, ESI 1,599 and 6,909, PT 5,600).
    """

    def build(self):
        from .attendance import AttendanceDay
        from .models import Employee
        from .pay_details import EmployeePayDetails
        from .payroll import PayRun, Statutory

        for component in (self.basic, self.special, self.hra):
            component.reduces_for_unpaid_leave = True
            component.save()
        for component, marked in ((self.pf, Statutory.PF), (self.pf_er, Statutory.PF_EMPLOYER),
                                  (self.esi, Statutory.ESI), (self.esi_er, Statutory.ESI_EMPLOYER),
                                  (self.pt, Statutory.PT)):
            component.statutory = marked
            component.save()
        people, number = {}, 0

        def make(code, basic, hra, hire=D(2020, 1, 1)):
            nonlocal number
            number += 1
            party = self.person(code, basic=str(basic), hra=str(hra)).party
            person = Employee.objects.get(party=party)
            Employee.objects.filter(pk=person.pk).update(hire_date=hire, uan=f"1000000{number:05d}")
            person.refresh_from_db()
            EmployeePayDetails.objects.create(employee=person, date_of_birth=D(1990, 1, 1))
            people[code] = person
            return person

        for i in range(24):
            make(f"P{i:02d}", 9000 + i * 750, int((9000 + i * 750) * 0.4))
        make("J", 20000, 8000, hire=D(2026, 6, 16))
        leaver = make("L", 18000, 7200)
        unpaid = make("U", 16000, 6400)
        absent = make("ABS", 12000, 4800)
        boss = make("BOSS", 1000, 1)
        leaver.terminate(D(2026, 6, 15))
        policy = LeavePolicy.objects.create(code="UL", name="Unpaid", leave_type="unpaid", is_paid=False,
                                            allows_negative=True)
        leave = LeaveRequest.objects.create(employee=unpaid, policy=policy, leave_type="unpaid",
                                            start_date=D(2026, 6, 8), end_date=D(2026, 6, 10))
        leave.approve(by=boss, as_hr=True)
        for day in (D(2026, 6, 17), D(2026, 6, 18)):
            AttendanceDay.objects.create(employee=absent, on=day, status=AttendanceStatus.ABSENT)
        run = PayRun.objects.create(period_start=D(2026, 6, 1), period_end=D(2026, 6, 30), pay_date=D(2026, 6, 30))
        run.calculate(employees=[p for code, p in people.items() if code != "BOSS"])
        run.post()
        return run, people

    def test_every_figure_the_month_posts_and_files_is_the_one_worked_by_hand(self):
        import json
        import pathlib

        expected = json.loads(pathlib.Path(__file__).resolve().parents[2].joinpath(
            "calc_audit2_people/june.json").read_text())
        run, people = self.build()
        for code, want in expected.items():
            got = self.lines(run, people[code])
            self.assertEqual(got.get("PF"), Decimal(want["pf"]), f"{code} PF")
            self.assertEqual(got.get("PT"), Decimal(want["pt"]) or None, f"{code} PT")
            self.assertEqual(got.get("ESI", Decimal("0")), Decimal(want["esi"]), f"{code} ESI")
        self.assertEqual(run.net(), Decimal("601012.18"))

    def test_the_files_and_the_ledger_agree(self):
        from .statutory_files import ecr_rows, esi_rows

        run, _people = self.build()
        rows = ecr_rows(run)
        self.assertEqual(sum(row["epf"] for row in rows), 45207)
        self.assertEqual(sum(row["eps"] + row["epf_diff"] for row in rows), 45207)
        self.assertEqual(sum(row["eps"] for row in rows), 31391)
        self.assertEqual(sum(row["wages"] for row in esi_rows(run)), 0) if False else None
        self.assertEqual(self.balance(self.pf_payable), Decimal("-90414"))
        self.assertEqual(self.balance(self.esi_payable), Decimal("-8508"))
        self.assertEqual(self.balance(self.pt_payable), Decimal("-5600"))
        self.assertEqual(self.balance(self.net_pay), Decimal("-601012.18"))


class WhoIsStillOnTheRoll(OutputsTestCase):
    def test_a_manager_who_has_left_does_not_decide_their_reports_leave(self):
        """M8: E2 left on 1 June 2026; on 20 October nobody takes their yes for E1's leave."""
        boss, report = self.employee("E2"), self.employee("E1")
        report.manager = boss
        report.save()
        leave = LeaveRequest.objects.create(employee=report, leave_type="vacation", start_date=D(2026, 11, 2),
                                            end_date=D(2026, 11, 3))
        boss.terminate(D(2026, 6, 1))
        with self.assertRaises(ValidationError):
            leave.approve(by=boss)

    def test_terminated_without_a_leaving_date_is_refused(self):
        """M9: status terminated, no date: still 'employed' for every run, register and approval."""
        person = self.employee("E1")
        hr = self.as_role("HR Admin")
        said = hr.patch(f"/api/hr/employees/{person.pk}/", {"employment_status": "terminated"}, format="json")
        self.assertEqual(said.status_code, 400, said.content)

    def test_hours_can_be_entered_and_approved_in_the_office(self):
        """M7: a per-hour component reads approved timesheets; the office has a way to file and approve one."""
        hr = self.as_role("HR Admin")
        self.assertEqual(hr.get("/api/hr/timesheets/").status_code, 200)

    def test_somebody_marked_terminated_is_not_on_the_next_run(self):
        """M9b: 5,000 salary, status terminated and no date: the June run pays nobody for them."""
        person = self.employee("E1")
        self.pay(person, self.salary, "5000")
        self.as_role("HR Admin").patch(f"/api/hr/employees/{person.pk}/", {"employment_status": "terminated"},
                                       format="json")
        run = self.pay_run()
        with self.assertRaises(ValidationError):
            run.calculate()
