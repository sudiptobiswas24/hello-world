"""
A customer sends back part of what came: twenty of fifty bags torn in
transit, and later the rest. Purchasing could always return part of a
receipt; a delivery could only be returned whole, and once, so twenty
torn bags could not be recorded at all. Found testing the return paths
end to end.

Fifty shipped at 25.00 and invoiced, 1,250.00. Twenty back: a credit
note of 500.00 and 750.00 still due. The other thirty: 750.00 more, and
nothing due.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.reports import reconcile_to_ledger

from .tests_base import SalesTestCase


class PartOfADeliveryComesBackTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.gap = reconcile_to_ledger()["difference"]
        self.order = self.make_order("50", "25")
        self.shipment = self.ship(self.order, "50")
        self.invoice = self.bill(self.order)
        self.line = self.shipment.lines.get()

    def test_refused_beyond_what_is_left(self):
        with self.assertRaisesMessage(ValidationError, "Only 50"):
            self.shipment.create_return(quantities={self.line: Decimal("51")})
        self.shipment.create_return(quantities={self.line: Decimal("20")})
        with self.assertRaisesMessage(ValidationError, "Only 30"):
            self.shipment.create_return(quantities={self.line: Decimal("31")})

    def test_refused_for_a_line_of_another_delivery(self):
        other = self.ship(self.make_order("5", "25"), "5")
        with self.assertRaisesMessage(ValidationError, "different delivery"):
            self.shipment.create_return(quantities={other.lines.get(): Decimal("1")})

    def test_part_then_the_rest(self):
        back = self.shipment.create_return(quantities={self.line: Decimal("20")})
        (note,) = back.credit_notes_created
        self.assertEqual((note.total(), self.invoice.amount_due()),
                         (Decimal("500.00"), Decimal("750.00")))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("470"))
        rest = self.shipment.create_return()
        self.assertEqual(rest.lines.get().quantity_shipped, Decimal("30"))
        self.assertEqual(self.invoice.amount_due(), Decimal("0.00"))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("500"))
        self.assertEqual(reconcile_to_ledger()["difference"], self.gap)
        with self.assertRaisesMessage(ValidationError, "nothing left on this delivery"):
            self.shipment.create_return()

    def test_over_the_api_too(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("store"))
        response = client.post(f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
                               {"quantities": {str(self.line.pk): "20"}}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["credit_notes"][0]["total"], "500.00")
        self.assertEqual(client.post(
            f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
            {"quantities": {str(self.line.pk): "31"}}, format="json").status_code, 400)


class BatchesGoHomeTests(SalesTestCase):
    """
    Thirty from batch A and twenty from batch B went out on one line.
    Twenty-five back, then twenty-five more: A gets its thirty and B its
    twenty, not fifty into A because each return began again at the top.
    """

    def test_each_batch_gets_back_what_left_it(self):
        from django.utils import timezone

        from apps.inventory.models import Item, Lot, MovementType, StockMovement

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        a = Lot.objects.create(item=self.item, code="A")
        b = Lot.objects.create(item=self.item, code="B")
        for lot, quantity in ((a, "30"), (b, "20")):
            StockMovement.objects.create(item=self.item, warehouse=self.warehouse, lot=lot,
                                         movement_type=MovementType.RECEIPT, uom=self.uom,
                                         quantity=Decimal(quantity), unit_cost=Decimal("4"),
                                         occurred_at=timezone.now())
        from .models import Delivery, DeliveryAllocation, DeliveryLine

        order = self.make_order("50", "25")
        shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        line = DeliveryLine.objects.create(delivery=shipment, order_line=order.lines.get(),
                                           warehouse=self.warehouse,
                                           quantity_shipped=Decimal("50"))
        shipment.post()
        sent = {row.lot.code: row.quantity for row in DeliveryAllocation.objects.filter(line=line)}
        before = {lot.code: lot.on_hand_at(self.warehouse) for lot in (a, b)}
        shipment.create_return(quantities={line: Decimal("25")}, credit_invoices=False)
        shipment.create_return(quantities={line: Decimal("25")}, credit_invoices=False)
        after = {lot.code: lot.on_hand_at(self.warehouse) for lot in (a, b)}
        self.assertEqual({code: after[code] - before[code] for code in after}, sent)

    def test_a_batch_shipped_on_a_posted_delivery_is_not_deleted(self):
        """O1 in docs/RISKS.md: the refusal read a field the allocation does not have."""
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        from apps.inventory.models import Item, Lot, MovementType, StockMovement

        from .models import Delivery, DeliveryAllocation, DeliveryLine

        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        lot = Lot.objects.create(item=self.item, code="A")
        StockMovement.objects.create(item=self.item, warehouse=self.warehouse, lot=lot,
                                     movement_type=MovementType.RECEIPT, uom=self.uom,
                                     quantity=Decimal("30"), unit_cost=Decimal("4"), occurred_at=timezone.now())
        order = self.make_order("30", "25")
        shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        line = DeliveryLine.objects.create(delivery=shipment, order_line=order.lines.get(),
                                           warehouse=self.warehouse, quantity_shipped=Decimal("30"))
        shipment.post()
        allocation = DeliveryAllocation.objects.get(line=line)

        with self.assertRaisesMessage(ValidationError, "Create a customer return instead"):
            allocation.delete()
        self.assertTrue(DeliveryAllocation.objects.filter(pk=allocation.pk).exists())


class ReturnedAtWhatTheShipmentTookTests(SalesTestCase):
    """
    A thousand at 4 and two thousand at 3: three thousand worth 10,000,
    averaging 3.3333... Shipped whole, cost of sales took 10,000.00.
    Returned at the line's four-place rate, 3,000 at 3.3333 put back
    9,999.90, and 0.10 of a shipment that came back entirely stayed in
    cost of sales. The total goes back, not a rate multiplied up.
    """

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        from apps.inventory.models import Item, MovementType, StockMovement

        from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine

        self.plain = Item.objects.create(sku="PLAIN", name="Plain", uom=self.uom)
        for quantity, cost in (("1000", "4"), ("2000", "3")):
            StockMovement.objects.create(item=self.plain, warehouse=self.warehouse,
                                         movement_type=MovementType.RECEIPT, uom=self.uom,
                                         quantity=Decimal(quantity), unit_cost=Decimal(cost),
                                         occurred_at=timezone.now())
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        order_line = SalesOrderLine.objects.create(order=order, item=self.plain, uom=self.uom,
                                                   quantity=Decimal("3000"), unit_price=Decimal("5"),
                                                   revenue_account=self.revenue)
        order.confirm()
        self.shipment = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        self.line = DeliveryLine.objects.create(delivery=self.shipment, order_line=order_line,
                                                warehouse=self.warehouse,
                                                quantity_shipped=Decimal("3000"))
        self.shipment.post()

    def test_returned_whole_nothing_stays_in_cost_of_sales(self):
        self.shipment.create_return(credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))

    def test_returned_whole_the_shelf_is_worth_what_it_was(self):
        self.shipment.create_return(credit_invoices=False)
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_returned_in_two_parts_it_still_comes_to_nothing(self):
        self.shipment.create_return(quantities={self.line: Decimal("1000")}, credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("6666.67"))
        self.shipment.create_return(quantities={self.line: Decimal("2000")}, credit_invoices=False)
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_returned_in_three_parts_the_last_takes_what_is_left(self):
        """Each part put back 3,333.33 on its own reckoning, and 0.01 stayed in cost of sales."""
        balances = []
        for _part in range(3):
            self.shipment.create_return(quantities={self.line: Decimal("1000")}, credit_invoices=False)
            balances.append(self.balance(self.cogs))
        self.assertEqual(balances, [Decimal("6666.67"), Decimal("3333.33"), Decimal("0.00")])
        self.assertEqual(self.plain.stock_value_at(self.warehouse), Decimal("10000.00"))

    def test_taken_back_by_the_store_through_the_api(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        store = User.objects.create_user("store")
        store.groups.add(Group.objects.get(name="Warehouse Staff"))
        client = APIClient()
        client.force_authenticate(store)
        response = client.post(f"/api/sales/deliveries/{self.shipment.pk}/customer_return/",
                               {"credit_invoices": False}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.balance(self.cogs), Decimal("0.00"))
