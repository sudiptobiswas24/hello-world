"""
A purchase line closed short: the vendor will send no more.

The mirror of a sales line's close_short(). Without it a short delivery
stayed open for ever, so MRP and the reorder rules counted the missing
five per cent as on its way, and a drop-ship the customer sent back to
the vendor left their line awaited from a vendor who was never sending
it again, unshippable from the shelf. Refusals first.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.sales.models import SalesOrder

from .models import Bill, BillLine, BillPolicy, FulfilmentStatus, PurchaseOrder, ReorderRule
from .tests_drop_ship import DropShipTestCase


class CloseShortTestCase(DropShipTestCase):
    def short_order(self, ordered="10", received="7"):
        order = self.make_order(quantity=ordered)
        self.receive(order, received)
        return order, order.lines.get()


class RefusalTests(CloseShortTestCase):
    def test_a_reason_is_required(self):
        _order, line = self.short_order()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            line.close_short("  ")
        line.refresh_from_db()
        self.assertFalse(line.is_closed_short())

    def test_a_line_received_in_full_has_nothing_to_close(self):
        _order, line = self.short_order(received="10")
        with self.assertRaisesMessage(ValidationError, "received in full"):
            line.close_short("Vendor out of stock")

    def test_billed_beyond_what_came_is_debited_first(self):
        order = self.make_order(quantity="10")
        receipt = self.receive(order, "10")
        line = order.lines.get()
        order.create_bill(self.payable).post()
        receipt.create_return(quantities={receipt.lines.get(): Decimal("4")}, debit_bills=False)
        with self.assertRaisesMessage(ValidationError, "Raise a debit note"):
            line.close_short("Rejected, not replaced")

    def test_twice_is_refused_and_a_closed_line_receives_nothing(self):
        order, line = self.short_order()
        line.close_short("Vendor out of stock")
        with self.assertRaisesMessage(ValidationError, "already closed short"):
            line.close_short("Again")
        with self.assertRaisesMessage(ValidationError, "Reopen it to receive more"):
            self.receive(order, "1")
        self.assertEqual(line.quantity_received(), Decimal("7"))


class WhatClosingChangesTests(CloseShortTestCase):
    def test_the_rest_is_no_longer_expected_anywhere(self):
        from apps.planning.mrp import purchase_supply

        rule = ReorderRule.objects.create(item=self.item, warehouse=self.warehouse,
                                          minimum=Decimal("1"), target=Decimal("20"))
        order, line = self.short_order()
        self.assertEqual(rule.on_order(), Decimal("3"))
        self.assertEqual(sum(row.quantity for row in purchase_supply(
            self.item, self.warehouse, datetime.date(2026, 2, 1))), Decimal("3"))

        line.close_short("Vendor out of stock")
        line.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(line.quantity_open(), Decimal("0"))
        self.assertEqual(order.receipt_status(), FulfilmentStatus.FULL)
        self.assertEqual(rule.on_order(), Decimal("0"))
        self.assertEqual(purchase_supply(self.item, self.warehouse, datetime.date(2026, 2, 1)), [])

        line.reopen()
        self.assertEqual(rule.on_order(), Decimal("3"))
        self.receive(order, "3")
        self.assertEqual(line.quantity_received(), Decimal("10"))

    def test_a_drop_ship_sent_back_and_not_replaced_is_the_shelfs_to_send(self):
        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        drop.confirm()
        receipt = self.receive(drop, "10")
        receipt.create_return()
        self.sales_line.refresh_from_db()
        # Returned: owed again, and awaited from the vendor again.
        self.assertEqual(self.sales_line.quantity_open(), Decimal("10"))
        self.assertEqual(self.sales_line.quantity_to_ship(), Decimal("0"))

        drop.lines.get().close_short("Rejected by the customer; vendor will not replace")
        self.assertEqual(self.sales_line.quantity_to_ship(), Decimal("10"))
        delivery = SalesOrder.objects.get(pk=sale.pk).create_delivery(warehouse=self.warehouse)
        self.assertEqual(delivery.lines.get().quantity_shipped, Decimal("10"))

    def test_reopening_a_drop_ship_already_met_another_way_is_refused(self):
        sale = self.sales_order("10")
        drop = PurchaseOrder.create_for_drop_ship(sale, self.vendor)
        drop.confirm()
        line = drop.lines.get()
        line.close_short("Vendor cannot make it")
        PurchaseOrder.create_for_drop_ship(sale, self.vendor)  # another vendor's, say
        with self.assertRaisesMessage(ValidationError, "would bring 10"):
            line.reopen()



class BillsOnlyWhatCameTests(CloseShortTestCase):
    """
    Closed short, a line bills only what came. Closing refused a line billed beyond its
    receipts and said so; nothing held to it after, so an order billed as ordered drafted
    and posted a bill for all ten, and the accrual kept a debit no receipt could clear.
      ordered 10 at 5, received 7, closed short
      receipt  Dr Inventory 35 / Cr GRNI 35
      bill     Dr GRNI 35 / Cr AP 35            GRNI 0 (billed for 10 it read 15 debit)
    """

    def closed(self, policy):
        order, line = self.short_order()
        order.bill_policy = policy
        order.save()
        line.close_short("Vendor out of stock")
        return order, line

    def test_a_bill_typed_for_the_rest_is_refused(self):
        order, line = self.closed(BillPolicy.ORDERED)
        bill = Bill.objects.create(vendor=self.vendor, bill_date=datetime.date(2026, 1, 12),
                                   purchase_order=order, payable_account=self.payable)
        BillLine.objects.create(bill=bill, order_line=line, item=self.item, quantity=Decimal("10"),
                                unit_price=Decimal("5"), expense_account=self.expense)
        with self.assertRaisesMessage(ValidationError,
                                      "would exceed the received quantity of a line closed short (7; 0 already billed)"):
            bill.post()

    def test_billed_for_what_came_the_order_is_billed_and_the_accrual_clears(self):
        for policy in (BillPolicy.ORDERED, BillPolicy.RECEIVED):
            with self.subTest(policy=policy):
                order, line = self.closed(policy)
                bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 12))
                self.assertEqual(bill.lines.get().quantity, Decimal("7"))
                bill.post()
                self.assertEqual((order.bill_status(), self.balance(self.grni)), (FulfilmentStatus.FULL, Decimal("0")))
                with self.assertRaisesMessage(ValidationError, "already fully billed"):
                    order.create_bill(self.payable)


class ThroughTheApiTests(CloseShortTestCase):
    def as_(self, role):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_buyer_closes_and_reopens_and_the_store_may_not(self):
        _order, line = self.short_order()
        url = f"/api/purchasing/purchase-order-lines/{line.pk}/"
        store = self.as_("Warehouse Staff")
        self.assertEqual(store.post(url + "close-short/", {"reason": "x"}, format="json").status_code, 403)

        buyer = self.as_("Purchasing Clerk")
        refused = buyer.post(url + "close-short/", {}, format="json")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("Say why", str(refused.json()))
        closed = buyer.post(url + "close-short/", {"reason": "Vendor out of stock"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual((closed.json()["quantity_open"], closed.json()["closed_short_reason"]),
                         ("0.0000", "Vendor out of stock"))
        self.assertIsNotNone(closed.json()["closed_short_at"])
        reopened = buyer.post(url + "reopen/", {}, format="json")
        self.assertEqual(reopened.status_code, 200, reopened.content)
        self.assertEqual(reopened.json()["quantity_open"], "3.0000")
