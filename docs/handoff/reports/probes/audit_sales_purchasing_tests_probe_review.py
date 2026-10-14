"""
Review probes for rules A and B (4b3cf73), purchasing side and drop-ship.
"""

import datetime
import unittest
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import connection

from apps.accounting.models import Account, AccountType
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, VendorPrice
from .tests_lifecycle import PurchasingLifecycleTestCase


class DropShipFixture:
    def drop_ship_fixture(self):
        from apps.sales.models import InvoicePolicy, SalesOrder, SalesOrderLine

        self.cogs = Account.objects.create(code="5001", name="COGS", account_type=AccountType.EXPENSE)
        self.revenue = Account.objects.create(code="4000", name="Revenue", account_type=AccountType.INCOME)
        company = Company.get()
        company.default_cogs_account = self.cogs
        company.default_purchase_expense_account = self.expense
        company.save()
        self.customer = Party.objects.create(code="C-1", name="Acme", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.customer, role=PartyRole.CUSTOMER)
        VendorPrice.objects.create(vendor=self.vendor, item=self.item, currency=self.usd, unit_price=D("6"))
        sale = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 1, 1),
                                         currency=self.usd, invoice_policy=InvoicePolicy.DELIVERED)
        SalesOrderLine.objects.create(order=sale, item=self.item, uom=self.uom, quantity=D("10"),
                                      unit_price=D("10"), revenue_account=self.revenue)
        sale.confirm()
        order = PurchaseOrder.create_for_drop_ship(sale, self.vendor, order_date=datetime.date(2026, 1, 2))
        order.confirm()
        return sale, order


class DropShipChangedBeforeItCameProbe(DropShipFixture, PurchasingLifecycleTestCase):
    """Rule A asks what moved; a drop-ship on its way has not moved, and its receipt ships the line as it stands."""

    def test_a_line_awaited_from_a_vendor_keeps_its_item(self):
        sale, order = self.drop_ship_fixture()
        gadget = Item.objects.create(sku="GDG-1", name="Gadget", uom=self.uom)
        line = sale.lines.get()
        line.item = gadget
        try:
            line.save()
        except ValidationError:
            return
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(), warehouse=self.warehouse,
                                        quantity_received=D("10"))
        try:
            receipt.post()
        except ValidationError:
            return
        line.refresh_from_db()
        self.fail(f"the vendor delivered 10 {order.lines.get().item}; the customer's line now reads "
                  f"{line.quantity_shipped()} {line.item} shipped, to be invoiced as such")


    def test_a_received_drop_ship_line_keeps_the_customer_line_it_delivered(self):
        from apps.sales.models import SalesOrderLine

        sale, order = self.drop_ship_fixture()
        first = sale.lines.get()
        # A second line of the same item, added before the drop-ship; its own drop-ship line follows.
        sale2_line = SalesOrderLine.objects.create(order=sale, item=self.item, uom=self.uom, quantity=D("10"),
                                                   unit_price=D("10"), revenue_account=self.revenue)
        from .models import PurchaseOrderLine

        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                         unit_price=D("6"), sales_order_line=sale2_line)
        po_line = order.lines.get(sales_order_line=first)
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=datetime.date(2026, 1, 10))
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=po_line, warehouse=self.warehouse,
                                        quantity_received=D("10"))
        receipt.post()
        po_line = PurchaseOrderLine.objects.get(pk=po_line.pk)
        po_line.sales_order_line = sale2_line
        try:
            po_line.save()
        except ValidationError:
            return
        GoodsReceipt.objects.get(pk=receipt.pk).create_return(debit_bills=False)
        self.fail(f"re-pointed after receipt; the 10 went back to the vendor and the first customer line still "
                  f"reads {SalesOrderLine.objects.get(pk=first.pk).quantity_shipped()} shipped (expected 0), "
                  f"the second {SalesOrderLine.objects.get(pk=sale2_line.pk).quantity_shipped()}")


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class PurchaseLockOrderRaceProbes(__import__("apps.e2e.tests_races", fromlist=["RaceCase"]).RaceCase):
    """The mirror: a return to the vendor debits its bill under the order's lock; a debit note now posts under it too."""

    setUp = PurchasingLifecycleTestCase.setUp
    receive = PurchasingLifecycleTestCase.receive

    def test_a_return_and_a_debit_note_on_one_bill_do_not_deadlock(self):
        from apps.e2e.tests_races import race
        from apps.inventory.models import StockMovement

        from .models import Bill, BillLine, BillPolicy, PurchaseOrderLine

        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=datetime.date(2026, 1, 1),
                                             bill_policy=BillPolicy.RECEIVED)
        PurchaseOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=D("10"),
                                         unit_price=D("5"))
        order.confirm()
        receipt = self.receive(order, "10")
        bill = order.create_bill(self.payable, bill_date=datetime.date(2026, 1, 10))
        bill.post()
        receipt_line, bill_line = receipt.lines.get().pk, bill.lines.get().pk

        def send_back():
            GoodsReceipt.objects.get(pk=receipt.pk).create_return(
                quantities={GoodsReceiptLine.objects.get(pk=receipt_line): D("3")})

        def debit():
            Bill.objects.get(pk=bill.pk).create_debit_note(quantities={BillLine.objects.get(pk=bill_line): D("2")})

        outcomes = race((StockMovement, Bill), send_back, debit)
        self.assertEqual(outcomes, ["done", "done"], "a return of 3 and a debit of 2 on a bill of 10 both stand")
