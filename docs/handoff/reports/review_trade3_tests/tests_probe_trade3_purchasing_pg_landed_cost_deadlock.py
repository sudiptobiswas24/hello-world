"""
Review probe (trade3), PostgreSQL only: stockposition is unranked (O182) and a landed cost is applied
one receipt line at a time, each taking that line's positions in the order the caller listed the
lines. Two allocations onto the same two receipt lines, listed the opposite ways round, deadlock.
"""
import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase, tag
from django.test.testcases import SimpleTestCase

from apps.e2e.tests_races import RaceCase, race
from apps.inventory.models import Item, StockMovement

from .models import BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, GoodsReceiptLine as GRL
from .tests_returns_and_landed import ThirdPartyLandedCostTests


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TwoLandedCostsOnTheSameTwoReceiptLines(RaceCase):
    def setUp(self):
        # The fixture of the landed-cost tests, built by an instance that is never run as a test.
        self.fx = ThirdPartyLandedCostTests("test_a_separate_freight_bill_expenses_until_allocated")
        self.fx.setUp()

    def test_opposite_listing_order_deadlocks_on_the_positions(self):
        fx = self.fx
        order, first = fx.goods_received("10", "5")
        other = Item.objects.create(sku="W2", name="Gadget", uom=fx.uom)
        second_order = PurchaseOrder.objects.create(vendor=fx.vendor, order_date=datetime.date(2026, 1, 1))
        second_line = PurchaseOrderLine.objects.create(order=second_order, item=other, uom=fx.uom,
                                                       quantity=Decimal("10"), unit_price=Decimal("15"))
        second_order.confirm()
        second = GoodsReceipt.objects.create(purchase_order=second_order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=second, order_line=second_line, warehouse=fx.warehouse,
                                        quantity_received=Decimal("10"))
        second.post()
        _bill1, charge1 = fx.carrier_bill("80")
        _bill2, charge2 = fx.carrier_bill("60")
        x, y = first.lines.get().pk, second.lines.get().pk

        def allocate(charge_pk, line_pks):
            def call():
                BillLine.objects.get(pk=charge_pk).allocate_landed_cost(
                    [GRL.objects.get(pk=pk) for pk in line_pks])
            return call

        outcomes = race(StockMovement, allocate(charge1.pk, [x, y]), allocate(charge2.pk, [y, x]))
        print("\nOUTCOMES", outcomes)
        self.assertEqual(outcomes, ["done", "done"])
