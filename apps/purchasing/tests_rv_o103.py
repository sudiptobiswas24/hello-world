import datetime
from decimal import Decimal as D
from apps.purchasing.tests_msme import MsmeTestCase, JUNE
from apps.purchasing.models import Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, ReceiptInspection


def show(*a):
    print("\nRV", *a)


class O103(MsmeTestCase):
    def order(self, qty="10"):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=JUNE)
        line = PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D(qty), unit_price=D("100"))
        order.confirm()
        return order, line

    def receipt(self, order, line, day, qty):
        r = GoodsReceipt.objects.create(purchase_order=order, receipt_date=day)
        rl = GoodsReceiptLine.objects.create(receipt=r, order_line=line, warehouse=self.warehouse, quantity_received=D(qty))
        r.post()
        return r, rl

    def billfor(self, order, line, day, qty):
        bill = Bill.objects.create(vendor=self.vendor, bill_date=day, payable_account=self.payable, payment_terms=self.net60, purchase_order=order)
        BillLine.objects.create(bill=bill, order_line=line, item=self.item, quantity=D(qty), unit_price=D("100"), expense_account=self.expense)
        bill.post()
        return Bill.objects.get(pk=bill.pk)

    def test_routine_inspection_pushes_the_date(self):
        order, line = self.order()
        r, rl = self.receipt(order, line, datetime.date(2026, 6, 10), "10")
        ReceiptInspection.objects.create(receipt_line=rl, quantity=D("10"), accepted=True, inspected_on=datetime.date(2026, 6, 20))
        bill = self.billfor(order, line, datetime.date(2026, 6, 25), "10")
        show("A: received 10 Jun, routine QC pass 20 Jun (no objection to supplier), net60 bill 25 Jun -> pay_by", bill.pay_by(),
             "| Act reading (delivery 10 Jun + 45) = ", datetime.date(2026, 6, 10) + datetime.timedelta(days=45))

    def test_two_deliveries_one_bill(self):
        order, line = self.order()
        self.receipt(order, line, datetime.date(2026, 6, 1), "6")
        self.receipt(order, line, datetime.date(2026, 6, 20), "4")
        bill = self.billfor(order, line, datetime.date(2026, 6, 25), "10")
        show("B: 6 received 1 Jun, 4 received 20 Jun, one bill 25 Jun -> pay_by", bill.pay_by(),
             "| first lot's 45th day:", datetime.date(2026, 6, 1) + datetime.timedelta(days=45))

    def test_second_bill_for_second_lot(self):
        order, line = self.order()
        self.receipt(order, line, datetime.date(2026, 6, 1), "6")
        self.receipt(order, line, datetime.date(2026, 6, 20), "4")
        b1 = self.billfor(order, line, datetime.date(2026, 6, 5), "6")
        b2 = self.billfor(order, line, datetime.date(2026, 6, 25), "4")
        show("C: bill1 pay_by", b1.pay_by(), "bill2 pay_by", b2.pay_by())

    def test_edit_vendor_category_and_debit_note(self):
        from apps.accounting.models import PartyTaxProfile
        bill = self.billfor(*self.order(), datetime.date(2026, 6, 5), "10")
        PartyTaxProfile.objects.filter(party=self.vendor).update(msme_category="medium")
        show("D: vendor reclassified medium after posting; bill pay_by", bill.pay_by(), "category kept", bill.msme_category)
        note = bill.create_debit_note()
        show("D: debit note category", note.msme_category, "pay_by", note.pay_by())
