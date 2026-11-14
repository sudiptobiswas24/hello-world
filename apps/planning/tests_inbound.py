"""
Where inbound goods are going, and stock that will not live to be used.

  Batch A of 1,000 kg expires on day 10, batch B of 1,000 kg on day 40.
  800 kg is wanted on day 5 and 1,100 on day 20. First-expired-first-
  out, day 5 takes 800 of A; A's other 200 is gone on day 11; day 20
  takes all of B and is 100 short. Counting A's 200 as cover said
  nothing was short.
  Wanted on day 10 instead, A is still good that day.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.models import lock_rows
from apps.inventory.models import MovementType, StockMovement, Warehouse
from apps.inventory.tracking import Lot, TrackingMode
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    VendorPrice,
    raise_reorder_requisition,
)

from .models import DemandSource
from .mrp import expiring_unused, purchase_supply, requisition_supply, sales_demand
from .promise import available_to_promise
from .tests_base import TODAY
from .tests_mrp import PlanningTestCase


class InboundTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.godown = Warehouse.objects.create(code="G2", name="Second godown")
        # O415: a line naming no warehouse belongs to the default receiving warehouse, and only there.
        self.plant.receives_by_default = True
        self.plant.save()

    def purchase(self, quantity, warehouse=None, **order_extra):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=TODAY,
                                             currency=self.inr, **order_extra)
        line = PurchaseOrderLine.objects.create(
            order=order, item=self.virgin, uom=self.kg, quantity=Decimal(quantity),
            unit_price=Decimal("90"), expected_date=self.day(10), warehouse=warehouse)
        order.confirm()
        return line

    def inbound(self, warehouse=None):
        return sum((row.quantity for row in purchase_supply(
            self.virgin, warehouse or self.plant, TODAY)), Decimal("0"))


class GoingWhereItIsGoingTests(InboundTestCase):
    def test_counted_only_at_the_warehouse_it_is_for(self):
        # O415: the 100 naming no warehouse is the default receiving warehouse's, the plant's, and not the
        # godown's as well. This read 300 at the godown before, encoding the double count.
        self.purchase("300", self.plant)
        self.purchase("200", self.godown)
        self.purchase("100")
        self.assertEqual((self.inbound(), self.inbound(self.godown)),
                         (Decimal("400"), Decimal("200")))

    def test_a_drop_ship_never_comes_here(self):
        sale = self.sell(self.virgin, "500", self.day(20)).order
        self.purchase("500", self.plant, drop_ship_for=sale)
        self.assertEqual(self.inbound(), Decimal("0"))
        self.assertEqual(self.orders()["PP-RAFFIA"].quantity, Decimal("500"))

    def test_a_requisition_counts_where_it_is_wanted(self):
        requisition = PurchaseRequisition.objects.create(requested_by=self.buyer,
                                                         request_date=TODAY,
                                                         needed_by=self.day(10))
        for quantity, warehouse in (("70", self.plant), ("20", self.godown), ("10", None)):
            PurchaseRequisitionLine.objects.create(requisition=requisition, item=self.virgin,
                                                   uom=self.kg, quantity=Decimal(quantity),
                                                   warehouse=warehouse)
        total = lambda warehouse: sum((row.quantity for row in requisition_supply(self.virgin, warehouse, TODAY)),
                                      Decimal("0"))
        self.assertEqual((total(self.plant), total(self.godown)), (Decimal("80"), Decimal("20")))


class CarriedThroughTests(InboundTestCase):
    def test_from_the_plan_to_the_requisition_to_the_order_to_the_receipt(self):
        self.sell(self.virgin, "500", self.day(30))
        planned = self.orders()["PP-RAFFIA"]
        requisition_line = planned.firm()
        self.assertEqual(requisition_line.warehouse, self.plant)
        requisition = requisition_line.requisition
        requisition.submit()
        requisition.approve(by=User.objects.create_user("approver"))
        VendorPrice.objects.create(vendor=self.vendor, item=self.virgin, currency=self.inr,
                                   unit_price=Decimal("90"), min_quantity=Decimal("0"),
                                   valid_from=TODAY)
        order = requisition.create_order(self.vendor)
        line = order.lines.get()
        self.assertEqual(line.warehouse, self.plant)
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=TODAY)
        received = GoodsReceiptLine.objects.create(receipt=receipt, order_line=line,
                                                   quantity_received=Decimal("500"))
        self.assertEqual(received.warehouse, self.plant)
        elsewhere = GoodsReceiptLine.objects.create(receipt=receipt, order_line=line,
                                                    warehouse=self.godown,
                                                    quantity_received=Decimal("1"))
        self.assertEqual(elsewhere.warehouse, self.godown)

    def test_a_reorder_requisition_is_wanted_where_its_rule_is(self):
        self.rule(self.virgin, minimum="1000", target="2000")
        requisition = raise_reorder_requisition(self.buyer, on_date=TODAY)
        self.assertEqual(requisition.lines.get().warehouse, self.plant)


class ReorderRuleScopeTests(InboundTestCase):
    def test_on_order_and_committed_are_this_warehouses(self):
        rule = self.rule(self.virgin, minimum="1000", target="2000")
        self.purchase("300", self.plant)
        self.purchase("200", self.godown)
        self.purchase("100")
        sale = self.sell(self.virgin, "50", self.day(20)).order
        self.purchase("500", None, drop_ship_for=sale)
        self.assertEqual(rule.on_order(), Decimal("400"))
        elsewhere = self.sell(self.virgin, "70", self.day(20))
        type(elsewhere).objects.filter(pk=elsewhere.pk).update(warehouse=self.godown)
        self.assertEqual(rule.committed(), Decimal("50"))


class ExpiringTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.virgin.tracking = TrackingMode.LOT
        self.virgin.save()
        self.a = self.batch("A", 10)
        self.b = self.batch("B", 40)

    def batch(self, code, expires_in):
        lot = Lot.objects.create(item=self.virgin, code=code, expires_on=self.day(expires_in))
        StockMovement.objects.create(item=self.virgin, warehouse=self.plant, lot=lot,
                                     movement_type=MovementType.RECEIPT, uom=self.kg,
                                     quantity=Decimal("1000"), unit_cost=Decimal("90"),
                                     occurred_at=timezone.now())
        return lot


class ExpiringUnusedTests(ExpiringTestCase):
    def test_what_is_left_on_a_batch_when_it_expires_is_planned_for(self):
        self.sell(self.virgin, "800", self.day(5))
        self.sell(self.virgin, "1100", self.day(20))
        rows = expiring_unused(self.virgin, self.plant, TODAY,
                               sales_demand(self.virgin, self.plant, TODAY))
        self.assertEqual([(row.date, row.quantity, row.source) for row in rows],
                         [(self.day(11), Decimal("200"), DemandSource.EXPIRY)])
        self.assertEqual(self.orders()["PP-RAFFIA"].quantity, Decimal("100"))

    def test_a_batch_is_good_on_the_day_it_expires(self):
        self.sell(self.virgin, "1000", self.day(10))
        self.sell(self.virgin, "1000", self.day(20))
        self.assertEqual(expiring_unused(self.virgin, self.plant, TODAY,
                                         sales_demand(self.virgin, self.plant, TODAY)), [])
        self.assertEqual(self.orders(), {})

    def test_a_batch_already_expired_is_not_lost_twice(self):
        # Already out of the opening balance; counting its expiry again
        # would take the same 1,000 kg away a second time.
        Lot.objects.filter(pk=self.a.pk).update(expires_on=TODAY - timedelta(days=1))
        rows = expiring_unused(self.virgin, self.plant, TODAY, [])
        self.assertEqual([(row.date, row.quantity) for row in rows],
                         [(self.day(41), Decimal("1000"))])

    def test_nothing_wanted_and_it_all_goes(self):
        rows = expiring_unused(self.virgin, self.plant, TODAY, [])
        self.assertEqual([(row.date, row.quantity) for row in rows],
                         [(self.day(11), Decimal("1000")), (self.day(41), Decimal("1000"))])

    def test_a_promise_counts_stock_only_until_it_expires(self):
        self.sell(self.virgin, "800", self.day(5))
        # 2,000 less the 800 owed: 1,200 can be promised now. A's other
        # 200 goes on day 11 and B's 1,000 on day 41, unless sold first.
        rows = {row["date"]: row["promisable"] for row in
                available_to_promise(self.virgin, self.plant, planned_on=TODAY)}
        self.assertEqual((rows[TODAY], rows[self.day(11)], rows[self.day(41)]),
                         (Decimal("1200"), Decimal("1000"), Decimal("0")))

    def test_a_batch_not_released_is_not_counted_twice(self):
        from apps.quality.models import Characteristic, InspectionPlan, PlanLine

        characteristic = Characteristic.objects.create(code="MFI", name="Melt flow",
                                                       uom=self.kg)
        inspection = InspectionPlan.objects.create(item=self.virgin, is_mandatory=True)
        PlanLine.objects.create(plan=inspection, characteristic=characteristic,
                                lower_limit=Decimal("2"), upper_limit=Decimal("4"),
                                line_number=1)
        # Neither batch is inspected: both are already out of the opening
        # balance, so their expiry takes nothing more away.
        self.assertEqual(expiring_unused(self.virgin, self.plant, TODAY, []), [])


class ARequisitionCountsOnceTests(InboundTestCase):
    """
    O315. 1,000 kg of polymer asked for by a requisition counts as supply
    exactly once on its way to the shelf: as the requisition until its order
    is confirmed, as the order from then on, as stock once it is in. Worked in
    calc_fix_plan/o315.py.
    """

    def setUp(self):
        super().setUp()
        from apps.accounting.models import Account, AccountType
        from apps.core.models import Company

        Company.objects.update(grni_account=Account.objects.create(
            code="2150", name="Goods received not invoiced", account_type=AccountType.LIABILITY))
        VendorPrice.objects.create(vendor=self.vendor, item=self.virgin, currency=self.inr,
                                   unit_price=Decimal("90"), min_quantity=Decimal("0"), valid_from=TODAY)
        self.requisition = PurchaseRequisition.objects.create(
            requested_by=self.buyer, request_date=TODAY, needed_by=TODAY)
        self.asked = PurchaseRequisitionLine.objects.create(
            requisition=self.requisition, item=self.virgin, uom=self.kg, quantity=Decimal("1000"))
        self.requisition.submit()

    def supply(self):
        def total(rows):
            return sum((row.quantity for row in rows), Decimal("0"))
        return (total(requisition_supply(self.virgin, self.plant, TODAY)),
                total(purchase_supply(self.virgin, self.plant, TODAY)))

    def approve_and_order(self, **kwargs):
        self.requisition.approve(by=User.objects.create_user("approver"))
        return self.requisition.create_order(self.vendor, **kwargs)

    def test_a_requisition_turned_into_a_draft_order_is_still_expected_supply(self):
        """The auditor's probe: draft order of 1,000 kg, and the plan asks for nothing more."""
        self.rule(self.virgin, minimum="1000")
        self.assertEqual(self.orders(), {})
        self.approve_and_order()
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))
        self.assertEqual(sorted(self.orders()), [])

    def test_each_step_forward_counts_the_thousand_once(self):
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))
        order = self.approve_and_order()
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))
        order.confirm()
        self.assertEqual(self.supply(), (Decimal("0"), Decimal("1000")))
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=TODAY)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                        warehouse=self.plant, quantity_received=Decimal("400"))
        receipt.post()
        self.assertEqual(self.supply(), (Decimal("0"), Decimal("600")))

    def test_a_draft_order_deleted_hands_the_quantity_back_to_the_requisition(self):
        order = self.approve_and_order()
        with transaction.atomic():  # as the API's delete holds the order first
            lock_rows(order)
            order.delete()
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))

    def test_a_confirmed_order_cancelled_hands_it_back_too(self):
        order = self.approve_and_order()
        order.confirm()
        order.cancel()
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))

    def test_a_part_received_order_is_not_cancelled_and_goods_returned_are_awaited_again(self):
        """
        O455 (calc_fix_core/o455.py). 400 of 1,000 kg in: (0, 600), and cancel() is refused. 100 sent
        back: the order awaits 700, the returned 100 among it, and is still refused. All 400 back: the
        order may be cancelled and the requisition is supply for the whole 1,000 again.
        """
        D = Decimal
        order = self.approve_and_order()
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=TODAY)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                        warehouse=self.plant, quantity_received=D("400"))
        receipt.post()
        self.assertEqual(self.supply(), (D("0"), D("600")))
        with self.assertRaisesMessage(ValidationError, "cannot be cancelled"):
            order.cancel()
        receipt.create_return({receipt.lines.get(): D("100")})
        self.assertEqual(self.supply(), (D("0"), D("700")))
        with self.assertRaisesMessage(ValidationError, "cannot be cancelled"):
            PurchaseOrder.objects.get(pk=order.pk).cancel()
        receipt.create_return()
        PurchaseOrder.objects.get(pk=order.pk).cancel()
        self.assertEqual(self.supply(), (D("1000"), D("0")))

    def test_a_rejected_requisition_is_nothing_coming(self):
        self.requisition.reject(note="Not this month.")
        self.assertEqual(self.supply(), (Decimal("0"), Decimal("0")))

    def test_part_confirmed_counts_the_rest_as_still_asked_for(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=TODAY, currency=self.inr)
        PurchaseOrderLine.objects.create(order=order, item=self.virgin, uom=self.kg, quantity=Decimal("600"),
                                         unit_price=Decimal("90"), expected_date=TODAY,
                                         requisition_line=self.asked)
        self.assertEqual(self.supply(), (Decimal("1000"), Decimal("0")))
        order.confirm()
        self.assertEqual(self.supply(), (Decimal("400"), Decimal("600")))
