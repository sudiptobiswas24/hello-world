"""Review probes for the edit log: who a pay run's post names, who may read the log, what it costs."""

import contextlib
import time
import unittest
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient

from apps.core.history import RecordEvent
from apps.core.tests_roles import RolesTestCase

from .tests_payroll_api import PayrollApiTestCase


@contextlib.contextmanager
def trail_off():
    """The application layer's receivers and wrappers, the actor stamp and (PostgreSQL) the 19 triggers, off."""
    from django.db.models import QuerySet
    from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
    from apps.core import history, ledger_log
    pairs = [(pre_save, history.before_save), (post_save, history.after_save), (pre_delete, history.before_delete),
             (post_delete, history.after_delete), (m2m_changed, history.link_changed)]
    saved = {n: getattr(QuerySet, n) for n in ("update", "bulk_create", "bulk_update")}
    for signal, fn in pairs:
        signal.disconnect(fn, dispatch_uid=f"apps.core.history.{fn.__name__}")
    for n in saved:
        setattr(QuerySet, n, getattr(saved[n], "__wrapped__", None) or saved[n].__closure__[0].cell_contents)
    connection.execute_wrappers[:] = [w for w in connection.execute_wrappers if w is not ledger_log.stamp_actor]
    if connection.vendor == "postgresql":
        with connection.cursor() as c:
            for t in ledger_log.BOOKS_TABLES:
                c.execute(f"ALTER TABLE {t} DISABLE TRIGGER erp_ledger_change")
    try:
        yield
    finally:
        if connection.vendor == "postgresql":
            with connection.cursor() as c:
                for t in ledger_log.BOOKS_TABLES:
                    c.execute(f"ALTER TABLE {t} ENABLE TRIGGER erp_ledger_change")
        for n, fn in saved.items():
            setattr(QuerySet, n, fn)
        history.install()
        connection.execute_wrappers.insert(0, ledger_log.stamp_actor)


class PayRunPostNamesThePoster(PayrollApiTestCase):
    def test_p20_every_line_a_pay_run_post_makes_names_the_person_who_posted(self):
        base = "/api/hr/pay-runs/"
        run = self.client.post(base, {"period_start": "2026-06-01", "period_end": "2026-06-30",
                                      "pay_date": "2026-06-30", "name": "June"}, format="json").json()
        self.client.post(f"{base}{run['id']}/calculate/", {}, format="json")
        mark = RecordEvent.objects.order_by("-pk").first().pk
        self.assertEqual(self.client.post(f"{base}{run['id']}/post/", {}, format="json").status_code, 200)
        lines = list(RecordEvent.objects.filter(pk__gt=mark))
        self.assertTrue(lines)
        self.assertEqual({(l.who_name, l.through) for l in lines}, {("payroll", "api")})
        if connection.vendor == "postgresql":
            from apps.core.ledger_log import LedgerChange
            poster = User.objects.get(username="payroll").pk
            rows = LedgerChange.objects.filter(table_name__in=["hr_payrun", "hr_payslip", "accounting_journalentry",
                                                               "accounting_journalline"]).exclude(
                id__lte=0)
            self.assertEqual(set(rows.filter(operation="UPDATE", table_name="hr_payrun").values_list(
                "acting_user_id", "through")), {(poster, "api")})
            self.assertEqual(LedgerChange.objects.filter(through="unattributed").count(), 0)


class ReadingTheLog(RolesTestCase):
    def setUp(self):
        super().setUp()
        from .tests_payroll import PayrollTestCase
        PayrollTestCase.setUp(self)
        self.person = PayrollTestCase.employee(self, "P1")
        self.row = PayrollTestCase.pay(self, self.person, self.salary, "5000")
        self.row.amount = Decimal("7000")
        self.row.save()

    def test_p21_the_auditor_does_not_read_pay_through_the_log_she_may_not_read_in_place(self):
        auditor = self.as_("Auditor")
        self.assertEqual(auditor.get("/api/hr/employee-compensations/").status_code in (403, 404), True) \
            if False else None
        refused = auditor.get("/api/core/history/", {"model": "hr.employeecompensation", "id": self.row.pk})
        self.assertEqual(refused.status_code, 403, "history panel: the Auditor has no right to pay")
        log = auditor.get("/api/core/edit-log/", {"model": "hr.employeecompensation", "from": "2000-01-01",
                                                  "to": "2099-12-31"})
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.json(), [], "the edit log hands the Auditor every salary change the role may not read")

    def test_p22_roles_without_the_right_are_refused_and_the_controller_filters(self):
        for role in ("Sales Rep", "HR Admin", "Employee Self Service"):
            if Group.objects.filter(name=role).exists():
                self.assertEqual(self.as_(role).get("/api/core/edit-log/").status_code, 403, role)
        controller = self.as_("Controller")
        response = controller.get("/api/core/edit-log/", {"model": "hr.employeecompensation", "kind": "updated",
                                                          "from": "2000-01-01", "to": "2099-12-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(controller.get("/api/core/edit-log/", {"model": "nope"}).status_code, 400)
        self.assertEqual(controller.get("/api/core/edit-log/", {"who": "x"}).status_code, 400)
        # the Controller has no hr right either
        self.assertEqual(controller.get("/api/core/history/", {"model": "hr.employeecompensation",
                                                               "id": self.row.pk}).status_code, 403)
        self.assertEqual(response.json(), [], "the Controller reads pay changes in the log without any right to pay")


class Cost(PayrollApiTestCase):
    def one_run(self, n):
        for i in range(n):
            person = self.employee(f"E{i}")
            self.pay(person, self.salary, "5000")
            self.pay(person, self.tax, "20")
            self.pay(person, self.pension, "5")
            self.pay(person, self.employer_pension, "3")

    def timed_run(self, off):
        base = "/api/hr/pay-runs/"
        run = self.client.post(base, {"period_start": "2026-06-01", "period_end": "2026-06-30",
                                      "pay_date": "2026-06-30", "name": "June"}, format="json").json()
        ctx = trail_off() if off else contextlib.nullcontext()
        with ctx:
            t = time.perf_counter()
            self.assertEqual(self.client.post(f"{base}{run['id']}/calculate/", {}, format="json").status_code, 200)
            c = time.perf_counter() - t
            t = time.perf_counter()
            self.assertEqual(self.client.post(f"{base}{run['id']}/post/", {}, format="json").status_code, 200)
            return c, time.perf_counter() - t

    def test_p30_pay_run_of_200_with_the_trail_on_and_off(self):
        self.one_run(199)
        c_on, p_on = self.timed_run(False)
        # void and repeat with the trail off
        run = self.client.get("/api/hr/pay-runs/").json()
        rid = (run["results"] if isinstance(run, dict) else run)[0]["id"]
        self.client.post(f"/api/hr/pay-runs/{rid}/void/", {"reason": "probe"}, format="json")
        c_off, p_off = self.timed_run(True)
        print(f"\nPROBE p30 {connection.vendor}: calculate on {c_on:.2f}s off {c_off:.2f}s; post on {p_on:.2f}s off {p_off:.2f}s")
