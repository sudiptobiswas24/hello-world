"""
Audit probe (audit2_stores): receipts, returns, landed cost and its take-back, at figures that
do not come out even, under the average, FIFO and the standard, with the shelf against the ledger
after every step.
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType
from apps.inventory.costing import CostingMethod, replay
from apps.inventory.models import (
    AdjustmentDirection, AdjustmentReason, Item, StockAdjustment, StockAdjustmentLine, Warehouse)
from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory

from .models import (GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine)
from .tests_landed_cost import LandedCostTestCase

D = Decimal


class ShelfAgainstLedger(LandedCostTestCase):
    method = CostingMethod.AVERAGE

    def setUp(self):
        super().setUp()
        self.item.costing_method = self.method
        if self.method == CostingMethod.STANDARD:
            self.item.standard_cost = D("4.005")
        self.item.save()
        from apps.core.models import Company
        ppv = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        company = Company.get()
        company.purchase_price_variance_account = ppv
        company.default_cogs_account = Account.objects.create(code='5001', name='COGS', account_type=AccountType.EXPENSE)
        company.save()
        self.shrink_acc = Account.objects.create(code="6200", name="Shrink", account_type=AccountType.EXPENSE)
        self.shrink = AdjustmentReason.objects.create(code="SH", name="Shrink", account=self.shrink_acc,
                                                      direction=AdjustmentDirection.DECREASE)

    def agree(self, label):
        shelf = sum((self.item.valuation_at(w)[1] for w in Warehouse.objects.all()), D("0"))
        self.assertEqual(shelf, self.balance(self.inventory), label)
        for w in Warehouse.objects.all():
            self.assertEqual(replay(self.item, w, before_id=10 ** 9), self.item.valuation_at(w), label + " fold")

    def write_off(self, quantity):
        a = StockAdjustment.objects.create(adjustment_date=datetime.date(2026, 3, 1), warehouse=self.warehouse,
                                           reason=self.shrink)
        StockAdjustmentLine.objects.create(adjustment=a, item=self.item, uom=self.uom, quantity=-D(quantity))
        a.post()
        return a

    def receive_part(self, order, quantity, line=None):
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 5))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line or order.lines.filter(charge__isnull=True).first(),
                                        warehouse=self.warehouse, quantity_received=D(quantity))
        receipt.post()
        return receipt

    def test_receipts_returns_at_awkward_prices(self):
        for price, qtys in (("4.01", ("2.5", "3.3333")), ("3.3333", ("7", "1.1")), ("95.8351", ("0.3", "2.7"))):
            order = self.make_order("20", price)
            receipts = [self.receive_part(order, q) for q in qtys]
            self.agree(f"receive {price}")
            for r, q in zip(receipts, qtys):
                line = r.lines.get()
                r.create_return(quantities={line: D(q) / 3}, debit_bills=False)
                self.agree(f"partial return {price} {q}")
                r2 = r
            self.write_off("1.0001")
            self.agree("write off")

    def test_landed_cost_then_a_sale_then_a_take_back(self):
        order = self.make_order("7", "4.01")
        order.add_charge(self.freight, D("10.00"))
        r1 = self.receive_part(order, "3")
        r2 = self.receive_part(order, "4")
        self.agree("received")
        self.write_off("2")
        self.agree("written off")
        bill = order.create_bill(self.payable)
        bill.post()
        self.agree("landed")
        freight = bill.lines.get(charge=self.freight)
        bill.create_debit_note(memo="Refunded", quantities={freight: D("1")})
        self.agree("freight refunded")

    def test_landed_cost_and_a_return(self):
        order = self.make_order("7", "4.01")
        order.add_charge(self.freight, D("10.00"))
        r1 = self.receive_part(order, "3")
        r2 = self.receive_part(order, "4")
        bill = order.create_bill(self.payable)
        bill.post()
        self.agree("landed")
        r1.create_return(quantities={r1.lines.get(): D("2")}, debit_bills=True)
        self.agree("returned after landing")
        self.write_off("3")
        self.agree("written off after")


class ShelfAgainstLedgerFifo(ShelfAgainstLedger):
    method = CostingMethod.FIFO


class ShelfAgainstLedgerStandard(ShelfAgainstLedger):
    method = CostingMethod.STANDARD

    def test_landed_cost_then_a_sale_then_a_take_back(self):
        super().test_landed_cost_then_a_sale_then_a_take_back()


from .tests_subcontract import SubcontractTestCase
from apps.core.models import Company
from apps.inventory.models import StockMovement


class InCases(LandedCostTestCase):
    """Ordered in cases of 12 at a price per case that does not divide, shelf in eaches."""

    def test_cases(self):
        from apps.core.models import Company
        company = Company.get()
        company.purchase_price_variance_account = Account.objects.create(code="5900", name="PPV", account_type=AccountType.EXPENSE)
        company.save()
        case = UnitOfMeasure.objects.create(code="case", name="Case", category=UnitOfMeasureCategory.COUNT
                                            if hasattr(UnitOfMeasureCategory, "COUNT") else None,
                                            base_unit=self.uom, conversion_factor=D("12"))
        for method in (CostingMethod.AVERAGE, CostingMethod.FIFO):
            # Written, not saved: a costing method no longer changes over stock (O330), and the probe runs both
            # methods over one shelf.
            type(self.item).objects.filter(pk=self.item.pk).update(costing_method=method)
            self.item.refresh_from_db()
            order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 1))
            line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=case, quantity=D("30"),
                                                    unit_price=D("7.01"))
            order.confirm()
            receipts = []
            for q in ("7.5", "3.3333"):
                r = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 5))
                GoodsReceiptLine.objects.create(receipt=r, order_line=line, warehouse=self.warehouse,
                                                quantity_received=D(q))
                r.post()
                receipts.append(r)
                shelf = self.item.valuation_at(self.warehouse)[1]
                self.assertEqual(shelf, self.balance(self.inventory), f"{method} receipt {q}")
            receipts[0].create_return(quantities={receipts[0].lines.get(): D("2.25")}, debit_bills=False)
            self.assertEqual(self.item.valuation_at(self.warehouse)[1], self.balance(self.inventory), f"{method} return")
