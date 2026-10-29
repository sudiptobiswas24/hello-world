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
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.management.commands.setup_roles import ROLES
from apps.inventory.serializers import ITEM_MONEY

from .serializers import OPERATION_MONEY, WORK_ORDER_MONEY, WorkOrderListSerializer
from .tests_orders import RunTestCase

ITEMS = "/api/inventory/items/"
RUNS = "/api/manufacturing/work-orders/"
MOVEMENTS = "/api/inventory/stock-movements/"
STOCK_REPORTS = "/api/inventory/stock-reports/"


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

    def test_they_read_the_stock_value_report_and_the_stock_ledger(self):
        """
        O376: the right to the figures alone reached no report, which reads the stock ledger. Both roles read
        the ledger now (inventory.view_stockmovement): the valuation answers what the shelf is worth, worked
        by hand in calc_fix_g23b2/o376_stock_value.py (2000 x 100 + 500 x 60 + 300 x 30 + 100 x 200).
        """
        for role in ("Controller", "Bookkeeper"):
            client = self.as_(role)
            valuation = client.get(f"{STOCK_REPORTS}valuation/")
            self.assertEqual(valuation.status_code, 200, role)
            self.assertEqual(valuation.json()["total_value"], "259000.00", role)
            self.assertEqual(client.get(f"{STOCK_REPORTS}reconciliation/").status_code, 200, role)
            listed = client.get(MOVEMENTS)
            self.assertEqual((listed.status_code, len(listed.json())), (200, 4), role)
            first = listed.json()[0]["id"]
            self.assertEqual(client.get(f"{MOVEMENTS}{first}/").status_code, 200, role)

    def test_they_read_a_runs_costs_on_the_list_and_the_detail(self):
        """O376: both read the runs (manufacturing.view_workorder), and with the figures, what a run cost."""
        self.run_.refresh_from_db()
        for role in ("Controller", "Bookkeeper"):
            client = self.as_(role)
            listed = client.get(RUNS)
            self.assertEqual(listed.status_code, 200, role)
            row = next(row for row in listed.json() if row["id"] == self.run_.pk)
            detail = client.get(f"{RUNS}{self.run_.pk}/")
            self.assertEqual(detail.status_code, 200, role)
            body = detail.json()
            self.assertEqual([field for field in WORK_ORDER_MONEY if field not in body], [], role)
            self.assertEqual(body["planned_unit_cost"], str(self.run_.planned_unit_cost), role)
            # The list shows what its serializer carries of the run's money, and hides none of it from them.
            listed_money = [field for field in WORK_ORDER_MONEY if field in WorkOrderListSerializer.Meta.fields]
            self.assertEqual([field for field in listed_money if field not in row], [], role)
            self.assertEqual(client.get(f"{RUNS}{self.run_.pk}/variance/").status_code, 200, role)

    def test_they_cannot_make_change_post_or_delete_a_run_or_a_movement(self):
        """O376: read only. No run is made, changed, released, closed or deleted; no movement written."""
        from apps.inventory.models import StockMovement
        from apps.manufacturing.models import WorkOrder, WorkOrderStatus

        movement = StockMovement.objects.order_by("pk").first()
        made = WorkOrder.objects.create(item=self.tape, bom=self.bom, quantity_ordered="100", uom=self.kg,
                                        warehouse=self.plant, work_centre=self.loom)
        for role in ("Controller", "Bookkeeper"):
            client = self.as_(role)
            asked = {
                "make a run": client.post(RUNS, {"item": self.tape.pk, "bom": self.bom.pk, "quantity_ordered": "5",
                                                 "uom": self.kg.pk, "warehouse": self.plant.pk}, format="json"),
                "change a run": client.patch(f"{RUNS}{made.pk}/", {"quantity_ordered": "7"}, format="json"),
                "release a run": client.post(f"{RUNS}{made.pk}/release/"),
                "close a run": client.post(f"{RUNS}{self.run_.pk}/close/"),
                "cancel a run": client.post(f"{RUNS}{made.pk}/cancel/"),
                "delete a run": client.delete(f"{RUNS}{made.pk}/"),
                "write a movement": client.post(MOVEMENTS, {"item": self.tape.pk, "warehouse": self.plant.pk,
                                                            "quantity": "1", "unit_cost": "1"}, format="json"),
                "change a movement": client.patch(f"{MOVEMENTS}{movement.pk}/", {"quantity": "1"}, format="json"),
                "delete a movement": client.delete(f"{MOVEMENTS}{movement.pk}/"),
            }
            self.assertEqual({what: answer.status_code for what, answer in asked.items() if answer.status_code != 403},
                             {}, role)
        made.refresh_from_db()
        self.assertEqual((made.status, made.quantity_ordered), (WorkOrderStatus.DRAFT, Decimal("100")))
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 4)
        movement.refresh_from_db()
        self.assertEqual(movement.quantity, Decimal("2000"))

    def test_no_other_office_role_gains_the_runs_or_the_ledger_by_it(self):
        """O376 gives the two ledger keepers read rights and nothing that writes."""
        for role in ("Controller", "Bookkeeper"):
            written = [right for right in ROLES[role]
                       if right.split(".")[1] in {f"{action}_{model}" for action in ("add", "change", "delete")
                                                  for model in ("stockmovement", "workorder")}]
            self.assertEqual(written, [], role)

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
