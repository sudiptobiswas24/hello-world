"""Review of fix_money2 (purchasing): probes."""
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import GoodsReceipt, GoodsReceiptLine
from .tests import PurchasingTestCase


class ReturnToVendor(PurchasingTestCase):
    def test_return_with_inventory_or_grni_off(self):
        line = self.make_po_line(Decimal("10"))
        receipt = GoodsReceipt.objects.create(purchase_order=line.order, receipt_date="2026-01-05")
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=line, warehouse=self.warehouse,
                                        quantity_received=Decimal("6"))
        receipt.post()
        self.grni.is_active = False
        self.grni.save()
        try:
            receipt.create_return()
            print("PURCHASE RETURN with GRNI off: ok")
        except ValidationError as exc:
            print("PURCHASE RETURN with GRNI off REFUSED:", exc.messages)
            self.fail("return refused")
