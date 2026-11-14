"""
g22 review of fix_stores: probes for O210 (the shelf foots to the inventory
account after landed freight is released, however the goods moved).

Each test states a fact about stock and money. Written to assert what SHOULD be
true; a FAIL is a finding. Subclasses the author's fixture and strips its
inherited tests so only the probes below run.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.inventory.models import Item, Lot, Warehouse
from apps.inventory.reports import reconcile_to_ledger
from apps.purchasing import tests_returns_and_landed as base
from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine


def only_mine(cls):
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


D = Decimal


@only_mine
class StoresReviewLandedProbes(base.LandedWhereTheGoodsAreTests):
    def foots(self):
        diff = reconcile_to_ledger()["difference"]
        self.assertEqual(diff, D("0.00"), f"the shelf and the inventory account part by {diff}")

    def values(self):
        wh = self.item.stock_value_at(self.warehouse)
        qc = self.item.stock_value_at(self.inspection) if hasattr(self, "inspection") else D("0")
        return wh, qc

    # ---- partial clearance out of inspection, then release
    def test_p1_landed_before_a_partial_clearance_then_released_foots(self):
        for method in ("average", "fifo"):
            with self.subTest(method=method):
                self.setUp_again()
                if method == "fifo":
                    self.first_in_first_out()
                self.via_inspection()
                self.received()
                line = self.receipt.lines.get()
                line.advance()
                application = self.landed()
                self.assertEqual(self.item.stock_value_at(self.inspection), D("130.00"))
                self.receipt.accept(self.warehouse, quantities={line: D("4")})
                self.foots()
                self.assertEqual(self.values(), (D("52.00"), D("78.00")), "landed value follows the 4 cleared")
                application.release()
                self.foots()
                self.assertEqual(self.values(), (D("20.00"), D("30.00")))
                self.assertEqual(self.balance(self.cogs), D("0"), "nothing was sold; cost of sales stays empty")

    def test_p2_landed_after_a_partial_clearance_then_released_foots(self):
        for method in ("average", "fifo"):
            with self.subTest(method=method):
                self.setUp_again()
                if method == "fifo":
                    self.first_in_first_out()
                self.via_inspection()
                self.received()
                line = self.receipt.lines.get()
                line.advance()
                self.receipt.accept(self.warehouse, quantities={line: D("4")})
                application = self.landed()
                self.foots()
                self.assertEqual(sum(self.values()), D("130.00"))
                application.release()
                self.foots()
                self.assertEqual(self.values(), (D("20.00"), D("30.00")))
                self.assertEqual(self.balance(self.cogs), D("0"))

    def test_p3_cleared_then_part_issued_part_returned_then_released(self):
        # 10 at 5, 4 cleared + 6 sent back from inspection (rejected), landed, release.
        for method in ("average", "fifo"):
            with self.subTest(method=method):
                self.setUp_again()
                if method == "fifo":
                    self.first_in_first_out()
                self.via_inspection()
                self.received()
                line = self.receipt.lines.get()
                line.advance()
                self.receipt.accept(self.warehouse, quantities={line: D("4")})
                self.receipt.create_return(quantities={line: D("6")}, from_inspection=True)
                application = self.landed()
                self.foots()
                application.release()
                self.foots()
                # 4 goods left on the shelf at 5; 6 went back (their 30 + share of freight to PPV).
                self.assertEqual(self.item.stock_value_at(self.warehouse), D("20.00"))
                self.assertEqual(self.balance(self.inventory), D("20"))

    def test_p4_cleared_issued_to_a_run_returned_to_vendor_released(self):
        for method in ("average", "fifo"):
            with self.subTest(method=method):
                self.setUp_again()
                if method == "fifo":
                    self.first_in_first_out()
                self.via_inspection()
                self.received()
                self.inspected_and_cleared()
                self.issued_to_a_run("6")
                line = self.receipt.lines.get()
                self.receipt.create_return(quantities={line: D("2")})
                application = self.landed()
                self.foots()
                application.release()
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.warehouse), D("10.00"))
                self.assertEqual(self.balance(self.cogs), D("0"), "6 in the run, 2 back to the vendor, 2 on the shelf: no sale")

    def test_p5_a_round_trip_between_shelves_then_released(self):
        from apps.inventory.transfers import move_stock

        self.received()
        application = self.landed()
        other = Warehouse.objects.create(code="W2", name="Second")
        when = timezone.now()
        move_stock(self.item, self.warehouse, other, D("6"), "T-1", when, "out")
        move_stock(self.item, other, self.warehouse, D("2"), "T-2", when, "back")
        self.foots()
        self.issued_to_a_run("4")
        application.release()
        self.foots()
        self.assertEqual(sum([self.item.stock_value_at(self.warehouse), self.item.stock_value_at(other)]), D("30.00"))
        self.assertEqual(self.balance(self.cogs), D("0"))

    # ---- rebatch
    def lot_receipt(self, code, qty="10", price="5"):
        Item.objects.filter(pk=self.item.pk).update(tracking="lot")
        self.item.refresh_from_db()
        lot = Lot.objects.create(item=self.item, code=code)
        order = self.make_order(qty, price)
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.first(), lot=lot,
                                        warehouse=self.warehouse, quantity_received=D(qty))
        receipt.post()
        return lot, receipt

    def test_p7_rebatch_then_part_issued_then_released_foots(self):
        from apps.manufacturing.rebatch import rebatch

        for method in ("average", "fifo", "specific"):
            with self.subTest(method=method):
                self.setUp_again()
                Item.objects.filter(pk=self.item.pk).update(costing_method=method)
                self.item.refresh_from_db()
                first, self.receipt = self.lot_receipt("L-1")
                application = self.landed()
                six, four = (Lot.objects.create(item=self.item, code=code) for code in ("L-2", "L-3"))
                rebatch(self.item, self.warehouse, [(first, D("10"))], [(six, D("6")), (four, D("4"))],
                        "split", on_date=datetime.date(2026, 2, 5))
                self.foots()
                application.release()
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.warehouse), D("50.00"))
                self.assertEqual(self.balance(self.cogs), D("0"))

    def test_p8_rebatch_of_two_batches_one_with_freight_then_released_foots(self):
        # L-1 (10 at 5, 80 freight) and L-2 (10 at 5, none) pooled into L-3 (12) and L-4 (8). Release the 80.
        from apps.manufacturing.rebatch import rebatch

        for method in ("average", "fifo", "specific"):
            with self.subTest(method=method):
                self.setUp_again()
                Item.objects.filter(pk=self.item.pk).update(costing_method=method)
                self.item.refresh_from_db()
                first, self.receipt = self.lot_receipt("L-1")
                application = self.landed()
                second, _ = self.lot_receipt("L-2")
                a, b = (Lot.objects.create(item=self.item, code=code) for code in ("L-3", "L-4"))
                rebatch(self.item, self.warehouse, [(first, D("10")), (second, D("10"))],
                        [(a, D("12")), (b, D("8"))], "pool", on_date=datetime.date(2026, 2, 5))
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.warehouse), D("180.00"))
                application.release()
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.warehouse), D("100.00"))
                self.assertEqual(self.balance(self.cogs), D("0"))

    def test_p9_rebatch_issue_to_a_run_then_released_goes_to_production_cost(self):
        from apps.manufacturing.rebatch import rebatch

        first, self.receipt = self.lot_receipt("L-1")
        application = self.landed()
        six, four = (Lot.objects.create(item=self.item, code=code) for code in ("L-2", "L-3"))
        rebatch(self.item, self.warehouse, [(first, D("10"))], [(six, D("6")), (four, D("4"))],
                "split", on_date=datetime.date(2026, 2, 5))
        # 6 of 10 issued to a run (the whole of L-2 or the pooled average, whichever the issue takes).
        self.issued_lot_to_a_run(six, "6")
        self.foots()
        application.release()
        self.foots()
        self.assertEqual(self.balance(self.cogs), D("0"), "the freight left with goods issued to a run, not a sale")
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("20.00"))

    def test_a_rebatch_voids_after_the_freight_on_its_batches_was_released(self):
        from apps.manufacturing.rebatch import rebatch

        first, self.receipt = self.lot_receipt("L-1")
        application = self.landed()
        six, four = (Lot.objects.create(item=self.item, code=code) for code in ("L-2", "L-3"))
        doc = rebatch(self.item, self.warehouse, [(first, D("10"))], [(six, D("6")), (four, D("4"))],
                      "split", on_date=datetime.date(2026, 2, 5))
        application.release()
        self.foots()
        # The release wrote value-only movements on the batches made; they are no use of the goods.
        try:
            doc.void("probe")
        except ValidationError as refused:
            self.fail(f"a re-batch could not be voided after a freight release on its batches: {refused}")
        self.foots()
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("50.00"))

    def test_freight_on_goods_rebatched_then_voided_comes_off_where_the_goods_are_on_release(self):
        from apps.manufacturing.rebatch import rebatch

        first, self.receipt = self.lot_receipt("L-1")
        application = self.landed()
        six, four = (Lot.objects.create(item=self.item, code=code) for code in ("L-2", "L-3"))
        doc = rebatch(self.item, self.warehouse, [(first, D("10"))], [(six, D("6")), (four, D("4"))],
                      "split", on_date=datetime.date(2026, 2, 5))
        doc.void("probe")
        self.foots()
        application.release()
        print("P11", self.item.stock_value_at(self.warehouse), self.balance(self.inventory), self.balance(self.cogs), self.balance(self.freight_expense))
        self.foots()
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("50.00"))

    def issued_lot_to_a_run(self, lot, quantity):
        from apps.inventory.models import Item as _Item
        from apps.manufacturing.bom import BillOfMaterials, BomComponent
        from apps.manufacturing.orders import (
            ManufacturingSettings, MaterialIssue, MaterialIssueLine, WorkCentre, WorkOrder,
        )
        from apps.accounting.models import Account, AccountType

        self.wip = Account.objects.create(code="1250", name="Work in progress", account_type=AccountType.ASSET)
        ManufacturingSettings.objects.create(wip_account=self.wip, variance_account=self.cogs)
        sack = _Item.objects.create(sku="SACK", name="Sack", uom=self.uom)
        bom = BillOfMaterials.objects.create(item=sack, name="Sack", quantity_produced=D("10"), uom=self.uom)
        BomComponent.objects.create(bom=bom, item=self.item, quantity=D("10"), uom=self.uom, line_number=1)
        run = WorkOrder.objects.create(item=sack, bom=bom, quantity_ordered=D("10"), uom=self.uom,
                                       warehouse=self.warehouse,
                                       work_centre=WorkCentre.objects.create(code="LOOM", name="Loom"))
        run.release(datetime.date(2026, 1, 15))
        issue = MaterialIssue.objects.create(work_order=run, issue_date=datetime.date(2026, 1, 15),
                                             warehouse=self.warehouse)
        MaterialIssueLine.objects.create(issue=issue, item=self.item, quantity=D(quantity), lot=lot,
                                         uom=self.uom, line_number=1)
        issue.post()
        return run

    def setUp(self):
        super().setUp()
        from django.db import transaction

        self._sp = transaction.savepoint()

    def setUp_again(self):
        """A fresh world for the next subTest: roll back to what setUp built."""
        from django.db import transaction

        transaction.savepoint_rollback(self._sp)
        self._sp = transaction.savepoint()
        self.warehouse.refresh_from_db()
        self.item.refresh_from_db()


@only_mine
class StoresReviewPutAwayProbes(base.LandedWhereTheGoodsAreTests):
    """O11 with O210: a put-away cancelled, landed cost before or after, released."""

    def foots(self):
        diff = reconcile_to_ledger()["difference"]
        self.assertEqual(diff, D("0.00"), f"the shelf and the inventory account part by {diff}")

    def test_t1_part_put_away_then_cancelled_then_landed_and_released_foots(self):
        self.via_the_bay()
        order, self.receipt = self.goods_received("10", "5")
        line = self.receipt.lines.get()
        move = line.advance(quantity=D("6"))
        move.transfer.cancel()
        self.assertEqual((self.item.on_hand_at(self.bay), line.quantity_at(self.bay)), (D("10"), D("10")))
        application = self.landed()
        self.assertEqual(self.item.stock_value_at(self.bay), D("130.00"))
        self.foots()
        application.release()
        self.foots()
        self.assertEqual((self.item.stock_value_at(self.bay), self.item.stock_value_at(self.warehouse)),
                         (D("50.00"), D("0.00")))

    def test_t2_landed_then_part_put_away_then_the_put_away_cancelled_then_released_foots(self):
        for method in ("average", "fifo"):
            with self.subTest(method=method):
                self.setUp_again()
                if method == "fifo":
                    self.first_in_first_out()
                self.via_the_bay()
                order, self.receipt = self.goods_received("10", "5")
                application = self.landed()
                line = self.receipt.lines.get()
                move = line.advance(quantity=D("6"))
                self.foots()
                move.transfer.cancel()
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.bay), D("130.00"))
                application.release()
                self.foots()
                self.assertEqual(self.item.stock_value_at(self.bay), D("50.00"))
                self.assertEqual(self.balance(self.cogs), D("0"))

    def setUp(self):
        super().setUp()
        from django.db import transaction

        self._sp = transaction.savepoint()

    def setUp_again(self):
        from django.db import transaction

        transaction.savepoint_rollback(self._sp)
        self._sp = transaction.savepoint()
        self.warehouse.refresh_from_db()
        self.item.refresh_from_db()
