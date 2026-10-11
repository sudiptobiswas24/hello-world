"""
What each role may see and do, asked of the API as a person in that role.

Before review every test that touched the API did so as a superuser, so
nothing here had ever been asked: an operator with only Employee Self
Service could read every payslip and the general ledger.
"""

from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from .management.commands import setup_roles

PAYSLIPS = "/api/hr/payslips/"
LEDGER = "/api/accounting/journal-entries/"
STATEMENTS = "/api/accounting/financial-statements/profit-and-loss/"
WORK_ORDERS = "/api/manufacturing/work-orders/"
SPECIFICATIONS = "/api/manufacturing/bag-specifications/"
ENTRIES = "/api/manufacturing/production-entries/"
FORECASTS = "/api/planning/forecasts/"
PLANNED = "/api/planning/planned-orders/"
INSPECTIONS = "/api/quality/inspections/"
PLANS = "/api/quality/plans/"
COMPLAINTS = "/api/manufacturing/complaints/"
INVOICES = "/api/sales/invoices/"
BILLS = "/api/purchasing/bills/"
ITEMS = "/api/inventory/items/"
GSTR1 = "/api/gst/gstr1/?period=2026-09"
E_INVOICES = "/api/gst/e-invoices/"


class RolesTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def as_(self, *roles):
        user = User.objects.create_user("-".join(roles).replace(" ", "_") or "nobody")
        user.groups.add(*Group.objects.filter(name__in=roles))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def can_read(self, client, url):
        return client.get(url).status_code != 403

    def assertReads(self, client, *urls):
        # 200, or 400 for a report missing a parameter: past the door
        # either way. A 404 is a mistyped address, not a permission.
        self.assertEqual([(url, code) for url in urls
                          if (code := client.get(url).status_code) not in (200, 400)], [])

    def assertRefused(self, client, *urls):
        self.assertEqual([url for url in urls if self.can_read(client, url)], [])


class TheRolesExistTests(RolesTestCase):
    def test_the_plants_roles(self):
        self.assertTrue({"Production Supervisor", "Station", "Production Planner",
                         "Quality Inspector", "Quality Manager", "GST Officer"}
                        <= set(Group.objects.values_list("name", flat=True)))

    def test_a_misspelt_permission_stops_it_and_changes_nothing(self):
        before = set(Group.objects.get(name="Station").permissions.values_list("codename",
                                                                                flat=True))
        wrong = {**setup_roles.ROLES, "Station": ["manufacturing.weigh_at_statoin"]}
        with patch.object(setup_roles, "ROLES", wrong):
            with self.assertRaisesMessage(CommandError, "Station: manufacturing.weigh_at_statoin"):
                call_command("setup_roles", verbosity=0)
        after = set(Group.objects.get(name="Station").permissions.values_list("codename",
                                                                               flat=True))
        self.assertEqual(after, before)


class NobodyReadsWhatIsNotTheirsTests(RolesTestCase):
    def test_a_login_alone_reads_nothing(self):
        self.assertRefused(self.as_(), PAYSLIPS, LEDGER, INVOICES, ITEMS, WORK_ORDERS,
                           STATEMENTS)

    def test_self_service_reads_no_payslips_and_no_ledger(self):
        self.assertRefused(self.as_("Employee Self Service"), PAYSLIPS, LEDGER, INVOICES,
                           STATEMENTS)

    def test_no_plant_role_reads_pay_or_the_ledger(self):
        for role in ("Production Supervisor", "Station", "Production Planner",
                     "Quality Inspector", "Quality Manager"):
            with self.subTest(role=role):
                self.assertRefused(self.as_(role), PAYSLIPS, LEDGER, STATEMENTS)


class ProductionTests(RolesTestCase):
    def test_the_supervisor_runs_the_floor(self):
        client = self.as_("Production Supervisor")
        self.assertReads(client, WORK_ORDERS, ENTRIES, SPECIFICATIONS, ITEMS)

    def test_but_does_not_change_what_was_specified(self):
        client = self.as_("Production Supervisor")
        self.assertEqual(client.post(SPECIFICATIONS, {}, format="json").status_code, 403)
        self.assertRefused(client, INVOICES, BILLS)

    def test_the_station_login_does_nothing_but_the_station(self):
        client = self.as_("Station")
        self.assertRefused(client, WORK_ORDERS, ENTRIES, ITEMS, INVOICES)
        user = User.objects.get(username="Station")
        self.assertTrue(user.has_perm("manufacturing.weigh_at_station"))


class PlanningTests(RolesTestCase):
    def test_the_planner_plans(self):
        self.assertReads(self.as_("Production Planner"), FORECASTS, PLANNED, WORK_ORDERS,
                         "/api/sales/sales-orders/", SPECIFICATIONS)

    def test_but_records_nothing_the_floor_did(self):
        client = self.as_("Production Planner")
        self.assertEqual(client.post(ENTRIES, {}, format="json").status_code, 403)


class QualityTests(RolesTestCase):
    def test_the_inspector_inspects(self):
        self.assertReads(self.as_("Quality Inspector"), INSPECTIONS, PLANS, COMPLAINTS)

    def test_but_does_not_set_the_pass_mark(self):
        client = self.as_("Quality Inspector")
        self.assertEqual(client.post(PLANS, {}, format="json").status_code, 403)

    def test_the_manager_does(self):
        client = self.as_("Quality Manager")
        self.assertNotEqual(client.post(PLANS, {}, format="json").status_code, 403)


class GstTests(RolesTestCase):
    def test_the_officer_compiles_returns_from_what_was_booked(self):
        self.assertReads(self.as_("GST Officer"), GSTR1, E_INVOICES, INVOICES, BILLS)

    def test_but_books_nothing(self):
        client = self.as_("GST Officer")
        self.assertEqual(client.post(INVOICES, {}, format="json").status_code, 403)
        self.assertEqual(client.post(BILLS, {}, format="json").status_code, 403)

    def test_nobody_else_compiles_them(self):
        for role in ("Sales Rep", "Purchasing Clerk", "Production Supervisor"):
            with self.subTest(role=role):
                self.assertRefused(self.as_(role), GSTR1)


class OfficeRolesStillWorkTests(RolesTestCase):
    """Reading took no permission before; the roles had to be given it."""

    def test_a_rep_sees_the_items_they_sell(self):
        self.assertReads(self.as_("Sales Rep"), ITEMS, "/api/sales/sales-orders/")

    def test_the_warehouse_sees_what_to_receive_and_ship(self):
        self.assertReads(self.as_("Warehouse Staff"), "/api/sales/sales-orders/",
                         "/api/purchasing/purchase-orders/", ITEMS)


class ActingTakesMoreThanAddingTests(RolesTestCase):
    """
    An action that posts, sends or commits, asked by someone who may add
    the document but not do the thing. Each fell back to "add" before.
    """

    def holding(self, *names):
        from django.contrib.auth.models import Permission

        user = User.objects.create_user(f"holder-{User.objects.count()}")
        for name in names:
            app_label, codename = name.split(".")
            user.user_permissions.add(Permission.objects.get(
                content_type__app_label=app_label, codename=codename))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_depreciation_posts_so_it_takes_the_right_to_post(self):
        client = self.holding("assets.add_fixedasset", "assets.view_fixedasset")
        self.assertEqual(client.post("/api/assets/assets/depreciate-all/").status_code, 403)

    def test_cancelling_an_order_is_changing_it(self):
        client = self.holding("sales.add_salesorder", "sales.view_salesorder")
        self.assertEqual(client.post("/api/sales/sales-orders/1/cancel/").status_code, 403)

    def test_writing_to_every_customer_is_not_setting_up_levels(self):
        client = self.holding("sales.add_dunninglevel", "sales.view_dunninglevel",
                              "sales.view_invoice")
        self.assertEqual(client.post("/api/sales/dunning-levels/run/").status_code, 403)
        self.assertEqual(client.post("/api/sales/sales-reports/statements/").status_code, 403)

    def test_drawing_consignment_buys_goods(self):
        client = self.holding("purchasing.view_purchaseorder", "purchasing.add_purchaseorder")
        self.assertEqual(client.post(
            "/api/purchasing/purchasing-reports/draw-consignment/").status_code, 403)
        self.assertEqual(client.post(
            "/api/purchasing/purchasing-reports/raise-reorder-requisition/").status_code, 403)

    def test_a_report_says_what_reading_it_takes(self):
        self.assertEqual(self.holding().get(STATEMENTS).status_code, 403)
        self.assertEqual(self.holding("accounting.view_journalentry").get(
            "/api/accounting/financial-statements/trial-balance/").status_code, 200)


class AViewThatForgetsTests(TestCase):
    def test_refuses_everybody_even_the_superuser(self):
        from types import SimpleNamespace

        from apps.core.permissions import RequiredPermission

        boss = User.objects.create_superuser("boss")
        request = SimpleNamespace(user=boss)
        self.assertFalse(RequiredPermission().has_permission(request, SimpleNamespace()))
        self.assertTrue(RequiredPermission().has_permission(
            request, SimpleNamespace(required_permission="sales.view_invoice")))
