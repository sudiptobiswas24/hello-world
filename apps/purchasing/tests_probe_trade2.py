"""Review probes, second trading review, purchasing side."""

import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.sales.models import InvoicePolicy, SalesOrder, SalesOrderLine, Delivery

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder
from .tests_drop_ship import ADropShipKeepsWhatItDeliversTests
from .tests_lifecycle import PurchasingLifecycleTestCase


class DropShipRepointProbes(PurchasingLifecycleTestCase):
    drop_ship_fixture = ADropShipKeepsWhatItDeliversTests.drop_ship_fixture

    def test_a_received_drop_ship_order_keeps_the_sales_order_it_delivered_to(self):
        sale, order = self.drop_ship_fixture()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=D("10"))
        receipt.post()
        other = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 1),
                                          currency=self.usd, invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=other, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("10"), revenue_account=self.revenue)
        other.confirm()
        order = PurchaseOrder.objects.get(pk=order.pk)
        order.drop_ship_for = other
        try:
            order.save()
            moved = True
        except ValidationError:
            moved = False
        if moved:
            GoodsReceipt.objects.get(pk=receipt.pk).create_return(debit_bills=False)
        shipped = sale.lines.get().quantity_shipped()
        self.assertFalse(moved, f"drop_ship_for re-pointed after the receipt; its return left the sale line shipped "
                                f"at {shipped} (expected 0 after the return)")


from decimal import Decimal  # noqa: E402

from .models import Bill, BillLine  # noqa: E402
from .tests_audit import ADebitLineDebitsOnlyItsNotesBillTests, JAN  # noqa: E402


class DebitNoteAgainstANote(ADebitLineDebitsOnlyItsNotesBillTests.__mro__[1]):
    billed = ADebitLineDebitsOnlyItsNotesBillTests.billed

    def test_a_debit_note_against_a_debit_note(self):
        bill = self.billed()
        note = bill.create_debit_note(quantities={bill.lines.get(): Decimal("5")})
        second = Bill.objects.create(vendor=self.vendor, bill_date=JAN(12), payable_account=self.payable,
                                     currency=self.usd, debits=note)
        BillLine.objects.create(bill=second, debits_line=note.lines.get(), item=self.item, quantity=Decimal("5"),
                                unit_price=Decimal("5"), expense_account=self.expense)
        try:
            second.post()
            posted = True
        except ValidationError:
            posted = False
        self.assertFalse(posted, f"a debit note against a debit note posted: payable {self.balance(self.payable)}")
