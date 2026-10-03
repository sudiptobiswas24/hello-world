"""
100 widgets ordered, 5% over and 5% under accepted: up to 105 may ship
and be billed, and 95 shipped is the order met.

  104 shipped: inside, billed 104.       106: refused.
  96 shipped: met; owes nothing, holds nothing, plans nothing.
  90 shipped: 10 still owed; closed short, owed nothing, billed only 90;
  reopened, owed 10 again and held again.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase, tag

from apps.planning.mrp import sales_demand

from .models import (
    CustomerProfile,
    Delivery,
    DeliveryLine,
    FulfilmentStatus,
    InvoicePolicy,
    SalesOrder,
    SalesOrderLine,
)
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


class ToleranceTestCase(SalesTestCase):
    def order(self, policy=InvoicePolicy.DELIVERED, **tolerance):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY,
                                          currency=self.usd, invoice_policy=policy)
        values = {"over_delivery_percent": Decimal("5"),
                  "under_delivery_percent": Decimal("5")}
        values.update(tolerance)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom,
                                      quantity=Decimal("100"), unit_price=Decimal("10"),
                                      revenue_account=self.revenue, warehouse=self.warehouse,
                                      **{k: v for k, v in values.items() if v is not None})
        order.confirm()
        return order

    def ship_it(self, order, quantity):
        delivery = Delivery.objects.create(sales_order=order, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=order.lines.get(),
                                    warehouse=self.warehouse, quantity_shipped=Decimal(quantity))
        delivery.post()
        return delivery


class OverrunTests(ToleranceTestCase):
    def test_shipped_and_billed_inside_the_overrun(self):
        order = self.order()
        self.ship_it(order, "104")
        line = order.lines.get()
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        self.assertEqual(invoice.lines.get().quantity, Decimal("104.0000"))
        invoice.post()
        self.assertTrue(line.is_fully_invoiced())
        # A line over-shipped inside its tolerance is not an edit below
        # what shipped: it still saves.
        line.description = "Widgets, as agreed"
        line.save()

    def test_not_beyond_it(self):
        order = self.order()
        with self.assertRaisesMessage(ValidationError, "5.00% over accepted"):
            self.ship_it(order, "106")
        self.ship_it(order, "105")

    def test_exact_where_nothing_was_agreed(self):
        order = self.order(over_delivery_percent=None, under_delivery_percent=None)
        line = order.lines.get()
        self.assertEqual((line.over_delivery_percent, line.under_delivery_percent),
                         (Decimal("0"), Decimal("0")))
        with self.assertRaisesMessage(ValidationError, "would exceed the ordered quantity"):
            self.ship_it(order, "101")

    def test_the_quantity_cannot_drop_below_what_shipped_with_its_tolerance(self):
        order = self.order()
        self.ship_it(order, "104")
        line = order.lines.get()
        line.quantity = Decimal("98")
        with self.assertRaisesMessage(ValidationError, "cannot drop below"):
            line.save()
        line.quantity = Decimal("99.05")
        line.save()

    def test_billing_follows_what_shipped_not_the_order(self):
        order = self.order()
        self.ship_it(order, "104")
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        invoice.lines.update(quantity=Decimal("105"))
        with self.assertRaisesMessage(ValidationError, "would exceed the ordered quantity"):
            invoice.post()


class MetInsideTheToleranceTests(ToleranceTestCase):
    def test_met_owes_nothing_holds_nothing_plans_nothing(self):
        order = self.order()
        line = order.lines.get()
        self.assertEqual(line.quantity_reserved(), Decimal("100"))
        self.ship_it(order, "96")
        line.refresh_from_db()
        self.assertEqual((line.quantity_open(), line.quantity_reserved(),
                          order.delivery_status()),
                         (Decimal("0"), Decimal("0"), FulfilmentStatus.FULL))
        self.assertEqual(sales_demand(self.item, self.warehouse, DAY), [])

    def test_just_short_of_it_is_still_owed(self):
        order = self.order()
        self.ship_it(order, "94")
        line = order.lines.get()
        self.assertEqual((line.quantity_open(), order.delivery_status()),
                         (Decimal("6"), FulfilmentStatus.PARTIAL))
        (row,) = sales_demand(self.item, self.warehouse, DAY)
        self.assertEqual(row.quantity, Decimal("6"))
        self.assertEqual(self.ship_it(order, "1").shortfall(), {})
        self.assertEqual(order.lines.get().quantity_open(), Decimal("0"))


class ClosedShortTests(ToleranceTestCase):
    def test_closed_short_owes_nothing_and_bills_what_shipped(self):
        order = self.order(policy=InvoicePolicy.ORDERED)
        delivery = self.ship_it(order, "90")
        self.assertEqual(delivery.shortfall(), {order.lines.get(): Decimal("10")})
        line = order.lines.get()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            line.close_short(" ")
        line.close_short("Customer cancelled the balance")
        line.refresh_from_db()
        self.assertEqual((line.quantity_open(), line.quantity_reserved(),
                          line.closed_short_reason),
                         (Decimal("0"), Decimal("0"), "Customer cancelled the balance"))
        self.assertEqual(sales_demand(self.item, self.warehouse, DAY), [])
        self.assertEqual(delivery.shortfall(), {})
        with self.assertRaisesMessage(ValidationError, "was closed short"):
            self.ship_it(order, "5")
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        self.assertEqual(invoice.lines.get().quantity, Decimal("90.0000"))
        invoice.lines.update(quantity=Decimal("95"))
        with self.assertRaisesMessage(ValidationError, "shipped quantity of a line closed short"):
            invoice.post()
        with self.assertRaisesMessage(ValidationError, "already closed short"):
            line.close_short("Again")

    def test_reopened_it_is_owed_and_held_again(self):
        order = self.order()
        self.ship_it(order, "90")
        line = order.lines.get()
        with self.assertRaisesMessage(ValidationError, "is not closed short"):
            line.reopen()
        line.close_short("Balance cancelled")
        line.reopen()
        line.refresh_from_db()
        self.assertEqual((line.quantity_open(), line.quantity_reserved(), line.closed_short_at),
                         (Decimal("10"), Decimal("10"), None))
        self.ship_it(order, "10")

    def test_what_cannot_be_closed(self):
        order = self.order()
        self.ship_it(order, "96")
        with self.assertRaisesMessage(ValidationError, "already met"):
            order.lines.get().close_short("x")
        billed = self.order(policy=InvoicePolicy.ORDERED)
        billed.create_invoice(self.ar, invoice_date=DAY).post()
        self.ship_it(billed, "90")
        with self.assertRaisesMessage(ValidationError, "Credit the difference first"):
            billed.lines.get().close_short("x")
        charge = order.add_charge(self._freight(), Decimal("50"))
        with self.assertRaisesMessage(ValidationError, "A charge is not shipped"):
            charge.close_short("x")

    def _freight(self):
        from apps.accounting.models import ChargeType

        return ChargeType.objects.create(code="FRT", name="Freight",
                                         revenue_account=self.revenue)


class TermsOfTheOrderTests(ToleranceTestCase):
    def test_the_customers_default_written_down_on_the_line(self):
        profile = CustomerProfile.objects.create(party=self.customer,
                                                 over_delivery_percent=Decimal("10"),
                                                 under_delivery_percent=Decimal("3"))
        line = self.order(over_delivery_percent=None, under_delivery_percent=None).lines.get()
        self.assertEqual((line.over_delivery_percent, line.under_delivery_percent),
                         (Decimal("10.00"), Decimal("3.00")))
        profile.over_delivery_percent = Decimal("2")
        profile.save()
        line.refresh_from_db()
        self.assertEqual(line.most_shippable(), Decimal("110.00000000"))

    def test_a_tolerance_is_a_share(self):
        order = self.order()
        for field, value in (("over_delivery_percent", "101"),
                             ("under_delivery_percent", "100"),
                             ("over_delivery_percent", "-1")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                SalesOrderLine.objects.filter(pk=order.lines.get().pk).update(
                    **{field: Decimal(value)})

    def test_reorder_rules_count_only_what_is_owed(self):
        from apps.purchasing.models import ReorderRule

        order = self.order()
        self.ship_it(order, "90")
        rule = ReorderRule(item=self.item, warehouse=self.warehouse,
                           minimum=Decimal("1"), target=Decimal("2"))
        self.assertEqual(rule.committed(), Decimal("10"))
        order.lines.get().close_short("Balance cancelled")
        self.assertEqual(rule.committed(), Decimal("0"))


class CloseShortApiTests(ToleranceTestCase):
    def test_closed_and_reopened_through_the_api(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("sales"))
        order = self.order()
        self.ship_it(order, "90")
        base = f"/api/sales/sales-order-lines/{order.lines.get().pk}/"
        body = client.get(base).json()
        self.assertEqual((body["quantity_open"], body["over_delivery_percent"]),
                         ("10.0000", "5.00"))
        self.assertEqual(client.post(base + "close-short/", {}, format="json").status_code, 400)
        response = client.post(base + "close-short/", {"reason": "Balance cancelled"},
                               format="json")
        self.assertEqual((response.status_code, response.json()["quantity_open"]),
                         (200, "0.0000"))
        response = client.post(base + "reopen/", {}, format="json")
        self.assertEqual((response.status_code, response.json()["quantity_open"]),
                         (200, "10.0000"))


# Slow: it unwinds every later migration and replays them.
@tag("migration")
class LinesAlreadyTakenMigrate(TransactionTestCase):
    """Lines on the books before tolerances existed were taken exact."""

    before = [("sales", "0036_price_variation")]
    after = [("sales", "0037_order_line_tolerance")]

    def test_item_lines_are_written_exact_and_charges_left_alone(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        uom = apps.get_model("core", "UnitOfMeasure").objects.create(code="each", name="Each")
        party = apps.get_model("core", "Party").objects.create(code="C", name="C")
        account = apps.get_model("accounting", "Account").objects.create(
            code="4000", name="Revenue", account_type="income")
        item = apps.get_model("inventory", "Item").objects.create(sku="W", name="W", uom=uom)
        charge = apps.get_model("accounting", "ChargeType").objects.create(
            code="FRT", name="Freight", revenue_account=account)
        order = apps.get_model("sales", "SalesOrder").objects.create(customer=party,
                                                                     order_date=DAY)
        line = apps.get_model("sales", "SalesOrderLine")
        line.objects.create(order=order, item=item, uom=uom, quantity=10, unit_price=1)
        line.objects.create(order=order, charge=charge, quantity=1, unit_price=5)
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        line = executor.loader.project_state(self.after).apps.get_model(
            "sales", "SalesOrderLine")
        self.assertEqual(sorted((row.item_id is not None, row.over_delivery_percent,
                                 row.under_delivery_percent) for row in line.objects.all()),
                         [(False, None, None), (True, Decimal("0"), Decimal("0"))])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
