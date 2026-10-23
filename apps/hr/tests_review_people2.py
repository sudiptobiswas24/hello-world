"""Review probes for fix_people2 (read-only review)."""
import datetime
from decimal import Decimal

from .payroll import ComponentKind, PayComponent, PayRun, Statutory
from .statutory_files import ecr_rows, esi_rows
from .tests_statutory_files import FilesTestCase
from .models import Employee

F1 = (datetime.date(2026, 6, 1), datetime.date(2026, 6, 15))
F2 = (datetime.date(2026, 6, 16), datetime.date(2026, 6, 30))
T1 = (datetime.date(2026, 6, 1), datetime.date(2026, 6, 10))
T2 = (datetime.date(2026, 6, 11), datetime.date(2026, 6, 20))
T3 = (datetime.date(2026, 6, 21), datetime.date(2026, 6, 30))


def show(label, *a):
    print("PROBE", label, *a)


class Probes(FilesTestCase):
    def solo(self, code, **pay):
        p = self.person(code, **pay)
        Employee.objects.filter(pk=p.pk).update(uan="1000000000" + code[-2:].rjust(2, "0"), esi_number="22000000" + code[-2:].rjust(2, "0"))
        from .pay_details import EmployeePayDetails
        EmployeePayDetails.objects.create(employee=p, date_of_birth=datetime.date(1990, 1, 1))
        return Employee.objects.get(pk=p.pk)

    def only(self, who):
        # runs for one person only
        def run(period, post=True):
            r = PayRun.objects.create(period_start=period[0], period_end=period[1], pay_date=period[1])
            r.calculate(employees=[who])
            if post:
                r.post()
            return r
        return run

    def test_eps_split_two_runs(self):
        self.pf_er.rate = None
        eps = PayComponent.objects.create(code="EPS", name="EPS", kind=ComponentKind.EMPLOYER_COST,
                                          basis=self.pf_er.basis, liability_account=self.pf_payable, sequence=65,
                                          expense_account=self.employer, base_ceiling=Decimal("15000"),
                                          rounding=self.pf_er.rounding, statutory=Statutory.EPS, remit_by_day=15)
        eps.base_components.set(self.pf_er.base_components.all())
        p = self.solo("Q1", basic="8000")
        self.paid(p, eps, "8.33")
        from .payroll import EmployeeCompensation
        EmployeeCompensation.objects.filter(employee=p, component=self.pf_er).update(amount=Decimal("3.67"))
        run = self.only(p)
        r1, r2 = run(F1), run(F2)
        for r in (r1, r2):
            show("eps_split", r.number, {k: str(v) for k, v in self.lines(r, p).items()})
        show("ecr", ecr_rows(r2))

    def test_three_runs(self):
        p = self.solo("Q2", basic="6000")
        run = self.only(p)
        rs = [run(T1), run(T2), run(T3)]
        for r in rs:
            show("three", r.period_end, {k: str(v) for k, v in self.lines(r, p).items()})
        show("ecr3", [r for r in ecr_rows(rs[2]) if r["name"] == "Q2"])
        show("esi3", [r for r in esi_rows(rs[2]) if r["name"] == "Q2"])
        rs[1].void(on_date=T2[1])
        r2 = run(T2)
        show("redo mid", {k: str(v) for k, v in self.lines(r2, p).items()})
        rs[0].void(on_date=T1[1])
        r1 = run(T1)
        show("redo first", {k: str(v) for k, v in self.lines(r1, p).items()})
        show("ecr after", [r for r in ecr_rows(r1) if r["name"] == "Q2"])
        tot = sum(self.lines(r, p)["PF"] for r in (r1, r2, rs[2]))
        show("PF total", tot)


from django.urls import get_resolver, URLPattern, URLResolver

from .contract_labour import ContractWorker, LabourContractor
from .tests_pay_outputs import OutputsTestCase
from apps.core.models import Party


def flat(patterns, prefix=""):
    for p in patterns:
        s = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from flat(p.url_patterns, s)
        else:
            yield s


class Probes2(OutputsTestCase):
    def test_manager_hijack_and_contractor_move(self):
        mgr = self.employee("M1")
        e1 = self.employee("E1", manager=mgr)
        ess = self.as_role("Employee Self Service", e1)
        made = ess.post("/api/hr/expense-claims/", {"purpose": "Taxi"}, format="json")
        cid = made.json()["id"]
        lm = self.as_role("Line Manager", mgr)
        r = lm.get(f"/api/hr/expense-claims/{cid}/")
        show("mgr sees claim", r.status_code)
        r = lm.patch(f"/api/hr/expense-claims/{cid}/", {"employee": mgr.pk}, format="json")
        show("mgr hijack patch", r.status_code, r.content[:200])
        from .expenses import ExpenseClaim
        show("claim employee now", ExpenseClaim.objects.get(pk=cid).employee.employee_number)
        # contractor
        c1 = LabourContractor.objects.create(party=Party.objects.create(code="C1", name="A"), licence_number="L1",
            licence_valid_to=datetime.date(2027, 3, 31), work_nature="Loading", max_workers=5)
        c2 = LabourContractor.objects.create(party=Party.objects.create(code="C2", name="B"), licence_number="L2",
            licence_valid_to=datetime.date(2027, 3, 31), work_nature="Loading", max_workers=5, is_active=False)
        w = ContractWorker.objects.create(contractor=c1, name="Ravi", gender="male", daily_wage=Decimal("600"), joined_on=datetime.date(2026, 10, 1))
        try:
            w.contractor = c2
            w.save()
            show("worker moved to inactive contractor: ACCEPTED")
        except Exception as e:
            show("worker move refused", e)

    def test_uan_crawl(self):
        mgr = self.employee("M1")
        e1 = self.employee("E1", manager=mgr)
        e1.uan, e1.esi_number = "123456789012", "5544332211"
        e1.save()
        self.banked(e1)
        run = self.pay_run()
        self.pay(e1, self.salary, "5000")
        run.calculate(); run.post()
        urls = sorted({u for u in flat(get_resolver().url_patterns) if u.startswith("api/") and "<" not in u and "(?P" not in u and "$" not in u.replace("\\Z","")} )
        urls = [("/" + u.rstrip("$").replace("^", "")) for u in flat(get_resolver().url_patterns)
                if u.startswith("api/") and "(" not in u and "<" not in u]
        urls = sorted(set(urls))
        show("urls", len(urls))
        extra = [f"/api/hr/employees/{e1.pk}/", f"/api/hr/employees/{e1.pk}/leave/", f"/api/hr/payslips/{run.payslips.first().pk}/",
                 "/api/hr/employees/?missing=uan", f"/api/hr/payruns/{run.pk}/"]
        for role, emp in [("Production Supervisor", None), ("Quality Inspector", None), ("Line Manager", mgr),
                          ("Employee Self Service", self.employee("S1")), ("Controller", None), ("Maintenance", None)]:
            c = self.as_role(role, emp)
            hits = []
            for u in urls + extra:
                try:
                    r = c.get(u)
                except Exception as e:
                    continue
                body = r.content.decode(errors="ignore")
                if r.status_code == 200 and ("123456789012" in body or "5544332211" in body):
                    hits.append(u)
            show("UANHITS", role, hits)


from unittest import mock
from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Policy(OutputsTestCase):
    def test_policy_years(self):
        from .models import LeavePolicy, LeaveEntitlement
        pol = LeavePolicy.objects.create(code="CL", name="CL", annual_days=Decimal("12"), carry_over_limit=Decimal("5"))
        def at(d):
            return mock.patch("apps.hr.models.timezone.localdate", return_value=d)
        with at(datetime.date(2026, 10, 23)):
            pol.annual_days = Decimal("15"); pol.save()
        show("after 2026 change", [(y, str(LeavePolicy.objects.get(pk=pol.pk).annual_days_for(y))) for y in (2024, 2025, 2026, 2027)])
        with at(datetime.date(2027, 3, 1)):
            p = LeavePolicy.objects.get(pk=pol.pk); p.annual_days = Decimal("18"); p.save()
        p = LeavePolicy.objects.get(pk=pol.pk)
        show("after 2027 change", [(y, str(p.annual_days_for(y))) for y in (2024, 2025, 2026, 2027, 2028)])
        # change back and forth within 2027
        with at(datetime.date(2027, 4, 1)):
            p.annual_days = Decimal("12"); p.save()
        p = LeavePolicy.objects.get(pk=pol.pk)
        show("after 2027 second change", [(y, str(p.annual_days_for(y))) for y in (2025, 2026, 2027)], list(p.years.values_list("year", "annual_days")))
        # a change in Jan 2029 after a gap year
        with at(datetime.date(2029, 1, 5)):
            p.annual_days = Decimal("20"); p.save()
        p = LeavePolicy.objects.get(pk=pol.pk)
        show("after 2029", [(y, str(p.annual_days_for(y))) for y in (2026, 2027, 2028, 2029)])
        # carry over
        e = self.employee("E9")
        for c in ("5", "5.01", "0"):
            try:
                LeaveEntitlement.objects.update_or_create(employee=e, policy=p, year=2026, defaults={"days": Decimal("12"), "carried_over": Decimal(c)})
                show("carry", c, "ok", str(p.entitlement_for(e, 2026, as_of=datetime.date(2026, 12, 31))))
            except Exception as ex:
                show("carry", c, "refused", ex)
        # lower the limit then edit the days of existing row
        p.carry_over_limit = Decimal("2"); p.save()
        le = LeaveEntitlement.objects.get(employee=e, policy=p, year=2026)
        le.carried_over = Decimal("5"); 
        le.days = Decimal("13")
        try:
            le.save(); show("resave over lowered limit ok")
        except Exception as ex:
            show("resave over lowered limit refused", ex)


class Mig(TransactionTestCase):
    def test_roundtrip(self):
        ex = MigrationExecutor(connection)
        ex.migrate([("hr", "0029_leaver_login_switch")])
        ex = MigrationExecutor(connection)
        ex.migrate([("hr", "0030_leave_policy_years")])
        from django.db import connection as c
        show("tables", "hr_leavepolicyyear" in c.introspection.table_names())
        ex = MigrationExecutor(connection)
        ex.migrate([("hr", "0029_leaver_login_switch")])
        show("rolled back", "hr_leavepolicyyear" not in c.introspection.table_names())
        ex = MigrationExecutor(connection)
        ex.migrate(ex.loader.graph.leaf_nodes())


class Foot(FilesTestCase):
    def test_files_foot(self):
        from .pay_details import EmployeePayDetails
        j = self.person("J", basic="9000", hra="3000")
        l = self.person("L", basic="14000", hra="2000")
        k = self.person("K", basic="11000", hra="1500")   # crosses ESI limit? 12500 per fortnight
        for n, p in enumerate((j, l, k)):
            Employee.objects.filter(pk=p.pk).update(uan=f"20000000000{n}", esi_number=f"330000000{n}")
            EmployeePayDetails.objects.create(employee=p, date_of_birth=datetime.date(1990, 1, 1))
        Employee.objects.filter(pk=j.pk).update(hire_date=datetime.date(2026, 6, 10))
        Employee.objects.filter(pk=l.pk).update(termination_date=datetime.date(2026, 6, 20), employment_status="terminated")
        r1, r2 = self.run_for(*F1), self.run_for(*F2)
        pf = sum(self.lines(r, e).get("PF", 0) + self.lines(r, e).get("PF-ER", 0) for r in (r1, r2) for e in Employee.objects.all() if r.payslips.filter(employee=e).exists())
        show("ledger pf payable", -self.balance(self.pf_payable), "lines", pf)
        rows = ecr_rows(r2)
        show("ecr total", sum(r["epf"] + r["eps"] + r["epf_diff"] for r in rows), [(r["name"], r["epf_wages"], r["epf"], r["eps"], r["epf_diff"], r["ncp_days"]) for r in rows])
        esi = -self.balance(self.esi_payable)
        erows = esi_rows(r2)
        show("ledger esi", esi, "file wages", [(r["name"], r["days"], r["wages"]) for r in erows])
        for r in (r1, r2):
            for s in r.payslips.all():
                show("slip", r.period_end, s.employee.employee_number, {k: str(v) for k, v in self.lines(r, s.employee).items() if k.startswith(("PF", "ESI"))})
        # is the file for r1 equal to the file for r2?
        show("same file either run", ecr_rows(r1) == ecr_rows(r2), esi_rows(r1) == esi_rows(r2))


class Zero(FilesTestCase):
    def test_zero_second_run(self):
        from .pay_details import EmployeePayDetails
        p = self.person("Z", basic="15000")
        Employee.objects.filter(pk=p.pk).update(uan="300000000001", esi_number="4400000001")
        EmployeePayDetails.objects.create(employee=p, date_of_birth=datetime.date(1990, 1, 1))
        p = Employee.objects.get(pk=p.pk)
        r1 = PayRun.objects.create(period_start=F1[0], period_end=F1[1], pay_date=F1[1]); r1.calculate(employees=[p]); r1.post()
        from .payroll import EmployeeCompensation
        EmployeeCompensation.objects.filter(employee=p, component=self.basic).update(effective_to=datetime.date(2026, 6, 15))
        self.paid(p, self.basic, "4000", since=datetime.date(2026, 6, 16))
        r2 = PayRun.objects.create(period_start=F2[0], period_end=F2[1], pay_date=F2[1]); r2.calculate(employees=[p]); r2.post()
        for r in (r1, r2):
            s = r.payslips.get(employee=p)
            show("slip", r.period_end, str(s.gross()), {k: str(v) for k, v in self.lines(r, p).items()}, s.kept_days())
        show("ecr", [x for x in ecr_rows(r2) if x["name"] == "Z"])
        show("esi", [x for x in esi_rows(r2) if x["name"] == "Z"])


class Hist(OutputsTestCase):
    def test_history_leak(self):
        e1 = self.employee("E1")
        hr = self.as_role("HR Admin")
        r = hr.patch(f"/api/hr/employees/{e1.pk}/", {"uan": "123456789012", "esi_number": "5544332211"}, format="json")
        show("hr patch", r.status_code)
        for role in ("Production Supervisor", "Quality Inspector", "Line Manager"):
            c = self.as_role(role)
            h = c.get(f"/api/core/history/?model=hr.employee&id={e1.pk}")
            body = h.content.decode()
            show("HISTBODY", body[:400])
            show("HISTORY", role, h.status_code, "123456789012" in body, "5544332211" in body)
            h = c.get(f"/api/hr/employees/?search=1234")
            s = c.get(f"/api/hr/employees/?missing=uan")
            show("missing oracle", role, s.status_code, len(s.json()) if s.status_code == 200 else None)


class PayDate(OutputsTestCase):
    def test_edges(self):
        from django.utils import timezone
        from django.core.exceptions import ValidationError
        person = self.employee("E1"); self.pay(person, self.salary, "5000")
        today = timezone.localdate()
        for delta in (10, 11):
            run = self.pay_run(datetime.date(2026, 6, 1), datetime.date(2026, 6, 30), pay_date=today + datetime.timedelta(days=delta))
            run.calculate()
            try:
                run.post(); show("june run (old) pay date today+%d" % delta, "POSTED, entry dated", run.pay_date)
                run.void(on_date=run.pay_date) if False else None
            except ValidationError as e:
                show("june run pay date today+%d refused" % delta, e)
            run.refresh_from_db()
            if run.status == "posted":
                run.void()
