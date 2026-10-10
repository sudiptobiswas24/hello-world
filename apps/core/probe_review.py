"""
Review probes against the O83-O88 fixes: each test states what should be
true, and fails where a fix leaves a hole or breaks what was meant.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.hr import tests_leave as _leave
from apps.sales import tests_approvals as _approvals
from apps.sales import tests_reps as _reps


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class HrAdminKeepsTheLoginsTests(TestCase):
    """users_api: the HR Admin makes a login, gives and takes roles, deactivates a leaver."""

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr")
        self.hr.groups.add(Group.objects.get(name="HR Admin"))

    def test_hr_admin_deactivates_a_leaving_sales_rep(self):
        leaver = User.objects.create_user("leaving-rep")
        leaver.groups.add(Group.objects.get(name="Sales Rep"))
        response = client_for(self.hr).post(f"/api/core/users/{leaver.pk}/deactivate/", {}, format="json")
        leaver.refresh_from_db()
        self.assertEqual((response.status_code, leaver.is_active), (200, False), response.content)

    def test_hr_admin_gives_the_new_bookkeeper_their_role(self):
        response = client_for(self.hr).post("/api/core/users/", {
            "username": "asha", "password": "loom-shed-2026", "roles": ["Bookkeeper"]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)


class SelfApprovalThroughARevisionTests(_approvals.ApprovalTestCase):
    """A revision copies the quote's lines in code, unstamped: authors() finds nobody."""

    def test_the_manager_who_wrote_the_quote_approves_it_through_its_revision(self):
        from apps.sales.models import ApprovalPolicy, Quotation, QuotationLine, QuotationStatus

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        manager = User.objects.create_user("ar")
        manager.groups.add(Group.objects.get(name="AR Manager"))
        client = client_for(manager)
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             valid_until=datetime.date(2027, 4, 1), currency=self.usd,
                                             created_by=manager, updated_by=manager)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                     unit_price=Decimal("100"), discount_percent=Decimal("40"),
                                     revenue_account=self.revenue, created_by=manager, updated_by=manager)
        Quotation.objects.filter(pk=quotation.pk).update(status=QuotationStatus.SENT)
        # Accepting the quote itself is refused: the manager wrote it.
        own = client.post(f"/api/sales/quotations/{quotation.pk}/accept/", {"approve": True}, format="json")
        self.assertEqual(own.status_code, 400, own.content)
        revised = client.post(f"/api/sales/quotations/{quotation.pk}/revise/", {}, format="json")
        self.assertEqual(revised.status_code, 200, revised.content)
        Quotation.objects.filter(pk=revised.json()["id"]).update(status=QuotationStatus.SENT)
        accepted = client.post(f"/api/sales/quotations/{revised.json()['id']}/accept/", {"approve": True},
                               format="json")
        self.assertEqual(accepted.status_code, 400, accepted.content)


class DeletedLeaveHistoryTests(_leave.LeaveTestCase):
    """The deleted-history rule now holds for the rep scope only; leave is a person's too."""

    def test_a_colleague_reads_the_history_of_deleted_leave(self):
        call_command("setup_roles", verbosity=0)
        mine = User.objects.create_user("me")
        mine.groups.add(Group.objects.get(name="Employee Self Service"))
        me = self.employee("ME", manager=None)
        me.user = mine
        me.save()
        theirs = User.objects.create_user("them")
        theirs.groups.add(Group.objects.get(name="Employee Self Service"))
        them = self.employee("THEM", manager=None)
        them.user = theirs
        them.save()
        made = client_for(theirs).post("/api/hr/leave-requests/", {
            "employee": them.pk, "policy": self.sick.pk, "leave_type": "sick", "start_date": "2026-11-02",
            "end_date": "2026-11-04", "reason": "Hospital"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        hr = User.objects.create_user("hr")
        hr.groups.add(Group.objects.get(name="HR Admin"))
        self.assertEqual(client_for(hr).delete(f"/api/hr/leave-requests/{pk}/").status_code, 204)
        response = client_for(mine).get("/api/core/history/", {"model": "hr.leaverequest", "id": pk})
        self.assertEqual(response.status_code, 404, response.content)


class BulkDeleteIsAllOrNothingTests(_leave.LeaveTestCase):
    def test_a_pending_and_an_approved_leave_selected_together(self):
        from apps.hr.models import LeaveRequest

        call_command("setup_roles", verbosity=0)
        hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        hr.groups.add(Group.objects.get(name="HR Admin"))
        pending = self.request(self.employee("A1"), datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        approved = self.request(self.employee("A2"), datetime.date(2026, 7, 1), datetime.date(2026, 7, 3))
        approved.approve(by=self.boss)
        self.client.force_login(hr)
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            self.client.post("/admin/hr/leaverequest/", {
                "action": "delete_selected", "_selected_action": [pending.pk, approved.pk], "post": "yes"})
        self.assertEqual(LeaveRequest.objects.filter(pk__in=[pending.pk, approved.pk]).count(), 2)


class RequisitionCancelledByAnyoneTests(TestCase):
    """Cancel of an approved requisition takes change_purchaserequisition, which Self Service holds."""

    def test_self_service_cancels_a_colleagues_approved_requisition(self):
        from apps.purchasing import tests_requisitions
        from apps.purchasing.models import PurchaseRequisition, RequisitionStatus

        case = type("R", (tests_requisitions.RequisitionTestCase,), {"runTest": lambda self: None})()
        case.setUp()
        requisition = case.requisition()
        requisition.approve(by=case.manager)
        call_command("setup_roles", verbosity=0)
        other = User.objects.create_user("someone-else")
        other.groups.add(Group.objects.get(name="Employee Self Service"))
        response = client_for(other).post(f"/api/purchasing/requisitions/{requisition.pk}/cancel/", {},
                                          format="json")
        self.assertEqual((response.status_code, PurchaseRequisition.objects.get(pk=requisition.pk).status),
                         (403, RequisitionStatus.APPROVED), response.content)


class ConvertedLeadTests(_reps.RepTestCase):
    """An unowned lead stays unowned once rep B converts it: rep A reads B's new customer off it."""

    def test_rep_a_reads_the_customer_rep_b_made_from_an_unowned_lead(self):
        from apps.sales.crm import Lead

        lead = Lead.objects.create(company_name="Gamma Cement", contact_name="Mr G")
        converted = self.as_user(self.rep_b).post(f"/api/sales/leads/{lead.pk}/convert/", {"code": "GCEM"},
                                                  format="json")
        self.assertEqual(converted.status_code, 200, converted.content)
        rows = self.as_user(self.rep_a).get("/api/sales/leads/", {"page_size": 200}).json()
        self.assertEqual([row["company_name"] for row in rows if row["id"] == lead.pk], [])


class EveryListAsRepATests(_reps.RepTestCase):
    """
    Rep B's customer Beta has an order, a posted invoice, a quotation, an
    opportunity, a call-off schedule, a tax profile and an address. Rep A
    asks every list under /api/ with no id (and every grouping of it, and
    the search): none may answer with Beta.
    """

    def test_no_list_names_beta(self):
        import re

        from django.urls import URLResolver, get_resolver

        from apps.accounting.models import PartyTaxProfile
        from apps.core.models import Address
        from apps.sales.crm import Opportunity
        from apps.sales.models import Quotation

        self.customer = self.beta
        invoice = self.make_invoice()
        invoice.post()
        Quotation.objects.create(customer=self.beta, quotation_date=datetime.date(2026, 9, 1), valid_until=datetime.date(2026, 12, 1))
        Opportunity.objects.create(customer=self.beta, title="Beta sacks", owner=self.rep_b.employee.party)
        PartyTaxProfile.objects.create(party=self.beta, pan="AAACB1234F")
        Address.objects.create(party=self.beta, line1="1 Beta Road", city="Pune")
        client = self.as_user(self.rep_a)

        def walk(patterns, prefix=""):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
                else:
                    yield prefix + str(pattern.pattern), pattern

        leaks = []
        lists = []
        for route, pattern in walk(get_resolver().url_patterns):
            actions = getattr(pattern.callback, "actions", None) or {}
            if not route.startswith("api/") or "(?P<" in route or actions.get("get") is None:
                continue
            url = "/" + route.replace("^", "").replace("$", "")
            response = client.get(url, {"page_size": 500})
            if response.status_code == 400:
                response = client.get(url, {"from": "2000-01-01", "to": "2100-01-01", "as_of": "2100-01-01",
                                            "start": "2000-01-01", "end": "2100-01-01"})
            if response.status_code == 200 and re.search(r"Beta|BETA|AAACB1234F", response.content.decode(errors="ignore")):
                leaks.append(f"GET {url}")
            if actions.get("get") == "list" and response.status_code == 200:
                lists.append(url)
        for url in lists:
            options = client.get("/api/web/summary/", {"endpoint": url})
            if options.status_code != 200:
                continue
            for by in options.json()["by"]:
                grouped = client.get("/api/web/summary/", {"endpoint": url, "by": by["key"]})
                if grouped.status_code == 200 and re.search(r"Beta|BETA", grouped.content.decode()):
                    leaks.append(f"summary {url} by {by['key']}")
        for q in ("Beta", "BETA", "Pune"):
            found = client.get("/api/web/search/", {"q": q}).content.decode()
            if "Beta" in found:
                leaks.append(f"search {q}")
        # The scan read something: rep A's own lists answer, and name Acme.
        self.assertGreater(len(lists), 40, lists)
        self.assertIn("Acme", client.get("/api/sales/sales-orders/").content.decode())
        self.assertEqual(leaks, [], "\n".join(leaks))
