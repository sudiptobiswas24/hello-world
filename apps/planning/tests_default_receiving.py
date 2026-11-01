"""
A purchase or requisition line naming no warehouse belongs to the default receiving warehouse only (O415).

mrp counted such a line as inbound to whichever warehouse it planned. On the demo plant the requisitions
and the polymer order name none (the screens never asked), so planning FG, the finished-goods godown, saw
them as its own supply with nothing there needing them, and advised cancelling them: a planner taking the
advice cancels the polymer the plant needs. Now such a line is the default receiving warehouse's
(Warehouse.default_receiving: the one marked, or the only active one), and go_live_check fails a plant with
two or more active warehouses and none marked.
"""

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from apps.inventory.models import Warehouse

from .models import RescheduleAction, SupplySource
from .tests_base import TODAY
from .tests_inbound import InboundTestCase


class WithNoneMarkedTests(InboundTestCase):
    def setUp(self):
        super().setUp()
        Warehouse.objects.filter(pk=self.plant.pk).update(receives_by_default=False)

    def test_two_warehouses_and_none_marked_count_it_in_neither(self):
        self.purchase("300", self.plant)
        self.purchase("200", self.godown)
        self.purchase("100")
        self.assertEqual((self.inbound(), self.inbound(self.godown)), (Decimal("300"), Decimal("200")))

    def test_one_active_warehouse_is_the_default_without_marking(self):
        Warehouse.objects.filter(pk=self.godown.pk).update(is_active=False)
        self.purchase("300", self.plant)
        self.purchase("100")
        self.assertEqual((Warehouse.default_receiving(), self.inbound()), (self.plant, Decimal("400")))

    def test_go_live_fails_two_active_warehouses_and_none_marked(self):
        from apps.imports.management.commands.go_live_check import FAIL, checks

        failed = [what for level, what, _detail in checks() if level == FAIL]
        self.assertIn("Default receiving warehouse", failed)
        self.plant.receives_by_default = True
        self.plant.save()
        failed = [what for level, what, _detail in checks() if level == FAIL]
        self.assertNotIn("Default receiving warehouse", failed)


class OneDefaultAtMostTests(InboundTestCase):
    def test_a_second_default_is_refused_in_words(self):
        self.godown.receives_by_default = True
        with self.assertRaisesMessage(ValidationError, f"{self.plant.code} is already the default receiving warehouse"):
            self.godown.save()

    def test_the_default_is_not_made_inactive(self):
        self.plant.is_active = False
        with self.assertRaisesMessage(ValidationError, "cannot be made inactive"):
            self.plant.save()

    def test_the_stores_manager_marks_it_over_the_api(self):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("stores")
        user.groups.add(Group.objects.get(name="Stores Manager"))
        client = APIClient()
        client.force_authenticate(user)
        Warehouse.objects.filter(pk=self.plant.pk).update(receives_by_default=False)
        response = client.patch(f"/api/inventory/warehouses/{self.godown.pk}/", {"receives_by_default": True},
                                format="json")
        self.assertEqual((response.status_code, response.json().get("receives_by_default")), (200, True),
                         response.content)
        refused = client.patch(f"/api/inventory/warehouses/{self.plant.pk}/", {"receives_by_default": True},
                               format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("G2 is already the default receiving warehouse", refused.content.decode())


class TheDemoPlantsGodownTests(TestCase):
    """The planning battery's case, on the demo plant: planning FG advises cancelling none of the plant's buying."""

    def test_planning_fg_does_not_cancel_the_plant_s_requisitions_or_polymer(self):
        from .mrp import plan

        with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
            executor.return_value.migration_plan.return_value = []
            call_command("load_demo", password="Trial-2026", stdout=StringIO(), stderr=StringIO())
        godown, plant = Warehouse.objects.get(code="FG"), Warehouse.objects.get(code="PLANT")
        run = plan(godown)
        cancelled = sorted((action.item.sku, action.source) for action in run.actions.all()
                           if action.action == RescheduleAction.CANCEL
                           and action.source in (SupplySource.PURCHASE, SupplySource.REQUISITION))
        self.assertEqual(cancelled, [])
        # The same lines are still the plant's supply: its own run counts them.
        from apps.purchasing.models import PurchaseRequisitionLine

        unplaced = PurchaseRequisitionLine.objects.filter(warehouse__isnull=True).select_related("item")
        self.assertTrue(unplaced.exists())
        from .mrp import requisition_supply

        line = unplaced.first()
        self.assertTrue(requisition_supply(line.item, plant, TODAY) or line.requisition.status in ("rejected", "cancelled"))
        self.assertEqual(requisition_supply(line.item, godown, TODAY), [])
