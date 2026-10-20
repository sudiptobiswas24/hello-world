"""Review probes for O300 (review_blockers). Not product tests."""
import datetime

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Employee, EmploymentStatus, LeaveRequest
from .tests_pay_outputs import OutputsTestCase

PW = "a-long-pass-phrase-41"


class Probes(OutputsTestCase):
    def person(self, code, role="Stores Manager"):
        p = self.employee(code)
        self.as_role(role, p)
        p.user.set_password(PW)
        p.user.save()
        return p

    def test_admin_disabled_login_then_terminated_then_withdrawn(self):
        p = self.person("E1")
        p.user.is_active = False
        p.user.save()
        p.terminate(datetime.date(2026, 9, 30))
        p.termination_date, p.employment_status = None, EmploymentStatus.ACTIVE
        p.save()
        print("ADMIN-DISABLED then withdraw -> is_active:", User.objects.get(pk=p.user_id).is_active)

    def test_leaving_day_and_after_session_and_basic(self):
        today = timezone.localdate()
        p = self.person("E1")
        Employee.objects.filter(pk=p.pk).update(termination_date=today)
        c = APIClient()
        print("leaving day login:", c.login(username=p.user.username, password=PW), c.get("/api/inventory/items/").status_code)
        Employee.objects.filter(pk=p.pk).update(termination_date=today - datetime.timedelta(days=1))
        print("day after, open session:", c.get("/api/inventory/items/").status_code)
        print("day after, new login:", APIClient().login(username=p.user.username, password=PW))
        import base64
        b = APIClient()
        b.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(f"{p.user.username}:{PW}".encode()).decode())
        print("day after, basic:", b.get("/api/inventory/items/").status_code)
        # date moved to the future again, no save: session returns
        Employee.objects.filter(pk=p.pk).update(termination_date=None)
        print("withdrawn (update) session:", c.get("/api/inventory/items/").status_code)

    def test_forced_auth_boss_approves_after_leaving(self):
        today = timezone.localdate()
        boss, rep = self.person("B1"), self.employee("R1")
        rep.manager = boss
        rep.save()
        leave = LeaveRequest.objects.create(employee=rep, leave_type="vacation",
                                            start_date=datetime.date(2026, 12, 2), end_date=datetime.date(2026, 12, 3))
        Employee.objects.filter(pk=boss.pk).update(termination_date=today - datetime.timedelta(days=1))
        c = APIClient()
        c.force_authenticate(User.objects.get(pk=boss.user_id))
        r = c.post(f"/api/hr/leave-requests/{leave.pk}/approve/", {}, format="json")
        print("leave approve by leaver (forced auth):", r.status_code, r.content[:160])
        Employee.objects.filter(pk=boss.pk).update(termination_date=today)
        r = c.post(f"/api/hr/leave-requests/{leave.pk}/approve/", {}, format="json")
        print("leave approve on leaving day:", r.status_code, r.content[:120])

    def test_api_key_of_leaver(self):
        from apps.core.api_keys import ApiKey, Integration
        today = timezone.localdate()
        p = self.person("K1")
        from django.contrib.auth.models import Permission
        i = Integration.objects.create(name="x", user=p.user) if "user" in [f.name for f in Integration._meta.fields] else None
        print("integration:", i)
