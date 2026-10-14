"""
Permission probes: every route asked as the people who should not get
through, and the side doors of the two scoped exceptions (a rep's
customers, a person's leave and claims).

Written as an audit, not a fix. Each test that fails names a defect; the
message lists every route or record that let the wrong person through.
"""

import datetime
import re
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import URLResolver, get_resolver
from rest_framework.test import APIClient

READS = {"get", "head", "options"}
GROUP = re.compile(r"\(\?P<(\w+)>([^)]*)\)")
SAMPLE = {"verb": "start", "code": "NOPE", "roll": "x", "machine": "x", "path": "x"}


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        else:
            yield prefix + str(pattern.pattern), pattern


def _url(route):
    def fill(match):
        return SAMPLE.get(match.group(1), "999999")
    path = GROUP.sub(fill, route).replace("^", "").replace("$", "").replace("\\.", ".")
    return "/" + path


def api_routes():
    """(url, method, action, view class) for every DRF route under /api/."""
    return list(_api_routes())


def _api_routes():
    for route, pattern in _walk(get_resolver().url_patterns):
        if "(?P<format>" in route or not route.startswith("api/"):
            continue
        cls = getattr(pattern.callback, "cls", None)
        actions = getattr(pattern.callback, "actions", None)
        if cls is None:
            continue
        if actions is None:  # an APIView: its methods
            actions = {m: m for m in ("get", "post", "put", "patch", "delete") if hasattr(cls, m)}
        for method, action in list(actions.items()):
            yield _url(route), method, action, cls


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def holding(name, *perms):
    user = User.objects.create_user(name)
    for perm in perms:
        app_label, codename = perm.split(".", 1)
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app_label,
                                                         codename=codename))
    return User.objects.get(pk=user.pk)


class EveryWriteRouteTests(TestCase):
    """
    Walks the URL conf. A write that a login with no permission, or with
    every view permission and nothing else, gets past the permission gate
    is listed (any answer but 401 or 403: a 404 for a missing record or a
    400 for an empty body means the gate let it through).
    """

    # Routes whose handler, not the gate, decides (they take IsAuthenticated
    # alone and check the record's own permission inside). Probed apart below.
    HANDLER_CHECKED = ("/api/core/attachments/", "/api/core/notes/", "/api/core/follow-ups/",
                       "/api/core/saved-filters/", "/api/core/me/")
    # A POST that writes nothing: works a fabric out from a weight (mapped to view_bagspecification).
    READS_BY_POST = ("/api/manufacturing/bag-specifications/solve/",)

    def _through(self, user):
        client = client_for(user)
        through = []
        for url, method, action, cls in api_routes():
            if method in READS or url.startswith(self.HANDLER_CHECKED) or url in self.READS_BY_POST:
                continue
            response = getattr(client, method)(url, {}, format="json")
            if response.status_code not in (401, 403):
                through.append(f"{method.upper()} {url} [{cls.__name__}.{action}] -> {response.status_code}")
        return through

    def test_a_login_with_no_permission_writes_nothing(self):
        through = self._through(User.objects.create_user("nobody"))
        self.assertEqual(through, [], "\n" + "\n".join(through))

    def test_a_login_that_may_read_everything_writes_nothing(self):
        viewer = User.objects.create_user("viewer")
        viewer.user_permissions.set(Permission.objects.filter(codename__startswith="view_"))
        viewer = User.objects.get(pk=viewer.pk)
        through = self._through(viewer)
        self.assertEqual(through, [], "\n" + "\n".join(through))


class EveryReadRouteTests(TestCase):
    """A login with no permission at all reads nothing under /api/."""

    # Open to any login, each checking the record's own permission inside (or
    # answering only the login's own rows): probed apart, or nothing to leak.
    OPEN = ("/api/core/me/", "/api/core/endpoints/", "/api/core/history/", "/api/core/attachments/",
            "/api/core/notes/", "/api/core/follow-ups/", "/api/core/saved-filters/", "/api/web/search/",
            "/api/web/inbox/", "/api/web/summary/")

    def test_a_login_with_no_permission_reads_nothing(self):
        client = client_for(User.objects.create_user("nobody"))
        through = []
        for url, method, action, cls in api_routes():
            if method != "get" or url.startswith(self.OPEN) or cls.__name__ == "APIRootView":
                continue
            response = client.get(url)
            if response.status_code not in (401, 403):
                through.append(f"GET {url} [{cls.__name__}.{action}] -> {response.status_code}")
        self.assertEqual(through, [], "\n" + "\n".join(through))


class AddAloneActsOnARecordTests(TestCase):
    """
    DRF maps every POST to add_. A custom action on one record that no
    action_permission_map names therefore takes only the right to add
    one: a void, a post, a cancel, a commit. Listed mechanically: a login
    holding add_<model> and view_<model> and nothing else.
    """

    def test_no_action_on_a_record_takes_only_the_right_to_add(self):
        from apps.core.permissions import ModelPermissions

        through = []
        for index, (url, method, action, cls) in enumerate(api_routes()):
            if method != "post" or action in ("create",) or "999999" not in url:
                continue
            if ModelPermissions not in cls.permission_classes or action in getattr(cls, "action_permission_map", {}):
                continue
            model = cls.queryset.model
            user = holding(f"adder-{index}", f"{model._meta.app_label}.add_{model._meta.model_name}",
                           f"{model._meta.app_label}.view_{model._meta.model_name}")
            response = client_for(user).post(url, {}, format="json")
            if response.status_code not in (401, 403):
                through.append(f"POST {url} [{cls.__name__}.{action}] with add_{model._meta.model_name}"
                               f" -> {response.status_code}")
        self.assertEqual(through, [], "\n" + "\n".join(through))


class CsrfOnEveryWriteTests(TestCase):
    """A signed-in browser session with no CSRF token writes nothing."""

    def test_every_write_refuses_a_session_without_its_token(self):
        boss = User.objects.create_superuser("boss", "boss@example.com", "pw")
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(boss)
        through = []
        for url, method, action, cls in api_routes():
            if method in READS:
                continue
            response = getattr(client, method)(url, {}, format="json")
            if response.status_code != 403 or b"CSRF" not in response.content:
                through.append(f"{method.upper()} {url} [{cls.__name__}.{action}] -> {response.status_code}")
        self.assertEqual(through, [], "\n" + "\n".join(through))


# --- a rep's customers: the side doors -------------------------------------

from apps.sales import tests_reps as _reps  # noqa: E402  (module, so its classes are not collected here)


class RepSideDoorTests(_reps.RepTestCase):
    """
    Rep A carries Acme; rep B carries Beta. Every probe asks, as rep A,
    something that answers with Beta.
    """

    def posted_for(self, customer):
        self.customer = customer
        invoice = self.make_invoice()
        invoice.post()
        return invoice

    def test_the_claims_report_names_another_reps_customer(self):
        self.posted_for(self.beta).credit_claim(Decimal("10"), "torn")
        response = self.as_user(self.rep_a).get("/api/sales/invoices/claims/",
                                                {"from": "2000-01-01", "to": "2100-12-31"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("Beta", {row["customer"] for row in response.json()})

    def test_the_bad_debt_report_names_another_reps_customer(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        company = Company.get()
        company.bad_debt_account = Account.objects.create(code="6900", name="Bad debts",
                                                          account_type=AccountType.EXPENSE)
        company.save()
        self.posted_for(self.beta).write_off(reason="Gone under")
        response = self.as_user(self.rep_a).get("/api/sales/sales-reports/bad-debt/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["customer"] for row in response.json() if "Beta" in row["customer"]], [])

    def test_the_inbox_counts_another_reps_overdue_invoices(self):
        self.posted_for(self.beta)  # dated 2026-01-01, due long ago
        client = self.as_user(self.rep_a)
        aging = client.get("/api/sales/invoices/aging/").json()
        self.assertEqual(sum(bucket["count"] for key, bucket in aging.items() if key != "current"), 0)
        rows = {row["key"]: row["count"] for row in client.get("/api/web/inbox/").json()["rows"]}
        self.assertEqual(rows.get("invoices_overdue", 0), 0, rows)

    def test_a_deleted_order_of_another_reps_customer_reads_in_its_history(self):
        manager = self.as_role("AR Manager")
        pk = self.beta_order.pk
        self.assertEqual(manager.delete(f"/api/sales/sales-orders/{pk}/").status_code, 204)
        response = self.as_user(self.rep_a).get("/api/core/history/", {"model": "sales.salesorder", "id": pk})
        self.assertEqual(response.status_code, 404, response.content)

    def test_tax_profiles_show_another_reps_customer(self):
        from apps.accounting.models import PartyTaxProfile

        PartyTaxProfile.objects.create(party=self.beta, pan="AAACB1234F")
        response = self.as_user(self.rep_a).get("/api/accounting/party-tax-profiles/", {"search": "Beta"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["pan"] for row in response.json()], [])

    def test_an_activity_on_another_reps_customer_is_written_and_names_them(self):
        response = self.as_user(self.rep_a).post("/api/sales/activities/", {
            "kind": "call", "party": self.beta.pk, "summary": "Who is this?"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)


class RepSeesWhoHoldsABatchTests(TestCase):
    """The recall names every customer shipped a batch, to a rep who carries none of them."""

    def test_a_rep_reads_the_recall(self):
        from apps.manufacturing import tests_trace

        case = type("Trace", (tests_trace.TraceTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        case.a_run()
        case.ship("600")
        call_command("setup_roles", verbosity=0)
        rep = User.objects.create_user("rep-nobody")
        rep.groups.add(Group.objects.get(name="Sales Rep"))
        response = client_for(rep).get(f"/api/manufacturing/lot-trace/{case.polymer_lot.pk}/recall/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["customers"], [])


# --- a posted document changed by moving its line ---------------------------

class ALineMovedOffAPostedInvoiceTests(_reps.RepTestCase):
    """The line's save() asks only where it is going, never where it was."""

    def test_a_rep_moves_a_line_off_a_posted_invoice(self):
        posted = self.make_invoice()
        posted.post()
        before = posted.subtotal()
        draft = self.make_invoice()
        line = posted.lines.get()
        response = self.as_user(self.rep_a).patch(f"/api/sales/invoice-lines/{line.pk}/",
                                                  {"invoice": draft.pk}, format="json")
        posted.refresh_from_db()
        self.assertEqual((response.status_code, posted.lines.count(), posted.subtotal()),
                         (400, 1, before), response.content)


class AReadingMovedOffAPostedInspectionTests(TestCase):
    def test_an_inspector_moves_a_reading_off_a_posted_inspection(self):
        from apps.quality import tests as quality_tests
        from apps.quality.models import Inspection

        case = type("Q", (quality_tests.QualityTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        plan = case.plan()
        posted = case.inspect(plan, [87, 88, 89])
        posted.post()
        draft = case.inspect(plan, [87, 88, 89])
        call_command("setup_roles", verbosity=0)
        inspector = User.objects.create_user("qi")
        inspector.groups.add(Group.objects.get(name="Quality Inspector"))
        reading = posted.readings.first()
        response = client_for(inspector).patch(f"/api/quality/readings/{reading.pk}/",
                                               {"inspection": draft.pk}, format="json")
        self.assertEqual((response.status_code, Inspection.objects.get(pk=posted.pk).readings.count()),
                         (400, 3), response.content)


# --- approvals: who signs their own -----------------------------------------

from apps.sales import tests_approvals as _approvals  # noqa: E402


class SelfApprovedOrderTests(_approvals.ApprovalTestCase):
    def test_an_ar_manager_approves_the_order_they_raised(self):
        from apps.sales.models import ApprovalPolicy

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        manager = User.objects.create_user("ar")
        manager.groups.add(Group.objects.get(name="AR Manager"))
        order = self.draft(discount="40")
        order.created_by = manager
        order.save(update_fields=["created_by"])
        response = client_for(manager).post(f"/api/sales/sales-orders/{order.pk}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)


from apps.purchasing import tests_requisitions as _requisitions  # noqa: E402


class SelfApprovedRequisitionTests(_requisitions.RequisitionTestCase):
    """setup_roles: "nobody approves their own request". An AP Manager who is also an employee does."""

    def test_the_requester_approves_their_own_requisition(self):
        from apps.hr.models import Employee

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ap")
        user.groups.add(*Group.objects.filter(name__in=["AP Manager", "Employee Self Service"]))
        Employee.objects.create(party=self.employee, employee_number="E-1",
                                hire_date=datetime.date(2020, 1, 1), user=user)
        requisition = self.requisition()
        response = client_for(user).post(f"/api/purchasing/requisitions/{requisition.pk}/approve/", {},
                                         format="json")
        self.assertEqual(response.status_code, 400, response.content)


class ConcessionNamesSomeoneElseTests(TestCase):
    """With the second-person rule on, the inspector names the manager and passes it."""

    def test_an_inspector_posts_a_concession_in_the_managers_name(self):
        from apps.quality import tests as quality_tests
        from apps.quality.models import Disposition, QualitySettings

        case = type("Q", (quality_tests.QualityTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        settings = QualitySettings.get()
        settings.concessions_need_a_second_person = True
        settings.save()
        call_command("setup_roles", verbosity=0)
        inspector = User.objects.create_user("qi")
        inspector.groups.add(Group.objects.get(name="Quality Inspector"))
        client = client_for(inspector)
        failed = case.inspect(case.plan(), [92, 92, 92])
        changed = client.patch(f"/api/quality/inspections/{failed.pk}/", {
            "disposition": Disposition.CONCESSION, "decided_by": case.manager.pk,
            "decision_note": "Customer takes it."}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        posted = client.post(f"/api/quality/inspections/{failed.pk}/post/")
        self.assertEqual(posted.status_code, 400, posted.content)


class InspectorVoidsAFailedCalibrationTests(TestCase):
    """Quality Inspector holds add and view on calibrations; void takes add."""

    def test_the_inspector_withdraws_the_calibration_that_found_their_balance_out(self):
        from apps.quality import tests_calibration
        from apps.quality.calibration import CalibrationResult

        D = datetime.date
        case = type("C", (tests_calibration.CalibrationTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        case.calibrate(D(2026, 1, 10))
        case.measured(D(2026, 3, 1)).post()
        found = case.calibrate(D(2026, 6, 15), CalibrationResult.FAIL)
        self.assertEqual(len(found.suspect_inspections()), 1)
        call_command("setup_roles", verbosity=0)
        inspector = User.objects.create_user("qi")
        inspector.groups.add(Group.objects.get(name="Quality Inspector"))
        response = client_for(inspector).post(f"/api/quality/calibrations/{found.pk}/void/",
                                              {"reason": "Lab error"}, format="json")
        self.assertEqual(response.status_code, 403, response.content)


# --- leave and claims: the person's own -------------------------------------

from apps.hr import tests_leave as _leave  # noqa: E402


class ManagerCancelsAReportsLeaveTests(_leave.LeaveTestCase):
    """Cancel takes add_leaverequest, which every employee holds; it asks nothing of who."""

    def test_a_manager_with_self_service_cancels_a_reports_approved_leave(self):
        from apps.hr.models import LeaveStatus

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("boss")
        user.groups.add(*Group.objects.filter(name__in=["Line Manager", "Employee Self Service"]))
        self.boss.user = user
        self.boss.save()
        booking = self.request(self.employee("A1"), datetime.date(2027, 7, 1), datetime.date(2027, 7, 3))
        booking.approve(by=self.boss)
        response = client_for(user).post(f"/api/hr/leave-requests/{booking.pk}/cancel/", {}, format="json")
        booking.refresh_from_db()
        self.assertEqual((response.status_code, booking.status), (403, LeaveStatus.APPROVED), response.content)


class ExpenseLinesTests(_leave.LeaveTestCase):
    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.hr.expenses import ExpenseClaim, ExpenseLine

        call_command("setup_roles", verbosity=0)
        self.travel = Account.objects.create(code="6100", name="Travel", account_type=AccountType.EXPENSE)
        self.me = self.employee("ME")
        self.user = User.objects.create_user("me")
        self.user.groups.add(Group.objects.get(name="Employee Self Service"))
        self.me.user = self.user
        self.me.save()
        self.colleague = self.employee("THEM")
        self.Claim, self.Line = ExpenseClaim, ExpenseLine

    def claim(self, employee, amount="100"):
        claim = self.Claim.objects.create(employee=employee, purpose="Visit")
        self.Line.objects.create(claim=claim, spent_on=datetime.date(2026, 9, 1), expense_account=self.travel,
                                 description="Taxi", amount=Decimal(amount))
        return claim

    def test_a_line_is_written_onto_a_colleagues_claim(self):
        theirs = self.claim(self.colleague)
        response = client_for(self.user).post("/api/hr/expense-lines/", {
            "claim": theirs.pk, "spent_on": "2026-09-02", "expense_account": self.travel.pk,
            "description": "Dinner", "amount": "5000"}, format="json")
        self.assertEqual((response.status_code, theirs.lines.count()), (400, 1), response.content)

    def test_a_line_is_moved_off_an_approved_claim(self):
        approved = self.claim(self.me, "100")
        approved.submit()
        approved.approve(by=self.boss)
        draft = self.claim(self.me, "1")
        line = approved.lines.get()
        response = client_for(self.user).patch(f"/api/hr/expense-lines/{line.pk}/", {"claim": draft.pk},
                                               format="json")
        self.assertEqual((response.status_code, approved.lines.count()), (400, 1), response.content)


# --- logins -------------------------------------------------------------------

class HrAdminLetsThemselvesInTests(TestCase):
    """users_api: "nobody changes their own roles ... so one person cannot let themselves in"."""

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr")
        self.hr.groups.add(Group.objects.get(name="HR Admin"))

    def test_hr_admin_makes_a_second_login_as_controller(self):
        response = client_for(self.hr).post("/api/core/users/", {
            "username": "hr-too", "password": "Plenty-Long-Pass-2026!", "roles": ["Controller"]}, format="json")
        self.assertEqual(response.status_code, 400, response.content)

    def test_hr_admin_sets_the_controllers_password(self):
        controller = User.objects.create_user("controller", password="Original-Pass-2026!")
        controller.groups.add(Group.objects.get(name="Controller"))
        response = client_for(self.hr).post(f"/api/core/users/{controller.pk}/set_password/",
                                            {"password": "Hr-Knows-This-2026!"}, format="json")
        controller.refresh_from_db()
        self.assertEqual((response.status_code, controller.check_password("Hr-Knows-This-2026!")),
                         (403, False), response.content)


# --- the admin --------------------------------------------------------------

class AdminBulkDeleteTests(_leave.LeaveTestCase):
    """
    The changelist's "delete selected" runs QuerySet.delete(), which never
    calls the model's delete(). Where the admin does not refuse the row
    itself (has_delete_permission with the object), the model's refusal is
    skipped. LeaveRequest refuses approved leave in delete(); its admin
    asks nothing.
    """

    def test_hr_admin_staff_deletes_approved_leave_in_bulk(self):
        from django.test import override_settings

        from apps.hr.models import LeaveRequest

        call_command("setup_roles", verbosity=0)
        hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        hr.groups.add(Group.objects.get(name="HR Admin"))
        booking = self.request(self.employee("A1"), datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        booking.approve(by=self.boss)
        api = client_for(hr).delete(f"/api/hr/leave-requests/{booking.pk}/")
        self.assertEqual(api.status_code, 400, api.content)
        self.client.force_login(hr)
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            self.client.post("/admin/hr/leaverequest/", {
                "action": "delete_selected", "_selected_action": [booking.pk], "post": "yes"})
        self.assertTrue(LeaveRequest.objects.filter(pk=booking.pk).exists())


class LeaveReadThroughTheEmployeeTests(_leave.LeaveTestCase):
    """Leave is read by the person, their managers and HR; the employee's leave/ action asks only view_employee."""

    def test_a_quality_inspector_reads_a_colleagues_sick_leave(self):
        call_command("setup_roles", verbosity=0)
        colleague = self.employee("SICKLY")
        self.request(colleague, datetime.date(2026, 3, 2), datetime.date(2026, 3, 4), policy=self.sick,
                     leave_type="sick").approve(by=self.boss)
        inspector = User.objects.create_user("qi")
        inspector.groups.add(Group.objects.get(name="Quality Inspector"))
        client = client_for(inspector)
        self.assertEqual(client.get("/api/hr/leave-requests/").status_code, 403)  # leave itself: refused
        response = client.get(f"/api/hr/employees/{colleague.pk}/leave/", {"year": 2026})
        self.assertEqual(response.status_code, 404, response.content)


class AppraisalOfSomeoneNotTheirsTests(_leave.LeaveTestCase):
    def test_a_line_manager_appraises_a_peer(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("lm")
        user.groups.add(Group.objects.get(name="Line Manager"))
        me = self.employee("LM", manager=None)
        me.user = user
        me.save()
        peer = self.employee("PEER", manager=None)
        response = client_for(user).post("/api/hr/appraisals/", {
            "employee": peer.pk, "period_start": "2026-01-01", "period_end": "2026-06-30", "rating": 1,
            "improvements": "Everything."}, format="json")
        self.assertEqual(response.status_code, 400, response.content)


class ImportGivesRolesTests(TestCase):
    """
    The import takes core.import_records alone (the Controller's). Its
    employees file makes logins with roles, and gives roles to an existing
    login not yet linked to an employee: the Controller's own included.
    """

    def test_the_controller_gives_themselves_hr_admin_through_the_import(self):
        call_command("setup_roles", verbosity=0)
        controller = User.objects.create_user("ctl")
        controller.groups.add(Group.objects.get(name="Controller"))
        text = "employee_number,name,hire_date,username,roles\nE-9,Controller,2024-04-01,ctl,HR Admin\n"
        response = client_for(controller).post("/api/imports/records/run/",
                                                {"kind": "employees", "text": text, "commit": True}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("HR Admin", set(controller.groups.values_list("name", flat=True)), response.content)


class AdminUserFormTests(TestCase):
    """
    The API keeps an HR Admin off their own roles and away from superusers
    (users_api). Django's own user admin, still registered, asks only
    auth.change_user: a staff HR Admin makes themselves a superuser.
    """

    def test_a_staff_hr_admin_makes_themselves_superuser_in_the_admin(self):
        from django.test import override_settings

        call_command("setup_roles", verbosity=0)
        hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        hr.groups.add(Group.objects.get(name="HR Admin"))
        self.client.force_login(hr)
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            page = self.client.get(f"/admin/auth/user/{hr.pk}/change/")
            self.assertEqual(page.status_code, 200)
            self.client.post(f"/admin/auth/user/{hr.pk}/change/", {
                "username": "hr-staff", "first_name": "", "last_name": "", "email": "",
                "is_active": "on", "is_staff": "on", "is_superuser": "on",
                "groups": [str(pk) for pk in hr.groups.values_list("pk", flat=True)],
                "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00",
                "initial-date_joined_0": "2026-01-01", "initial-date_joined_1": "00:00:00",
            })
        hr.refresh_from_db()
        self.assertFalse(hr.is_superuser)


class AttachmentFilesTests(TestCase):
    """No finding expected: a name cannot climb out of MEDIA_ROOT, HTML is refused, a file is never served inline."""

    def test_names_kinds_and_disposition(self):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from apps.core.attachments import Attachment
        from apps.core.models import Party

        party = Party.objects.create(code="P1", name="Somebody")
        boss = User.objects.create_superuser("boss", "boss@example.com", "pw")
        client = client_for(boss)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            refused = client.post("/api/core/attachments/", {
                "model": "core.party", "id": party.pk,
                "file": SimpleUploadedFile("page.html", b"<script>alert(1)</script>", "text/html")},
                format="multipart")
            self.assertEqual(refused.status_code, 400, refused.content)
            kept = client.post("/api/core/attachments/", {
                "model": "core.party", "id": party.pk,
                "file": SimpleUploadedFile("../../../etc/passwd.pdf", b"<html>not a pdf</html>", "text/html")},
                format="multipart")
            self.assertEqual(kept.status_code, 201, kept.content)
            row = Attachment.objects.get()
            self.assertTrue(row.file.name.startswith("attachments/core/party/"), row.file.name)
            self.assertNotIn("..", row.file.name)
            served = client.get(f"/api/core/attachments/{row.pk}/download/")
            self.assertTrue(served["Content-Disposition"].startswith("attachment"), served["Content-Disposition"])
            self.assertEqual(served["Content-Type"], "application/pdf")
