"""
How many places goods pass through on the way in.

A warehouse that inspects everything it receives had to be remembered
on every order line, and there was no receiving bay at all — goods went
straight from the lorry to the shelf they would eventually be picked
from, which is not how a building with a loading dock works.

The route is declared on the warehouse now, because it is a fact about
the building. The quality step is the quarantine and inspection flow
that already existed rather than a second one beside it, and the hops
are StockTransfers rather than a private set of movements.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, AccountType
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)
from apps.inventory.models import Item, Warehouse

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
)


class RoutingTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.inventory = acc("1200", "Inventory", AccountType.ASSET)
        self.cogs = acc("5000", "Cost of sales", AccountType.EXPENSE)
        self.grni = acc("2150", "GRNI", AccountType.LIABILITY)
        self.purchases = acc("5300", "Purchases", AccountType.EXPENSE)
        Company.objects.create(
            name="Test Co", base_currency=self.usd,
            default_inventory_account=self.inventory,
            default_cogs_account=self.cogs, grni_account=self.grni,
            default_purchase_expense_account=self.purchases,
        )
        self.item = Item.objects.create(sku="W", name="Widget", uom=self.each)
        self.stock_room = Warehouse.objects.create(code="STK", name="Stock")
        self.bay = Warehouse.objects.create(code="BAY", name="Receiving bay")
        self.qa = Warehouse.objects.create(
            code="QA", name="Inspection", is_quarantine=True
        )
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def two_step(self):
        self.stock_room.receipt_route = "input"
        self.stock_room.input_warehouse = self.bay
        self.stock_room.save()

    def three_step(self):
        self.stock_room.receipt_route = "inspect"
        self.stock_room.input_warehouse = self.bay
        self.stock_room.quality_warehouse = self.qa
        self.stock_room.save()

    def receive(self, quantity="40", warehouse=None):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.each,
            quantity=Decimal(quantity), unit_price=Decimal("5"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        receipt_line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=line,
            warehouse=warehouse or self.stock_room,
            quantity_received=Decimal(quantity),
        )
        receipt.post()
        receipt_line.refresh_from_db()
        return receipt, receipt_line


class DeclaringTheRouteTests(RoutingTestCase):
    def test_a_warehouse_receives_straight_to_stock_by_default(self):
        # What every warehouse did before this, and what most still do.
        self.assertEqual(self.stock_room.receipt_steps(), [self.stock_room])

    def test_a_two_step_route_passes_through_the_bay(self):
        self.two_step()
        self.assertEqual(
            [w.code for w in self.stock_room.receipt_steps()], ["BAY", "STK"]
        )

    def test_a_three_step_route_passes_through_inspection(self):
        self.three_step()
        self.assertEqual(
            [w.code for w in self.stock_room.receipt_steps()], ["BAY", "QA", "STK"]
        )

    def test_a_route_must_say_where_its_bay_is(self):
        self.stock_room.receipt_route = "input"
        with self.assertRaises(ValidationError) as caught:
            self.stock_room.save()
        self.assertIn("has not said which", str(caught.exception))

    def test_an_inspecting_route_must_say_where(self):
        self.stock_room.receipt_route = "inspect"
        self.stock_room.input_warehouse = self.bay
        with self.assertRaises(ValidationError) as caught:
            self.stock_room.save()
        self.assertIn("has not said where", str(caught.exception))

    def test_the_inspection_area_has_to_be_quarantine(self):
        # A quality step anybody can pick from is not a quality step.
        open_shelf = Warehouse.objects.create(code="OPEN", name="Open")
        self.stock_room.receipt_route = "inspect"
        self.stock_room.input_warehouse = self.bay
        self.stock_room.quality_warehouse = open_shelf
        with self.assertRaises(ValidationError) as caught:
            self.stock_room.save()
        self.assertIn("could be shipped before anyone looked", str(caught.exception))

    def test_a_warehouse_cannot_be_its_own_bay(self):
        self.stock_room.receipt_route = "input"
        self.stock_room.input_warehouse = self.stock_room
        with self.assertRaises(ValidationError) as caught:
            self.stock_room.save()
        self.assertIn("its own", str(caught.exception))

    def test_a_bay_cannot_itself_route_onwards(self):
        # Otherwise goods route into a bay that routes them into another,
        # and nothing says where they stop.
        other = Warehouse.objects.create(code="OTH", name="Other")
        self.bay.receipt_route = "input"
        self.bay.input_warehouse = other
        self.bay.save()
        self.stock_room.receipt_route = "input"
        self.stock_room.input_warehouse = self.bay
        with self.assertRaises(ValidationError) as caught:
            self.stock_room.save()
        self.assertIn("route into it forever", str(caught.exception))


class GoodsLandInTheBayTests(RoutingTestCase):
    def test_a_direct_route_is_unchanged(self):
        _receipt, line = self.receive("40")
        self.assertEqual(line.arrived_at(), self.stock_room)
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("40"))

    def test_a_two_step_route_lands_them_in_the_bay(self):
        self.two_step()
        _receipt, line = self.receive("40")
        self.assertEqual(line.arrived_at(), self.bay)
        self.assertEqual(self.item.on_hand_at(self.bay), Decimal("40"))
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("0"))

    def test_goods_in_the_bay_are_owned_and_valued(self):
        # They were received. The purchase happened whether or not
        # anybody has put them away.
        self.two_step()
        self.receive("40")
        self.assertEqual(self.item.stock_value_at(self.bay), Decimal("200.00"))

    def test_putting_them_away_moves_them_on(self):
        self.two_step()
        _receipt, line = self.receive("40")
        line.advance()
        self.assertEqual(self.item.on_hand_at(self.bay), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("40"))

    def test_putting_them_away_moves_no_value_between_accounts(self):
        from apps.accounting.models import JournalLine

        self.two_step()
        _receipt, line = self.receive("40")
        before = sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(
                account=self.inventory
            )),
            Decimal("0"),
        )
        line.advance()
        after = sum(
            (l.debit - l.credit for l in JournalLine.objects.filter(
                account=self.inventory
            )),
            Decimal("0"),
        )
        self.assertEqual(after, before)

    def test_half_a_pallet_can_be_put_away(self):
        self.two_step()
        _receipt, line = self.receive("40")
        line.advance(quantity=Decimal("15"))
        self.assertEqual(line.quantity_at(self.bay), Decimal("25"))
        self.assertEqual(line.quantity_at(self.stock_room), Decimal("15"))
        self.assertEqual(self.item.on_hand_at(self.bay), Decimal("25"))

    def test_the_rest_can_follow(self):
        self.two_step()
        _receipt, line = self.receive("40")
        line.advance(quantity=Decimal("15"))
        line.advance()
        self.assertEqual(line.quantity_at(self.bay), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("40"))

    def test_more_than_is_in_the_bay_cannot_be_put_away(self):
        self.two_step()
        _receipt, line = self.receive("40")
        with self.assertRaises(ValidationError) as caught:
            line.advance(quantity=Decimal("50"))
        self.assertIn("cannot move", str(caught.exception))

    def test_goods_already_at_the_end_have_nowhere_to_go(self):
        _receipt, line = self.receive("40")
        with self.assertRaises(ValidationError) as caught:
            line.advance()
        self.assertIn("end of its route", str(caught.exception))

    def test_an_unposted_receipt_has_nothing_to_move(self):
        self.two_step()
        order = PurchaseOrder.objects.create(
            vendor=self.vendor, order_date=datetime.date(2026, 1, 1)
        )
        order_line = PurchaseOrderLine.objects.create(
            order=order, item=self.item, uom=self.each,
            quantity=Decimal("10"), unit_price=Decimal("5"),
        )
        order.confirm()
        receipt = GoodsReceipt.objects.create(
            purchase_order=order, receipt_date=datetime.date(2026, 1, 2)
        )
        line = GoodsReceiptLine.objects.create(
            receipt=receipt, order_line=order_line, warehouse=self.stock_room,
            quantity_received=Decimal("10"),
        )
        with self.assertRaises(ValidationError) as caught:
            line.advance()
        self.assertIn("Only a posted receipt", str(caught.exception))

    def test_each_hop_is_a_transfer(self):
        # Reusing StockTransfer means the reverse path, the batch and bin
        # handling and the value arithmetic are the ones already tested.
        self.two_step()
        _receipt, line = self.receive("40")
        move = line.advance()
        self.assertEqual(move.transfer.from_warehouse, self.bay)
        self.assertEqual(move.transfer.to_warehouse, self.stock_room)
        self.assertEqual(move.transfer.status, "received")


class ThroughInspectionTests(RoutingTestCase):
    def test_goods_stop_at_inspection_on_the_way_through(self):
        self.three_step()
        _receipt, line = self.receive("40")
        line.advance()
        self.assertEqual(self.item.on_hand_at(self.qa), Decimal("40"))
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("0"))

    def test_stock_waiting_to_be_looked_at_is_not_available(self):
        self.three_step()
        _receipt, line = self.receive("40")
        line.advance()
        self.assertEqual(self.item.available_at(self.qa), 0)
        self.assertEqual(self.item.on_hand_at(self.qa), Decimal("40"))

    def test_they_do_not_simply_move_on_from_inspection(self):
        # Goods leave quarantine when somebody has looked at them, which
        # is a decision with its own record.
        self.three_step()
        _receipt, line = self.receive("40")
        line.advance()
        with self.assertRaises(ValidationError) as caught:
            line.advance()
        self.assertIn("awaiting inspection", str(caught.exception))

    def test_the_receipt_knows_what_is_waiting(self):
        self.three_step()
        receipt, line = self.receive("40")
        line.advance()
        line.refresh_from_db()
        self.assertEqual(
            [str(row) for row in receipt.quarantined_lines()], [str(line)]
        )

    def test_a_line_that_landed_in_quarantine_directly_still_counts(self):
        # The original per-order-line route, unchanged.
        receipt, _line = self.receive("40", warehouse=self.qa)
        self.assertEqual(len(receipt.quarantined_lines()), 1)


class ReturningFromWhereTheGoodsAreTests(RoutingTestCase):
    """
    A return has to take goods off the shelf they are really on, not the
    one the line says they were destined for.
    """

    def test_a_return_of_goods_still_in_the_bay(self):
        self.two_step()
        receipt, _line = self.receive("40")
        receipt.create_return()
        self.assertEqual(self.item.on_hand_at(self.bay), Decimal("0"))
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("0"))

    def test_a_direct_route_returns_as_it_always_did(self):
        receipt, _line = self.receive("40")
        receipt.create_return()
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("0"))


class RoutingIsReachableTests(RoutingTestCase):
    """Built and wired in the same sitting, rather than after somebody asks."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        user = get_user_model().objects.create_superuser(
            username="goods", email="g@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_a_warehouse_exposes_its_route(self):
        self.three_step()
        response = self.client.get(
            f"/api/inventory/warehouses/{self.stock_room.pk}/"
        )
        self.assertEqual(response.data["receipt_route"], "inspect")
        self.assertEqual(response.data["input_warehouse"], self.bay.pk)
        self.assertEqual(response.data["quality_warehouse"], self.qa.pk)

    def test_a_line_can_say_where_its_goods_are(self):
        self.two_step()
        _receipt, line = self.receive("40")
        response = self.client.get(
            f"/api/purchasing/goods-receipt-lines/{line.pk}/route/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["current_step"], "BAY")
        self.assertEqual(
            [row["warehouse"] for row in response.data["steps"]], ["BAY", "STK"]
        )

    def test_putting_away_is_an_action(self):
        self.two_step()
        _receipt, line = self.receive("40")
        response = self.client.post(
            f"/api/purchasing/goods-receipt-lines/{line.pk}/advance/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["to"], "STK")
        self.assertEqual(self.item.on_hand_at(self.stock_room), Decimal("40"))

    def test_a_partial_put_away_says_what_is_left(self):
        self.two_step()
        _receipt, line = self.receive("40")
        response = self.client.post(
            f"/api/purchasing/goods-receipt-lines/{line.pk}/advance/",
            {"quantity": "15"}, format="json",
        )
        self.assertEqual(Decimal(response.data["still_here"]), Decimal("25"))

    def test_a_refusal_arrives_as_an_answer(self):
        _receipt, line = self.receive("40")
        response = self.client.post(
            f"/api/purchasing/goods-receipt-lines/{line.pk}/advance/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("end of its route", str(response.data))
