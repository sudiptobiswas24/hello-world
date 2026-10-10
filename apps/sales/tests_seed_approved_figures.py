"""O174: sales 0064 seeds approved_figures for orders approved before 0063, from what they hold."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from .models import ApprovalPolicy, SalesOrder, SalesOrderLine
from .tests_base import SalesTestCase


@tag("migration")
class AnOrderApprovedBefore0063KeepsItsApprovalTests(TransactionTestCase):
    serialized_rollback = True
    setUp = SalesTestCase.setUp

    def test_seeded_from_what_it_holds_so_a_cut_can_be_put_back(self):
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                             unit_price=Decimal("100"), discount_percent=Decimal("20"),
                                             revenue_account=self.revenue)
        order.approve(by=User.objects.create_user("approver"))
        order.confirm()
        approved = SalesOrder.objects.get(pk=order.pk).approved_figures
        SalesOrder.objects.filter(pk=order.pk).update(approved_figures=None)  # as before 0063
        untouched = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                              currency=self.usd)

        executor = MigrationExecutor(connection)
        executor.migrate([("sales", "0063_salesorder_approved_figures")])
        executor.loader.build_graph()
        executor.migrate([("sales", "0064_seed_approved_figures")])

        self.assertEqual(SalesOrder.objects.get(pk=order.pk).approved_figures, approved)
        self.assertEqual(approved["discounts"], {str(line.pk): "20.00"})
        self.assertIsNone(SalesOrder.objects.get(pk=untouched.pk).approved_figures)
        for discount in ("10", "20"):  # cut, then put back within what was approved
            line = SalesOrderLine.objects.get(pk=line.pk)
            line.discount_percent = Decimal(discount)
            line.save()
        self.assertEqual(SalesOrderLine.objects.get(pk=line.pk).discount_percent, Decimal("20.00"))
