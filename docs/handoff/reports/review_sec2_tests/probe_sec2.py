"""
Review probes for 0bd52ab (O142), e73b502 (O143), 10d9ff6 (O144), 689bac7 (O128).
Each test states what SHOULD be true; a failure is a hole. Run:
  manage.py test apps.core.probe_sec2 --settings=scripts.gate.fastsettings
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.management.commands.setup_roles import ROLES
from apps.core.roles import ProposalStatus, RoleProposal
from apps.hr import tests_people as _people
from apps.purchasing import tests_requisitions as _req
from apps.sales import tests_approvals as _approvals

PW = "First-Pw-2026-xq"


def api_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def rows_of(response):
    data = response.json()
    if isinstance(data, list):
        return data
    for value in data.values():
        if isinstance(value, list):
            return value
    return []


def signed_in(username, password):
    """A real sign-in (session), the way a person with the first password does it."""
    client = APIClient()
    assert client.login(username=username, password=password), f"{username} cannot sign in"
    return client


class TwoPeople(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = self.mk("hr", "HR Admin")
        self.bk = self.mk("bk", "Bookkeeper")
        self.hrc = api_for(self.hr)

    def mk(self, name, *roles, password=None):
        user = User.objects.create_user(name, password=password)
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def make(self, client, username, roles, password=PW):
        response = client.post("/api/core/users/", {"username": username, "password": password, "roles": roles},
                               format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return User.objects.get(username=username)

    def holds(self, user, role):
        return user.groups.filter(name=role).exists()

    def pending(self, user, role):
        return RoleProposal.objects.get(user=user, group__name=role, status=ProposalStatus.PENDING)

    def confirm(self, client, proposal):
        return client.post(f"/api/core/role-proposals/{proposal.pk}/confirm/", {}, format="json")


class TwoPersonRuleTests(TwoPeople):
    def test_hr_cannot_confirm_own_proposal_nor_via_a_login_it_made_holding_only_what_hr_holds(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        self.assertFalse(self.holds(asha, "Bookkeeper"))
        proposal = self.pending(asha, "Bookkeeper")
        self.assertEqual(self.confirm(self.hrc, proposal).status_code, 400)
        puppet = self.make(self.hrc, "puppet", ["HR Admin"])  # held by HR: given at once
        self.assertTrue(self.holds(puppet, "HR Admin"))
        self.assertIn(self.confirm(api_for(puppet), proposal).status_code, (400, 403, 404))
        self.assertFalse(self.holds(asha, "Bookkeeper"))

    def test_the_login_the_role_is_for_cannot_confirm_it(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        self.assertIn(self.confirm(api_for(asha), self.pending(asha, "Bookkeeper")).status_code, (400, 403, 404))

    def test_someone_who_holds_it_confirms(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        self.assertEqual(self.confirm(api_for(self.bk), self.pending(asha, "Bookkeeper")).status_code, 200)
        self.assertTrue(self.holds(asha, "Bookkeeper"))

    def test_a_login_holding_the_role_only_as_a_pending_proposal_cannot_confirm(self):
        a = self.make(self.hrc, "a", ["Bookkeeper"])
        b = self.make(self.hrc, "b", ["Bookkeeper"])
        # a is pending for Bookkeeper; a tries to confirm b's
        self.assertIn(self.confirm(api_for(a), self.pending(b, "Bookkeeper")).status_code, (400, 403, 404))
        self.assertFalse(self.holds(b, "Bookkeeper"))
        # nor does a see it in its own queue
        self.assertEqual([r["username"] for r in rows_of(api_for(a).get("/api/core/role-proposals/?waiting=true"))], [])

    def test_two_keepers_proposing_one_role_make_one_proposal_and_the_second_is_not_a_proposer(self):
        hr2 = self.mk("hr2", "HR Admin")
        asha = self.make(self.hrc, "asha", [])
        self.assertEqual(self.hrc.post(f"/api/core/users/{asha.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 200)
        r2 = api_for(hr2).post(f"/api/core/users/{asha.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(RoleProposal.objects.filter(user=asha, group__name="Bookkeeper").count(), 1)
        proposal = self.pending(asha, "Bookkeeper")
        self.assertEqual(proposal.proposed_by, self.hr)
        # neither keeper confirms it
        self.assertEqual(self.confirm(self.hrc, proposal).status_code, 400)
        self.assertEqual(self.confirm(api_for(hr2), proposal).status_code, 400)

    def test_proposer_loses_the_keepers_right_or_is_switched_off_before_the_holder_confirms(self):
        a = self.make(self.hrc, "a", ["Bookkeeper"])
        b = self.make(self.hrc, "b", ["Bookkeeper"])
        self.hr.groups.clear()
        self.hr.is_active = False
        self.hr.save()
        got = (self.confirm(api_for(self.bk), self.pending(a, "Bookkeeper")).status_code, self.holds(a, "Bookkeeper"))
        # characterisation: a proposal by a since-removed keeper is still given
        print("\n[probe] proposer demoted+deactivated, holder confirms ->", got)
        self.assertEqual(got, (400, False), "a proposal by a keeper who has since lost the right or been switched off is still confirmable")
        del b

    def test_the_confirmer_loses_the_role_before_confirming(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        proposal = self.pending(asha, "Bookkeeper")
        self.bk.groups.clear()
        self.assertIn(self.confirm(api_for(User.objects.get(pk=self.bk.pk)), proposal).status_code, (400, 403, 404))
        self.assertFalse(self.holds(asha, "Bookkeeper"))

    def test_a_proposal_for_a_login_then_switched_off(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        proposal = self.pending(asha, "Bookkeeper")
        self.assertEqual(self.hrc.post(f"/api/core/users/{asha.pk}/deactivate/", {}, format="json").status_code, 200)
        rows = rows_of(api_for(self.bk).get("/api/core/role-proposals/?waiting=true"))
        confirmed = self.confirm(api_for(self.bk), proposal).status_code
        asha.refresh_from_db()
        print("\n[probe] proposal for a switched-off login: still listed to holder =", [r["username"] for r in rows],
              "; confirm ->", confirmed, "; holds =", self.holds(asha, "Bookkeeper"), "; active =", asha.is_active)
        # and HR cannot switch it back on once it holds the role
        reactivated = self.hrc.post(f"/api/core/users/{asha.pk}/reactivate/", {}, format="json").status_code
        print("[probe] HR reactivates it ->", reactivated)
        self.assertEqual((confirmed, self.holds(asha, "Bookkeeper")), (400, False),
                         "a role is given to a switched-off login by confirming its pending proposal")

    def test_hr_cannot_decline_to_dodge_nor_withdraw_after_confirm(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        proposal = self.pending(asha, "Bookkeeper")
        self.assertEqual(self.confirm(api_for(self.bk), proposal).status_code, 200)
        again = self.hrc.post(f"/api/core/role-proposals/{proposal.pk}/decline/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertTrue(self.holds(asha, "Bookkeeper"))


class FirstPasswordGapTests(TwoPeople):
    """O157: HR still signs in as the login after a higher role is confirmed, and by then can mint more."""

    def test_O157_hr_signs_in_as_the_login_after_the_role_is_confirmed(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        self.assertEqual(self.confirm(api_for(self.bk), self.pending(asha, "Bookkeeper")).status_code, 200)
        # HR knows PW. It signs in, through the real backend, as the Bookkeeper.
        as_asha = signed_in("asha", PW)
        me = as_asha.get("/api/core/me/").json()
        self.assertNotIn("Bookkeeper", me["roles"], "HR, signed in with the first password it chose, is a Bookkeeper")
        # HR's own door is shut (check_may_administer) once confirmed
        reset = self.hrc.post(f"/api/core/users/{asha.pk}/set_password/", {"password": "Another-Pw-2026-xq"},
                              format="json")
        self.assertEqual(reset.status_code, 403)

    def test_O157_amplified_hr_mints_holders_of_a_role_it_never_held_without_a_second_person(self):
        """One honest confirmation, then the keeper is both the proposer and (through the first login) the confirmer."""
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        self.assertEqual(self.confirm(api_for(self.bk), self.pending(asha, "Bookkeeper")).status_code, 200)
        as_asha = signed_in("asha", PW)  # HR, with the first password
        puppet = self.make(self.hrc, "puppet", ["HR Admin", "Bookkeeper"])  # HR Admin given; Bookkeeper proposed
        step = self.confirm(as_asha, self.pending(puppet, "Bookkeeper"))
        print("\n[probe] HR-as-asha confirms HR's own proposal for puppet ->", step.status_code)
        reached = False
        if step.status_code == 200:
            as_puppet = signed_in("puppet", PW)
            gave = as_puppet.post(f"/api/core/users/{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json")
            self.hr.refresh_from_db()
            reached = self.holds(self.hr, "Bookkeeper")
            print("[probe] puppet (HR-controlled, Bookkeeper+HR Admin) grants Bookkeeper to HR's OWN login ->",
                  gave.status_code, "; HR now holds Bookkeeper =", reached)
        self.assertEqual((step.status_code != 200, reached), (True, False),
                         "the proposer confirmed through a login only the proposer controls, then gave itself the role")

    def test_O157_existing_login_variant_hr_sets_the_password_while_the_proposal_is_pending(self):
        ravi = self.mk("ravi", "HR Admin")
        self.assertEqual(self.hrc.post(f"/api/core/users/{ravi.pk}/set_password/", {"password": PW}, format="json")
                         .status_code, 200)
        self.assertEqual(self.hrc.post(f"/api/core/users/{ravi.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 200)
        self.assertEqual(self.confirm(api_for(self.bk), self.pending(ravi, "Bookkeeper")).status_code, 200)
        self.assertEqual(self.hrc.post(f"/api/core/users/{ravi.pk}/set_password/", {"password": "Other-Pw-2026-xq"},
                                       format="json").status_code, 403)
        self.assertNotIn("Bookkeeper", signed_in("ravi", PW).get("/api/core/me/").json()["roles"],
                         "HR set ravi's password, proposed a role, and signs in as ravi with it once confirmed")

    def test_O157_email_set_while_the_role_is_pending_reaches_a_password_reset_after_confirm(self):
        from django.core import mail

        response = self.hrc.post("/api/core/users/", {"username": "asha", "password": PW, "roles": ["Bookkeeper"],
                                                      "email": "hr-mailbox@example.com"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        asha = User.objects.get(username="asha")
        self.assertEqual(self.confirm(api_for(self.bk), self.pending(asha, "Bookkeeper")).status_code, 200)
        from django.test import Client
        try:
            reset = Client().post("/accounts/password_reset/", {"email": "hr-mailbox@example.com"})
            print("\n[probe] /accounts/password_reset/ ->", reset.status_code, "mails:", len(mail.outbox))
        except Exception as error:  # templates may be missing
            print("\n[probe] /accounts/password_reset/ raised", type(error).__name__, error)


class OtherDoorTests(TwoPeople):
    def test_user_api_doors_propose_not_give(self):
        asha = self.make(self.hrc, "asha", ["Controller"])
        self.assertFalse(self.holds(asha, "Controller"))
        for verb in ("patch", "put"):
            body = {"username": "asha", "roles": ["Controller"]}
            getattr(self.hrc, verb)(f"/api/core/users/{asha.pk}/", body, format="json")
        self.hrc.post(f"/api/core/users/{asha.pk}/grant/", {"role": "Controller"}, format="json")
        self.hrc.post(f"/api/core/users/{asha.pk}/grant/", {"role": str(Group.objects.get(name="Controller").pk)},
                      format="json")
        self.assertFalse(self.holds(asha, "Controller"))
        self.assertEqual(RoleProposal.objects.filter(user=asha, group__name="Controller").count(), 1)

    def test_hr_cannot_give_itself_through_the_user_api(self):
        for body in ({"roles": ["Controller"]}, {"roles": ["HR Admin", "Controller"]}):
            r = self.hrc.patch(f"/api/core/users/{self.hr.pk}/", body, format="json")
            self.assertEqual(r.status_code, 400, r.content)
        r = self.hrc.post(f"/api/core/users/{self.hr.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(RoleProposal.objects.count(), 0)

    def test_the_proposal_and_role_endpoints_have_no_write_door(self):
        asha = self.make(self.hrc, "asha", ["Bookkeeper"])
        proposal = self.pending(asha, "Bookkeeper")
        codes = [
            self.hrc.post("/api/core/role-proposals/", {"user": asha.pk, "group": 1, "status": "confirmed"}, format="json").status_code,
            self.hrc.patch(f"/api/core/role-proposals/{proposal.pk}/", {"status": "confirmed"}, format="json").status_code,
            self.hrc.delete(f"/api/core/role-proposals/{proposal.pk}/").status_code,
            self.hrc.post("/api/core/roles/", {"name": "x"}, format="json").status_code,
            self.hrc.patch(f"/api/core/roles/{Group.objects.get(name='HR Admin').pk}/", {"name": "x"}, format="json").status_code,
        ]
        self.assertTrue(all(code in (403, 404, 405) for code in codes), codes)

    def test_admin_is_not_a_door(self):
        from django.test import Client, override_settings

        self.hr.is_staff = True
        self.hr.save()
        client = Client()
        client.force_login(self.hr)
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            codes = [client.get(path).status_code for path in
                     ("/admin/auth/user/", "/admin/auth/group/", f"/admin/auth/user/{self.bk.pk}/change/",
                      "/admin/core/roleproposal/", "/admin/core/roleproposal/add/")]
        self.assertTrue(all(code in (302, 403, 404) for code in codes), codes)

    def test_group_and_permission_endpoints_do_not_write(self):
        r = self.hrc.patch(f"/api/core/users/{self.bk.pk}/", {"is_staff": True, "is_superuser": True,
                                                              "user_permissions": [1], "groups": [1]}, format="json")
        self.bk.refresh_from_db()
        self.assertEqual((self.bk.is_staff, self.bk.is_superuser, self.bk.groups.count()), (False, False, 1), r.content)

    def test_deleting_a_login_is_refused(self):
        asha = self.make(self.hrc, "asha", [])
        self.assertIn(self.hrc.delete(f"/api/core/users/{asha.pk}/").status_code, (400, 403))
        su = User.objects.create_superuser("su", "su@example.com", "x")
        self.assertEqual(api_for(su).delete(f"/api/core/users/{asha.pk}/").status_code, 400)

    def test_import_links_only_as_refused_link_says(self):
        from apps.imports.importer import run

        ctl = self.mk("ctl", "Controller")
        csv = "employee_number,name,hire_date,username,roles\nE-1,Cee,2024-04-01,ctl,Controller\n"
        report = run("employees", csv, commit=True, user=self.hr)
        print("\n[probe] import linking a Controller login by HR -> committed:", report.committed, report.errors[:2])
        self.assertFalse(report.committed)
        from apps.hr.models import Employee
        self.assertFalse(Employee.objects.filter(user=ctl).exists())
        # no roles column effect
        ess = self.mk("ess", "Employee Self Service")
        csv = "employee_number,name,hire_date,username,roles\nE-2,Ess,2024-04-01,fresh,Controller\n"
        report = run("employees", csv, commit=True, user=self.hr)
        fresh = User.objects.filter(username="fresh").first()
        self.assertTrue(fresh is None or not fresh.groups.exists(), (report.committed, report.errors))
        del ess

    def test_a_login_with_only_a_pending_proposal_is_linkable_and_then_confirmed(self):
        """Characterisation: refused_link reads groups, not pending proposals."""
        from apps.hr.models import Employee
        from apps.hr.tests_people import make_employee_party

        asha = self.make(self.hrc, "asha", ["Controller"])
        boss = Employee.objects.create(party=make_employee_party("B1", "B1"), employee_number="B1",
                                       hire_date=datetime.date(2025, 1, 1))
        r = self.hrc.patch(f"/api/hr/employees/{boss.pk}/", {"user": asha.pk}, format="json")
        print("\n[probe] link a login with a Controller proposal pending to an employee ->", r.status_code)
        ctl = self.mk("ctl2", "Controller")
        confirmed = self.confirm(api_for(ctl), self.pending(asha, "Controller")).status_code
        print("[probe] holder confirms Controller for the login now linked to an employee ->", confirmed)


# --- O143 -------------------------------------------------------------------------------------

class RevisionOfARevisionTests(_approvals.ApprovalTestCase):
    def test_writer_of_the_first_quote_cannot_approve_the_second_revision(self):
        from apps.sales.models import ApprovalPolicy, Quotation, QuotationLine, QuotationStatus

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))

        def manager(name):
            user = User.objects.create_user(name)
            user.groups.add(Group.objects.get(name="AR Manager"))
            return user

        m1, m2, m3 = manager("m1"), manager("m2"), manager("m3")
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             valid_until=datetime.date(2027, 4, 1), currency=self.usd,
                                             created_by=m1, updated_by=m1)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                     unit_price=Decimal("100"), discount_percent=Decimal("40"),
                                     revenue_account=self.revenue, created_by=m1, updated_by=m1)
        Quotation.objects.filter(pk=quotation.pk).update(status=QuotationStatus.SENT)
        r1 = api_for(m2).post(f"/api/sales/quotations/{quotation.pk}/revise/", {}, format="json").json()["id"]
        Quotation.objects.filter(pk=r1).update(status=QuotationStatus.SENT)
        r2 = api_for(m3).post(f"/api/sales/quotations/{r1}/revise/", {}, format="json").json()["id"]
        Quotation.objects.filter(pk=r2).update(status=QuotationStatus.SENT)
        codes = {}
        for name, user in (("m1_first_writer", m1), ("m2_first_reviser", m2), ("m3_second_reviser", m3)):
            codes[name] = api_for(user).post(f"/api/sales/quotations/{r2}/accept/", {"approve": True},
                                             format="json").status_code
            self.assertEqual(codes[name], 400, (name, codes))
        # a fourth person may
        m4 = manager("m4")
        ok = api_for(m4).post(f"/api/sales/quotations/{r2}/accept/", {"approve": True}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_the_order_made_from_the_second_revision_counts_every_writer_as_raiser(self):
        from apps.sales.models import ApprovalPolicy, Quotation, QuotationLine, QuotationStatus

        call_command("setup_roles", verbosity=0)
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        users = []
        for name in ("w1", "w2", "w3", "w4"):
            user = User.objects.create_user(name)
            user.groups.add(Group.objects.get(name="AR Manager"))
            users.append(user)
        w1, w2, w3, w4 = users
        quotation = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1),
                                             valid_until=datetime.date(2027, 4, 1), currency=self.usd,
                                             created_by=w1, updated_by=w1)
        QuotationLine.objects.create(quotation=quotation, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                     unit_price=Decimal("100"), discount_percent=Decimal("40"),
                                     revenue_account=self.revenue, created_by=w1, updated_by=w1)
        Quotation.objects.filter(pk=quotation.pk).update(status=QuotationStatus.SENT)
        r1 = api_for(w2).post(f"/api/sales/quotations/{quotation.pk}/revise/", {}, format="json").json()["id"]
        Quotation.objects.filter(pk=r1).update(status=QuotationStatus.SENT)
        r2 = api_for(w3).post(f"/api/sales/quotations/{r1}/revise/", {}, format="json").json()["id"]
        Quotation.objects.filter(pk=r2).update(status=QuotationStatus.SENT)
        done = api_for(w4).post(f"/api/sales/quotations/{r2}/accept/", {"approve": True}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        order = Quotation.objects.get(pk=r2).sales_order
        self.assertIsNotNone(order)
        self.assertTrue({w1.pk, w2.pk, w3.pk} <= order.raised_by(), order.raised_by())
        # a revision made with nobody named (by=None, as code may) names nobody: nothing to find, nothing to crash
        again = Quotation.objects.get(pk=r2).create_revision() if False else None
        del again


# --- O144 / O128 ------------------------------------------------------------------------------

class RequisitionCancelEveryRoleTests(_req.RequisitionTestCase):
    def setUp(self):
        super().setUp()
        from apps.hr.models import Employee

        call_command("setup_roles", verbosity=0)
        self.dana = self.mkuser("dana", "Employee Self Service")
        Employee.objects.create(party=self.employee, employee_number="E-1", hire_date=datetime.date(2020, 1, 1),
                                user=self.dana)

    def mkuser(self, name, *roles):
        user = get_user_model().objects.create_user(name)
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def cancel(self, user, requisition):
        response = api_for(User.objects.get(pk=user.pk)).post(
            f"/api/purchasing/requisitions/{requisition.pk}/cancel/", {}, format="json")
        requisition.refresh_from_db()
        return response.status_code, requisition.status

    def test_cancel_by_every_role_matches_decide_or_requester(self):
        rows, wrong = {}, []
        for i, role in enumerate(ROLES):
            requisition = self.requisition()
            requisition.approve(by=self.manager)
            user = self.mkuser(f"role{i}", role)
            code, status = self.cancel(user, requisition)
            rows[role] = code
            should = user.has_perm("purchasing.decide_purchaserequisition")
            if (code == 200) != should:
                wrong.append((role, code, status, "decider" if should else "not decider"))
        print("\n[probe] requisition cancel by role:", {k: v for k, v in rows.items() if v == 200})
        self.assertEqual(wrong, [])

    def test_requester_holding_every_other_role_still_cancels_own(self):
        wrong = []
        for i, role in enumerate(ROLES):
            requisition = self.requisition()
            requisition.approve(by=self.manager)
            # dana wears the role as well as being the requester
            self.dana.groups.set(Group.objects.filter(name__in=["Employee Self Service", role]))
            code, _ = self.cancel(self.dana, requisition)
            if code != 200:
                wrong.append((role, code))
        self.assertEqual(wrong, [])

    def test_a_draft_created_by_a_self_service_login_through_the_api_can_be_cancelled_by_its_creator(self):
        client = api_for(self.dana)
        made = client.post("/api/purchasing/requisitions/", {"request_date": "2026-01-01"}, format="json")
        print("\n[probe] ESS creates a requisition with no requested_by ->", made.status_code, str(made.content)[:200])
        if made.status_code == 201:
            pk = made.json()["id"]
            r = client.post(f"/api/purchasing/requisitions/{pk}/cancel/", {}, format="json")
            print("[probe] its creator cancels it ->", r.status_code, str(r.content)[:200])
            self.assertEqual(r.status_code, 200)

    def test_requester_with_no_login_or_no_employee_record_only_deciders_cancel(self):
        requisition = self.requisition()
        requisition.approve(by=self.manager)
        from apps.hr.models import Employee
        Employee.objects.filter(party=self.employee).update(user=None)
        self.assertEqual(self.cancel(self.dana, requisition), (403, "approved"))
        decider = self.mkuser("dec", "Purchase Manager" if Group.objects.filter(name="Purchase Manager").exists() else "Controller")
        print("\n[probe] decider has decide perm:", decider.has_perm("purchasing.decide_purchaserequisition"))

    def test_O128_gate_by_every_role(self):
        routes = {
            "tds reverse": "/api/purchasing/tds-deductions/999999/reverse/",
            "challan void": "/api/purchasing/tds-challans/999999/void/",
            "grn accept": "/api/purchasing/goods-receipts/999999/accept/",
            "grn reject": "/api/purchasing/goods-receipts/999999/reject/",
        }
        old = {"tds reverse": "purchasing.add_tdsdeduction", "challan void": "purchasing.add_tdschallan",
               "grn accept": "purchasing.add_receiptinspection", "grn reject": "purchasing.add_receiptinspection"}
        new = {"tds reverse": "purchasing.post_bill", "challan void": "purchasing.post_bill",
               "grn accept": "purchasing.change_receiptinspection", "grn reject": "purchasing.change_receiptinspection"}
        wrong, lost, gained = [], [], []
        for i, role in enumerate(ROLES):
            user = self.mkuser(f"o128-{i}", role)
            client = api_for(user)
            for name, url in routes.items():
                code = client.post(url, {}, format="json").status_code
                through = code != 403
                if through != user.has_perm(new[name]):
                    wrong.append((role, name, code))
                if user.has_perm(old[name]) and not user.has_perm(new[name]):
                    lost.append((role, name))
                if user.has_perm(new[name]) and not user.has_perm(old[name]):
                    gained.append((role, name))
        print("\n[probe] O128 roles that LOST the action:", lost)
        print("[probe] O128 roles that GAINED the action:", gained)
        self.assertEqual(wrong, [])
        # superuser passes the gate
        su = User.objects.create_superuser("su", "su@example.com", "x")
        self.assertNotEqual(api_for(su).post(routes["tds reverse"], {}, format="json").status_code, 403)
