"""O174, O186: sales 0064 seeds approved_figures for orders approved before 0063, from what the approval stood on."""

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
    # Not serialized_rollback: restoring the snapshot collides with content
    # types another test already made, and errored in setUpClass whenever this
    # ran after one (apps/e2e/tests_races.py says the same). The schema is put
    # back to the latest migrations instead, as the assets migration test does.
    setUp = SalesTestCase.setUp

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def approved_at_20(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                          currency=self.usd)
        line = SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("10"),
                                             unit_price=Decimal("100"), discount_percent=Decimal("20"),
                                             revenue_account=self.revenue)
        order.approve(by=User.objects.get_or_create(username="approver")[0])
        order.confirm()
        return order, line

    def put(self, line, discount):
        line = SalesOrderLine.objects.get(pk=line.pk)
        line.discount_percent = Decimal(discount)
        line.save()

    def test_seeded_from_the_approval_so_a_cut_can_be_put_back(self):
        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        uncut, uncut_line = self.approved_at_20()
        cut, cut_line = self.approved_at_20()
        self.put(cut_line, "10")  # cut after its approval, before the seed: the line holds 10, the approval was 20
        SalesOrder.objects.filter(pk__in=[uncut.pk, cut.pk]).update(approved_figures=None)  # as before 0063
        untouched = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1),
                                              currency=self.usd)

        executor = MigrationExecutor(connection)
        executor.migrate([("sales", "0063_salesorder_approved_figures")])
        executor.loader.build_graph()
        executor.migrate([("sales", "0064_seed_approved_figures")])

        self.assertEqual(SalesOrder.objects.get(pk=uncut.pk).approved_figures,
                         {"discounts": {str(uncut_line.pk): "20.00"}, "total": None, "margin": None})
        self.assertEqual(SalesOrder.objects.get(pk=cut.pk).approved_figures,
                         {"discounts": {str(cut_line.pk): "20.00"}, "total": None, "margin": None})
        self.assertIsNone(SalesOrder.objects.get(pk=untouched.pk).approved_figures)
        for discount in ("10", "20"):  # cut, then put back within what was approved
            self.put(uncut_line, discount)
        self.put(cut_line, "20")
        self.assertEqual(SalesOrderLine.objects.get(pk=uncut_line.pk).discount_percent, Decimal("20.00"))
        self.assertEqual(SalesOrderLine.objects.get(pk=cut_line.pk).discount_percent, Decimal("20.00"))


class AnOrderCutBeforeTheSeedKeepsItsApprovalTests(SalesTestCase):  # O186, the probe P9 without the migration run
    def test_approved_at_20_cut_to_10_is_seeded_at_20(self):
        from importlib import import_module

        from django.apps import apps

        ApprovalPolicy.objects.create(code="STD", name="Standard", max_discount_percent=Decimal("15"))
        order, line = AnOrderApprovedBefore0063KeepsItsApprovalTests.approved_at_20(self)
        AnOrderApprovedBefore0063KeepsItsApprovalTests.put(self, line, "10")
        SalesOrder.objects.filter(pk=order.pk).update(approved_figures=None)
        import_module("apps.sales.migrations.0064_seed_approved_figures").seed(apps, None)
        self.assertEqual(SalesOrder.objects.get(pk=order.pk).approved_figures["discounts"], {str(line.pk): "20.00"})
        AnOrderApprovedBefore0063KeepsItsApprovalTests.put(self, line, "20")
        self.assertEqual(SalesOrderLine.objects.get(pk=line.pk).discount_percent, Decimal("20.00"))
