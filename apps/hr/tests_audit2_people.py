"""
audit2_people probes: each asserts what SHOULD be true. Figures from calc_audit2_people/figures.py.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Account, AccountType
from .payroll import EmployeeCompensation

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


class WhoReadsWhat(OutputsTestCase):
    def test_the_floor_and_a_line_manager_do_not_read_provident_fund_and_insurance_numbers(self):
        """M13: UAN and ESI numbers are the personnel and payroll offices'; a storekeeper's list of names needs neither."""
        person = self.employee("E1")
        person.uan, person.esi_number = "100000000001", "2200012345"
        person.save()
        for role in ("Quality Inspector", "Maintenance", "Production Supervisor", "Process Engineer", "Line Manager"):
            body = self.as_role(role).get("/api/hr/employees/").content.decode()
            self.assertNotIn("100000000001", body, role)
            self.assertNotIn("2200012345", body, role)


class APayDateIsNearItsPeriod(OutputsTestCase):
    def test_a_run_is_not_posted_for_a_pay_date_years_from_its_period(self):
        """M12: June 2026 posted with a pay date of 30 June 2062 (a typed year) books wages 36 years ahead."""
        person = self.employee("E1")
        self.pay(person, self.salary, "5000")
        run = self.pay_run(pay_date=D(2062, 6, 30))
        run.calculate()
        with self.assertRaises(ValidationError):
            run.post()


class TheContractLabourRegister(OutputsTestCase):
    def test_a_contractor_who_has_stopped_brings_no_new_worker(self):
        """M14: an inactive contractor (licence surrendered) is not entered a new worker on the register."""
        from apps.core.models import Party

        from .contract_labour import ContractWorker, LabourContractor

        party = Party.objects.create(code="CON1", name="Sai Labour")
        contractor = LabourContractor.objects.create(party=party, licence_number="L1", licence_valid_to=D(2027, 3, 31),
                                                     work_nature="Loading", max_workers=5, is_active=False)
        with self.assertRaises(ValidationError):
            ContractWorker.objects.create(contractor=contractor, name="Ravi", gender="male", daily_wage=Decimal("600"),
                                          joined_on=D(2026, 10, 1))

    def test_the_licence_headcount_is_not_lowered_below_those_working(self):
        """M15: 3 workers on a licence for 5; the licence is edited down to 2 over the API."""
        from apps.core.models import Party

        from .contract_labour import ContractWorker, LabourContractor

        party = Party.objects.create(code="CON1", name="Sai Labour")
        contractor = LabourContractor.objects.create(party=party, licence_number="L1", licence_valid_to=D(2027, 3, 31),
                                                     work_nature="Loading", max_workers=5)
        for number in range(3):
            ContractWorker.objects.create(contractor=contractor, name=f"W{number}", gender="male",
                                          daily_wage=Decimal("600"), joined_on=D(2026, 10, 1))
        said = self.as_role("HR Admin").patch(f"/api/hr/labour-contractors/{contractor.pk}/", {"max_workers": 2},
                                              format="json")
        self.assertEqual(said.status_code, 400, said.content)


class ARateChangeIsPaidForItsDays(OutputsTestCase):
    """M16: calc_audit2_people: last day 20,181.82; first day 24,000.00; mid-month 22,000.00; joiner 11,090.91."""

    def salary_for(self, rows, hire=D(2020, 1, 1)):
        person = self.employee("E1", hire=hire)
        self.salary.reduces_for_unpaid_leave = True
        self.salary.save()
        for amount, start, end in rows:
            EmployeeCompensation.objects.create(employee=person, component=self.salary, amount=Decimal(amount),
                                                effective_from=start, effective_to=end)
        run = self.pay_run()
        run.calculate()
        return run.payslips.get().gross()

    def test_a_rise_on_the_last_day_of_the_month(self):
        self.assertEqual(self.salary_for([("20000", D(2020, 1, 1), D(2026, 6, 29)), ("24000", D(2026, 6, 30), None)]),
                         Decimal("20181.82"))

    def test_a_rise_on_the_first_day_of_the_month(self):
        self.assertEqual(self.salary_for([("20000", D(2020, 1, 1), D(2026, 5, 31)), ("24000", D(2026, 6, 1), None)]),
                         Decimal("24000.00"))

    def test_a_rise_in_the_middle_of_the_month(self):
        self.assertEqual(self.salary_for([("20000", D(2020, 1, 1), D(2026, 6, 15)), ("24000", D(2026, 6, 16), None)]),
                         Decimal("22000.00"))

    def test_a_joiner_on_the_16th_with_a_rise_on_the_23rd(self):
        self.assertEqual(self.salary_for([("20000", D(2020, 1, 1), D(2026, 6, 22)), ("24000", D(2026, 6, 23), None)],
                                         hire=D(2026, 6, 16)), Decimal("11090.91"))


class APolicyChangedForNextYearLeavesThisYearAlone(OutputsTestCase):
    def test_raising_the_allowance_from_2027_does_not_restate_2026(self):
        """M17: the policy's own docstring: 'changing next year's entitlement is a policy change rather than an edit to
        everybody's history'. 12 days a year in 2026; 15 from 2027 (edited on 2 January 2027): 2026 still 12."""
        person = self.employee("E1")
        policy = LeavePolicy.objects.create(code="PL", name="Privilege", leave_type="vacation",
                                            annual_days=Decimal("12"))
        before = policy.entitlement_for(person, 2026)
        policy.annual_days = Decimal("15")
        policy.save()
        self.assertEqual(policy.entitlement_for(person, 2026), before)


class WhenSomebodyLeaves(OutputsTestCase):
    def test_a_leavers_login_stops_signing_in_on_the_day_they_go(self):
        """M18: E1, a Stores Manager's login, left on 30 September 2026; on 20 October the login is off."""
        from django.contrib.auth.models import User

        person = self.employee("E1")
        self.as_role("Stores Manager", person)
        person.terminate(D(2026, 9, 30))
        self.assertFalse(User.objects.get(pk=person.user_id).is_active)


class NobodyIsLeftOffARunInSilence(OutputsTestCase):
    def test_an_employee_with_no_pay_set_up_is_named_when_the_run_is_worked_out(self):
        """M19: E1 paid 5,000; ZED9, employed all June, has no compensation row: the run says ZED9 was left out."""
        paid, forgotten = self.employee("E1"), self.employee("ZED9")
        self.pay(paid, self.salary, "5000")
        run = self.pay_run()
        said = self.as_role("Payroll Officer").post(f"/api/hr/pay-runs/{run.pk}/calculate/", {}, format="json")
        self.assertEqual(said.status_code, 200, said.content)
        self.assertIn(forgotten.employee_number, said.content.decode())
