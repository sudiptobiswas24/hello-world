"""audit2_core probes for planning."""
from decimal import Decimal

from .tests_mrp import PlanningTestCase, TODAY


class RequisitionBecomesDraftOrderTests(PlanningTestCase):
    def test_a_requisition_turned_into_a_draft_order_is_still_expected_supply(self):
        """
        Plant figure: 1,000 kg of polymer wanted (reorder minimum), asked for by a requisition of 1,000 kg.
        The buyer turns it into a purchase order and has not confirmed it yet. The requisition is
        'net of what has been turned into an order' (requisition_supply) and a draft order is not
        supply (purchase_supply: confirmed only): the 1,000 kg is nowhere, and the plan asks for 1,000 again.
        """
        from apps.purchasing.models import (PurchaseOrder, PurchaseOrderLine, PurchaseRequisition,
                                            PurchaseRequisitionLine)

        self.rule(self.virgin, minimum="1000")
        requisition = PurchaseRequisition.objects.create(requested_by=self.buyer, request_date=TODAY, needed_by=TODAY)
        asked = PurchaseRequisitionLine.objects.create(requisition=requisition, item=self.virgin, uom=self.kg,
                                                       quantity=Decimal("1000"))
        self.assertEqual(self.orders(), {})            # as the existing test says
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=TODAY, currency=self.inr)
        PurchaseOrderLine.objects.create(order=order, item=self.virgin, uom=self.kg, quantity=Decimal("1000"),
                                         unit_price=Decimal("90"), expected_date=TODAY, requisition_line=asked)
        self.assertEqual(sorted(self.orders()), [])    # still 1,000 kg coming: no new planned buy


class PlannedOrderAfterFirmingTests(PlanningTestCase):
    def test_a_planner_cannot_change_or_delete_a_planned_order_after_it_became_a_work_order(self):
        """
        1,000 kg of fabric planned, firmed into a draft run of 1,000 kg. The suggestion is the record of why
        the run exists. Changing its quantity to 10 over the API, or deleting it (the plan then suggests
        1,000 kg again), leaves the run with no account of itself.
        """
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        from .models import PlannedOrder, PlannedOrderStatus

        call_command("setup_roles", verbosity=0)
        self.sell(self.fabric, "1000", self.day(30))
        run = self.plan()
        order = run.orders.get(item=self.fabric)
        order.firm()
        order.refresh_from_db()
        self.assertEqual(order.status, PlannedOrderStatus.FIRMED)
        user = User.objects.create_user("planner")
        user.groups.add(Group.objects.get(name="Production Planner"))
        client = APIClient()
        client.force_authenticate(user)
        changed = client.patch(f"/api/planning/planned-orders/{order.pk}/", {"quantity": "10"}, format="json")
        deleted = client.delete(f"/api/planning/planned-orders/{order.pk}/")
        self.assertEqual((changed.status_code, deleted.status_code), (400, 400),
                         (changed.status_code, deleted.status_code))
        self.assertTrue(PlannedOrder.objects.filter(pk=order.pk).exists())


class PromiseQuestionTests(PlanningTestCase):
    def test_a_rep_who_mistypes_the_date_or_the_quantity_is_told_so_not_given_a_server_fault(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient(raise_request_exception=False)
        client.force_authenticate(User.objects.create_superuser("boss", "b@example.com", "x"))
        base = f"/api/planning/promise/?item={self.fabric.pk}&warehouse={self.plant.pk}"
        when = f"/api/planning/promise/when/?item={self.fabric.pk}&warehouse={self.plant.pk}"
        answers = {
            "date=banana": client.get(base + "&on=banana").status_code,
            "qty=Infinity": client.get(when + "&quantity=Infinity").status_code,
            "qty=NaN": client.get(when + "&quantity=NaN").status_code,
            "qty=-5": client.get(when + "&quantity=-5").status_code,
            "qty=0": client.get(when + "&quantity=0").status_code,
        }
        print("PROMISE ANSWERS", answers)
        self.assertEqual({k: v for k, v in answers.items() if v >= 500}, {})
