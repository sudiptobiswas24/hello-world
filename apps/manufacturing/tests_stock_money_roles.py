"""
O348: what stock and a run are worth, hidden from whoever may not see it
(HidesStockMoney, inventory.view_stock_value), asked as the people in
their roles. An item's standard_cost x its quantity is the stock's worth;
a run's planned and booked costs are its worth on the floor.

The brief named a Machine Operator and a Shift Supervisor: setup_roles has
neither, so every role that reads items or runs without the right is asked
(the Quality Inspector, Process Engineer and Warehouse Staff among them),
against the Production Supervisor and Maintenance, who keep it (g23).
"""
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.management.commands.setup_roles import ROLES
from apps.inventory.serializers import ITEM_MONEY

from .serializers import OPERATION_MONEY, WORK_ORDER_MONEY
from .tests_orders import RunTestCase

ITEMS = "/api/inventory/items/"
RUNS = "/api/manufacturing/work-orders/"


def holds(role, right):
    return right in ROLES[role]


class StockMoneyByRoleTests(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.run_ = self.order()
        self.run_.release()

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def item_rows(self, client):
        listed = client.get(ITEMS, {"page_size": 200})
        detail = client.get(f"{ITEMS}{self.tape.pk}/")
        self.assertEqual((listed.status_code, detail.status_code), (200, 200))
        return [*listed.json(), detail.json()]

    def test_no_role_without_the_right_reads_an_items_standard_cost(self):
        blind = [role for role in ROLES if holds(role, "inventory.view_item")
                 and not holds(role, "inventory.view_stock_value")]
        self.assertTrue({"Quality Inspector", "Process Engineer", "Warehouse Staff"} <= set(blind), blind)
        seen = {role: sorted({field for row in self.item_rows(self.as_(role)) for field in ITEM_MONEY if field in row})
                for role in blind}
        self.assertEqual({role: fields for role, fields in seen.items() if fields}, {})

    def test_the_production_supervisor_and_maintenance_keep_it(self):
        for role in ("Production Supervisor", "Maintenance"):
            rows = self.item_rows(self.as_(role))
            self.assertTrue(all("standard_cost" in row for row in rows), role)
            self.assertEqual(rows[-1]["standard_cost"], str(self.tape.standard_cost) if self.tape.standard_cost
                             is not None else None, role)

    def test_a_quality_inspector_reads_the_run_but_not_what_it_cost(self):
        inspector = self.as_("Quality Inspector")
        listed = inspector.get(RUNS)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([field for row in listed.json() for field in WORK_ORDER_MONEY if field in row], [])
        detail = inspector.get(f"{RUNS}{self.run_.pk}/")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["number"], self.run_.number)
        self.assertEqual([field for field in WORK_ORDER_MONEY if field in body], [])
        self.assertEqual([field for row in body["operations"] for field in OPERATION_MONEY if field in row], [])
        self.assertEqual(inspector.get(f"{RUNS}{self.run_.pk}/variance/").status_code, 403)
        energy = inspector.get(f"{RUNS}{self.run_.pk}/energy/")
        self.assertEqual(energy.status_code, 200)
        self.assertNotIn("cost", energy.json())

    def test_every_role_that_reads_runs_without_the_right_sees_no_cost(self):
        blind = [role for role in ROLES if holds(role, "manufacturing.view_workorder")
                 and not holds(role, "inventory.view_stock_value")]
        for role in blind:
            body = self.as_(role).get(f"{RUNS}{self.run_.pk}/").json()
            self.assertEqual([field for field in WORK_ORDER_MONEY if field in body], [], role)

    def test_the_production_supervisor_keeps_the_runs_costs(self):
        supervisor = self.as_("Production Supervisor")
        body = supervisor.get(f"{RUNS}{self.run_.pk}/").json()
        self.assertEqual([field for field in WORK_ORDER_MONEY if field not in body], [])
        self.run_.refresh_from_db()
        self.assertEqual(body["planned_unit_cost"], str(self.run_.planned_unit_cost))
        self.assertEqual(supervisor.get(f"{RUNS}{self.run_.pk}/variance/").status_code, 200)
        self.assertIn("cost", supervisor.get(f"{RUNS}{self.run_.pk}/energy/").json())


class TheLedgerKeepersSeeTheStockFiguresTests(StockMoneyByRoleTests):
    """
    Decided in g23b (O348): the Controller and the Bookkeeper keep the ledger, the inventory account included,
    so they hold inventory.view_stock_value; no other office or floor role gains it.
    """

    def test_the_controller_and_the_bookkeeper_read_an_items_standard_cost_on_the_list_and_the_detail(self):
        from apps.inventory.models import Item

        # A figure on file, so the field carries one (the fixture's tape has none); the column holds 4 places.
        Item.objects.filter(pk=self.tape.pk).update(standard_cost="12.5000")
        for role in ("Controller", "Bookkeeper"):
            rows = self.item_rows(self.as_(role))
            self.assertTrue(all("standard_cost" in row for row in rows), role)
            self.assertEqual(rows[-1]["standard_cost"], "12.5000", role)
            self.assertIn("12.5000", [row["standard_cost"] for row in rows[:-1]], role)

    def test_they_do_not_read_runs_at_all_so_the_right_shows_them_no_run(self):
        # Neither role reads work orders (no manufacturing.view_workorder): the right adds no run to read.
        for role in ("Controller", "Bookkeeper"):
            client = self.as_(role)
            self.assertEqual(client.get(RUNS).status_code, 403, role)
            self.assertEqual(client.get(f"{RUNS}{self.run_.pk}/").status_code, 403, role)

    def test_the_roles_left_without_it_read_neither(self):
        stay = ("Process Engineer", "AP Manager", "Purchasing Clerk", "Sales Rep", "AR Manager", "Warehouse Staff",
                "Quality Inspector")
        self.assertEqual([role for role in stay if holds(role, "inventory.view_stock_value")], [])
        for role in stay:
            client = self.as_(role)
            self.assertEqual([row for row in self.item_rows(client) if "standard_cost" in row], [], role)
            if holds(role, "manufacturing.view_workorder"):
                listed, detail = client.get(RUNS).json(), client.get(f"{RUNS}{self.run_.pk}/").json()
                self.assertEqual([field for row in [*listed, detail] for field in WORK_ORDER_MONEY if field in row],
                                 [], role)
