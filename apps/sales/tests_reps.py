"""
A sales rep sees and writes their own customers, nobody else's.

Asked of the API as people in their roles: rep A carries Acme, rep B
carries Beta, nobody carries Gamma, and an AR Manager sees all three.
"""

import datetime

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, tag
from rest_framework.test import APIClient

from apps.core.management.commands.setup_roles import ROLES
from apps.core.models import Address, Country, Party, PartyRole, PartyRoleAssignment
from apps.hr.models import Employee

from .models import CustomerProfile, Invoice, SalesOrder, SalesRep
from .tests import SalesTestCase

PARTIES = "/api/core/parties/"
ADDRESSES = "/api/core/addresses/"
ORDERS = "/api/sales/sales-orders/"
ORDER_LINES = "/api/sales/sales-order-lines/"
INVOICES = "/api/sales/invoices/"
PROFILES = "/api/sales/customer-profiles/"
QUOTATIONS = "/api/sales/quotations/"


def party(code, *roles):
    made = Party.objects.create(code=code, name=code.title())
    for role in roles:
        PartyRoleAssignment.objects.create(party=made, role=role)
    return made


class RepTestCase(SalesTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.rep_a = self.rep("rep-a")
        self.rep_b = self.rep("rep-b")
        self.acme = self.customer  # from SalesTestCase
        CustomerProfile.objects.create(party=self.acme, sales_rep=self.rep_a.employee.party)
        self.beta = party("BETA", PartyRole.CUSTOMER)
        CustomerProfile.objects.create(party=self.beta, sales_rep=self.rep_b.employee.party)
        self.gamma = party("GAMMA", PartyRole.CUSTOMER)
        self.vendor = party("VEND", PartyRole.VENDOR)
        self.acme_order = SalesOrder.objects.create(customer=self.acme, order_date="2026-09-01")
        self.beta_order = SalesOrder.objects.create(customer=self.beta, order_date="2026-09-01")
        self.gamma_order = SalesOrder.objects.create(customer=self.gamma, order_date="2026-09-01")

    def rep(self, name, active=True, linked=True):
        user = User.objects.create_user(name)
        user.groups.add(Group.objects.get(name="Sales Rep"))
        if linked:
            person = party(name.upper(), PartyRole.EMPLOYEE)
            Employee.objects.create(party=person, employee_number=name.upper(),
                                    hire_date=datetime.date(2026, 1, 1), user=user)
            SalesRep.objects.create(party=person, is_active=active)
        return user

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def as_role(self, role):
        user = User.objects.create_user(role.replace(" ", "-").lower())
        user.groups.add(Group.objects.get(name=role))
        return self.as_user(user)

    def codes(self, client, url, **query):
        response = client.get(url, {"page_size": 200, **query})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class RepReadsOnlyTheirOwnTests(RepTestCase):
    def test_parties_are_their_customers_and_every_non_customer(self):
        rows = self.codes(self.as_user(self.rep_a), PARTIES)
        codes = {row["code"] for row in rows}
        self.assertIn("CUST-1", codes)
        self.assertIn("VEND", codes)
        self.assertNotIn("BETA", codes)
        self.assertNotIn("GAMMA", codes)

    def test_another_reps_customer_is_not_found(self):
        client = self.as_user(self.rep_a)
        self.assertEqual(client.get(f"{PARTIES}{self.beta.pk}/").status_code, 404)
        self.assertEqual(client.get(f"{ORDERS}{self.beta_order.pk}/").status_code, 404)

    def test_orders_invoices_and_profiles_are_their_customers_only(self):
        client = self.as_user(self.rep_a)
        self.assertEqual([row["id"] for row in self.codes(client, ORDERS)], [self.acme_order.pk])
        Invoice.objects.create(customer=self.beta, invoice_date="2026-09-02",
                               receivable_account=self.receivable)
        mine = Invoice.objects.create(customer=self.acme, invoice_date="2026-09-02",
                                      receivable_account=self.receivable)
        self.assertEqual([row["id"] for row in self.codes(client, INVOICES)], [mine.pk])
        self.assertEqual([row["party"] for row in self.codes(client, PROFILES)], [self.acme.pk])

    def test_reports_count_only_their_customers(self):
        for customer in (self.acme, self.beta):
            self.customer = customer
            invoice = self.make_invoice()
            invoice.post()
        client = self.as_user(self.rep_a)
        revenue = self.codes(client, f"{INVOICES}revenue/", group_by="customer")
        self.assertEqual([row["key"] for row in revenue], [str(self.acme)])
        aging = self.codes(client, f"{INVOICES}aging/", as_of="2027-06-01")
        customers = {row["customer"] for bucket in aging.values() for row in bucket["invoices"]}
        self.assertEqual(customers, {str(self.acme)})

    def test_a_login_linked_to_nobody_sees_no_customer(self):
        client = self.as_user(self.rep("loose", linked=False))
        codes = {row["code"] for row in self.codes(client, PARTIES)}
        self.assertFalse(codes & {"CUST-1", "BETA", "GAMMA"})
        self.assertIn("VEND", codes)
        self.assertEqual(self.codes(client, ORDERS), [])

    def test_ar_manager_sees_every_customer(self):
        client = self.as_role("AR Manager")
        codes = {row["code"] for row in self.codes(client, PARTIES)}
        self.assertTrue({"CUST-1", "BETA", "GAMMA"} <= codes)
        self.assertEqual(len(self.codes(client, ORDERS)), 3)


class RepWritesOnlyTheirOwnTests(RepTestCase):
    def test_an_order_for_another_reps_customer_is_refused(self):
        response = self.as_user(self.rep_a).post(
            ORDERS, {"customer": self.beta.pk, "order_date": "2026-09-03"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["customer"], ["Not one of your customers."])
        self.assertFalse(SalesOrder.objects.filter(customer=self.beta, order_date="2026-09-03").exists())

    def test_an_order_cannot_be_moved_to_another_reps_customer(self):
        response = self.as_user(self.rep_a).patch(
            f"{ORDERS}{self.acme_order.pk}/", {"customer": self.gamma.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.acme_order.refresh_from_db()
        self.assertEqual(self.acme_order.customer, self.acme)

    def test_a_line_on_another_reps_order_is_refused(self):
        response = self.as_user(self.rep_a).post(ORDER_LINES, {
            "order": self.beta_order.pk, "item": self.item.pk, "description": "Sacks",
            "quantity": "1", "unit_price": "10",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.beta_order.lines.count(), 0)

    def test_a_quotation_for_another_reps_customer_is_refused(self):
        response = self.as_user(self.rep_a).post(
            QUOTATIONS, {"customer": self.beta.pk, "quotation_date": "2026-09-03"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("customer", response.json())

    def test_a_rep_changes_no_party_but_their_own_customers(self):
        response = self.as_user(self.rep_a).patch(
            f"{PARTIES}{self.vendor.pk}/", {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.vendor.refresh_from_db()
        self.assertEqual(self.vendor.name, "Vend")

    def test_an_address_for_another_reps_customer_is_refused(self):
        country = Country.objects.create(code="IN", name="India")
        response = self.as_user(self.rep_a).post(ADDRESSES, {
            "party": self.beta.pk, "line1": "1 Road", "city": "Pune", "country": country.pk,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Address.objects.filter(party=self.beta).exists())

    def test_a_rep_makes_customers_not_vendors(self):
        response = self.as_user(self.rep_a).post(
            PARTIES, {"code": "NEWV", "name": "New vendor", "role": "vendor"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Party.objects.filter(code="NEWV").exists())

    def test_a_rep_linked_to_nobody_makes_nothing(self):
        response = self.as_user(self.rep("loose", linked=False)).post(
            PARTIES, {"code": "NEWC", "name": "New", "role": "customer"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not linked to an employee", str(response.json()))
        self.assertFalse(Party.objects.filter(code="NEWC").exists())

    def test_an_inactive_rep_makes_no_customer(self):
        response = self.as_user(self.rep("idle", active=False)).post(
            PARTIES, {"code": "NEWC", "name": "New", "role": "customer"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Party.objects.filter(code="NEWC").exists())

    def test_a_customer_the_rep_makes_is_theirs(self):
        client = self.as_user(self.rep_a)
        response = client.post(PARTIES, {"code": "NEWC", "name": "New", "role": "customer"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = Party.objects.get(code="NEWC")
        self.assertEqual(made.customer_profile.sales_rep, self.rep_a.employee.party)
        self.assertEqual(client.get(f"{PARTIES}{made.pk}/").status_code, 200)
        # And the rep cannot see it from rep B's chair.
        self.assertEqual(self.as_user(self.rep_b).get(f"{PARTIES}{made.pk}/").status_code, 404)

    def test_a_new_order_takes_the_customers_rep(self):
        response = self.as_user(self.rep_a).post(
            ORDERS, {"customer": self.acme.pk, "order_date": "2026-09-03"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        order = SalesOrder.objects.get(pk=response.json()["id"])
        self.assertEqual(order.sales_rep, self.rep_a.employee.party)

    def test_a_credit_note_keeps_its_invoices_rep(self):
        invoice = self.make_invoice()  # Acme's, typed afresh: takes rep A
        self.assertEqual(invoice.sales_rep, self.rep_a.employee.party)
        invoice.sales_rep = None
        invoice.save()
        invoice.post()
        note = invoice.create_credit_note(memo="Torn bags")
        self.assertIsNone(note.sales_rep)


class AssigningRepsTests(RepTestCase):
    def test_a_customer_is_carried_only_by_an_active_rep(self):
        with self.assertRaises(ValidationError):
            CustomerProfile.objects.create(party=self.gamma, sales_rep=self.vendor)
        idle = self.rep("idle", active=False)
        with self.assertRaises(ValidationError):
            CustomerProfile.objects.create(party=self.gamma, sales_rep=idle.employee.party)

    def test_a_rep_who_has_left_does_not_stop_the_credit_limit_changing(self):
        SalesRep.objects.filter(party=self.rep_a.employee.party).update(is_active=False)
        profile = self.acme.customer_profile
        manager = self.as_role("AR Manager")
        response = manager.patch(
            f"{PROFILES}{profile.pk}/", {"credit_limit": "50000"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        # But nobody is given to them now.
        response = manager.patch(
            f"{PROFILES}{self.beta.customer_profile.pk}/",
            {"sales_rep": self.rep_a.employee.party.pk}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_the_ar_manager_assigns_and_the_rep_then_sees_it(self):
        manager = self.as_role("AR Manager")
        response = manager.post(PROFILES, {"party": self.gamma.pk,
                                           "sales_rep": self.rep_a.employee.party.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["sales_rep_name"], self.rep_a.employee.party.name)
        rows = self.codes(self.as_user(self.rep_a), PARTIES)
        self.assertIn("GAMMA", {row["code"] for row in rows})

    def test_a_rep_cannot_reassign_a_customer(self):
        profile = self.acme.customer_profile
        response = self.as_user(self.rep_a).patch(
            f"{PROFILES}{profile.pk}/", {"sales_rep": self.rep_b.employee.party.pk}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_every_role_that_reads_parties_but_the_rep_sees_every_customer(self):
        for role, permissions in ROLES.items():
            if "core.view_party" not in permissions:
                continue
            self.assertEqual("sales.view_every_customer" in permissions, role != "Sales Rep", role)
        every = Permission.objects.get(codename="view_every_customer")
        self.assertFalse(Group.objects.get(name="Sales Rep").permissions.filter(pk=every.pk).exists())
        self.assertTrue(Group.objects.get(name="Warehouse Staff").permissions.filter(pk=every.pk).exists())



class RepSideDoorTests(RepTestCase):
    """
    O84: rep A carries Acme, rep B carries Beta. Every report, history,
    profile and count that names a customer answered rep A with Beta as
    well; each asks the rep scope now (apps/core/scoping.py).
    """

    def posted_for(self, customer):
        self.customer = customer
        invoice = self.make_invoice()
        invoice.post()
        return invoice

    def test_the_claims_report_names_only_their_customers(self):
        from decimal import Decimal

        self.posted_for(self.beta).credit_claim(Decimal("10"), "torn")
        self.posted_for(self.acme).credit_claim(Decimal("10"), "torn")
        response = self.as_user(self.rep_a).get(f"{INVOICES}claims/", {"from": "2000-01-01", "to": "2100-12-31"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({row["customer"] for row in response.json()}, {self.acme.name})

    def bad_debts(self):
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        company = Company.get()
        company.bad_debt_account = Account.objects.create(code="6900", name="Bad debts",
                                                          account_type=AccountType.EXPENSE)
        company.save()
        self.posted_for(self.beta).write_off(reason="Gone under")
        self.posted_for(self.acme).write_off(reason="Gone under")

    def test_the_bad_debt_report_asks_its_own_view_right(self):
        self.bad_debts()
        self.assertEqual(self.as_user(self.rep_a).get("/api/sales/sales-reports/bad-debt/").status_code, 403)

    def test_the_bad_debt_report_names_only_their_customers(self):
        self.bad_debts()
        self.rep_a.user_permissions.add(Permission.objects.get(codename="view_invoicewriteoff"))
        response = self.as_user(User.objects.get(pk=self.rep_a.pk)).get("/api/sales/sales-reports/bad-debt/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["customer"] for row in response.json()], [str(self.acme)])
        everyone = self.as_role("Controller").get("/api/sales/sales-reports/bad-debt/").json()
        self.assertEqual(sorted(row["customer"] for row in everyone), sorted([str(self.acme), str(self.beta)]))

    def test_the_inbox_counts_only_their_overdue_invoices(self):
        self.posted_for(self.beta)  # dated 2026-01-01, due long ago
        rows = {row["key"]: row["count"] for row in self.as_user(self.rep_a).get("/api/web/inbox/").json()["rows"]}
        self.assertEqual(rows.get("invoices_overdue", 0), 0, rows)
        rows = {row["key"]: row["count"] for row in self.as_user(self.rep_b).get("/api/web/inbox/").json()["rows"]}
        self.assertEqual(rows.get("invoices_overdue", 0), 1, rows)

    def test_a_deleted_order_of_another_reps_customer_is_not_in_history(self):
        manager = self.as_role("AR Manager")
        pk = self.beta_order.pk
        self.assertEqual(manager.delete(f"{ORDERS}{pk}/").status_code, 204)
        response = self.as_user(self.rep_a).get("/api/core/history/", {"model": "sales.salesorder", "id": pk})
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(manager.get("/api/core/history/", {"model": "sales.salesorder", "id": pk}).status_code, 200)

    def test_tax_profiles_and_what_hangs_on_them_are_their_customers_only(self):
        from apps.accounting.models import PartyTaxProfile

        beta = PartyTaxProfile.objects.create(party=self.beta, pan="AAACB1234F")
        PartyTaxProfile.objects.create(party=self.acme, pan="AAACA1234F")
        client = self.as_user(self.rep_a)
        response = client.get("/api/accounting/party-tax-profiles/", {"search": "Beta"})
        self.assertEqual((response.status_code, [row["pan"] for row in response.json()]), (200, []))
        self.assertEqual([row["pan"] for row in client.get("/api/accounting/party-tax-profiles/").json()],
                         ["AAACA1234F"])
        self.assertEqual(client.get(f"/api/accounting/party-tax-profiles/{beta.pk}/").status_code, 404)
        for path in ("/api/core/notes/", "/api/core/attachments/", "/api/core/history/"):
            self.assertEqual(client.get(path, {"model": "accounting.partytaxprofile", "id": beta.pk}).status_code, 404,
                             path)

    def test_an_activity_on_another_reps_customer_is_refused(self):
        client = self.as_user(self.rep_a)
        refused = client.post("/api/sales/activities/", {"kind": "call", "party": self.beta.pk,
                                                          "summary": "Who is this?"}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        made = client.post("/api/sales/activities/", {"kind": "call", "party": self.acme.pk,
                                                       "summary": "Rang Acme"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)


class ConvertedLeadTests(RepTestCase):
    """O144: an unowned lead stayed unowned once rep B converted it, so rep A read B's new customer off it."""

    def test_rep_a_does_not_read_the_customer_rep_b_made_from_an_unowned_lead(self):
        from .crm import Lead

        lead = Lead.objects.create(company_name="Gamma Cement", contact_name="Mr G")
        converted = self.as_user(self.rep_b).post(f"/api/sales/leads/{lead.pk}/convert/", {"code": "GCEM"},
                                                  format="json")
        self.assertEqual(converted.status_code, 200, converted.content)
        rows = self.as_user(self.rep_a).get("/api/sales/leads/", {"page_size": 200}).json()
        self.assertEqual([row["company_name"] for row in rows if row["id"] == lead.pk], [])
        self.assertEqual(Lead.objects.get(pk=lead.pk).owner, self.rep_b.employee.party)


@tag("migration")
class EveryoneKeepsSeeingEveryCustomerMigrationTests(TransactionTestCase):
    """Upgraded without setup_roles, nobody but a Sales Rep is limited."""

    before = [("sales", "0045_opening_balance_flag")]
    after = [("sales", "0046_customer_sales_rep")]

    def test_groups_and_people_that_read_parties_are_given_it_but_the_rep(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        Group_ = apps.get_model("auth", "Group")
        Permission_ = apps.get_model("auth", "Permission")
        ContentType_ = apps.get_model("contenttypes", "ContentType")
        party_type, _ = ContentType_.objects.get_or_create(app_label="core", model="party")
        reads, _ = Permission_.objects.get_or_create(
            content_type=party_type, codename="view_party", defaults={"name": "Can view party"})
        for name in ("Sales Rep", "Warehouse Staff", "Station"):
            group = Group_.objects.create(name=name)
            if name != "Station":
                group.permissions.add(reads)
        clerk = apps.get_model("auth", "User").objects.create(username="clerk")
        clerk.user_permissions.add(reads)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        every = apps.get_model("auth", "Permission").objects.get(codename="view_every_customer")
        holders = sorted(apps.get_model("auth", "Group").objects.filter(
            permissions=every).values_list("name", flat=True))
        self.assertEqual(holders, ["Warehouse Staff"])
        self.assertTrue(apps.get_model("auth", "User").objects.filter(
            username="clerk", user_permissions=every).exists())
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
