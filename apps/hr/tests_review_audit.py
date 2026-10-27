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
            for t in ledger_log.product_tables(connection):
                c.execute(f"ALTER TABLE {t} DISABLE TRIGGER erp_ledger_change")
    try:
        yield
    finally:
        if connection.vendor == "postgresql":
            with connection.cursor() as c:
                for t in ledger_log.product_tables(connection):
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

    # Decided in g24: the Edit log and History show a record's values only to whoever may read that record in
    # place; the Auditor reads every model, read only; the Controller reads pay (it posts the runs).
    def test_p21_the_auditor_does_not_read_pay_through_the_log_she_may_not_read_in_place(self):
        auditor = self.as_("Auditor")
        self.assertEqual(auditor.get("/api/hr/compensation/").status_code, 200, "the Auditor reads pay")
        self.assertEqual(auditor.get("/api/core/history/", {"model": "hr.employeecompensation",
                                                            "id": self.row.pk}).status_code, 200)
        log = auditor.get("/api/core/edit-log/", {"model": "hr.employeecompensation", "from": "2000-01-01",
                                                  "to": "2099-12-31", "kind": "updated"}).json()
        self.assertEqual([change["to"] for row in log for change in row["changes"] if change["field"] == "amount"],
                         ["7000.0000"])  # the column keeps four places
        self.assertEqual(auditor.post("/api/hr/compensation/", {}, format="json").status_code, 403)
        # A login given the log and no right to pay: no pay change through it, and no pay History.
        reader = User.objects.create_user("log_reader")
        from django.contrib.auth.models import Permission
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_recordevent"))
        client = APIClient()
        client.force_authenticate(reader)
        self.assertEqual(client.get("/api/core/edit-log/", {"model": "hr.employeecompensation", "from": "2000-01-01",
                                                            "to": "2099-12-31"}).json(), [])
        self.assertEqual(client.get("/api/core/history/", {"model": "hr.employeecompensation",
                                                           "id": self.row.pk}).status_code, 403)

    def test_p22_roles_without_the_right_are_refused_and_the_controller_filters(self):
        from apps.core.management.commands.setup_roles import ROLES
        let_in = {role: code for role in ROLES if role not in ("Controller", "Auditor")
                  if (code := self.as_(role).get("/api/core/edit-log/").status_code) != 403}
        self.assertEqual(let_in, {}, "every role but the Controller and the Auditor is refused the edit log")
        controller = self.as_("Controller")
        response = controller.get("/api/core/edit-log/", {"model": "hr.employeecompensation", "kind": "updated",
                                                          "from": "2000-01-01", "to": "2099-12-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(controller.get("/api/core/edit-log/", {"model": "nope"}).status_code, 400)
        self.assertEqual(controller.get("/api/core/edit-log/", {"who": "x"}).status_code, 400)
        # The Controller holds hr.view_employeecompensation (setup_roles: it posts the pay runs), so it reads the
        # change in place and in the log alike.
        self.assertEqual(controller.get("/api/core/history/", {"model": "hr.employeecompensation",
                                                               "id": self.row.pk}).status_code, 200)
        self.assertEqual(len(response.json()), 1)
        # A model the Controller may not read in place: none of its lines through the log either.
        from apps.core.history import RecordEvent
        who = User.objects.get(username="Controller")
        unread = {f"{row.content_type.app_label}.{row.content_type.model}" for row in RecordEvent.objects.all()
                  if not who.has_perm(f"{row.content_type.app_label}.view_{row.content_type.model}")}
        self.assertTrue(unread, "a line of a kind the Controller may not read, for the filter to leave out")
        shown = {row["model"] for row in controller.get("/api/core/edit-log/").json()}
        self.assertFalse(set(unread) & shown)


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
