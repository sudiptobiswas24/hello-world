"""
Out and back through every module, and every account asked at the end.

Bought 100 bags at 10.00 and paid; thirty sent back as faulty and the
vendor refunds 300.00. Sold fifty at 25.00 and paid; twenty come back
torn and the customer is refunded 500.00.

    on the shelf   100 - 30 - 50 + 20 = 40 at 10.00      400.00
    bank           -1,000 + 300 + 1,250 - 500              50.00
    revenue        1,250 - 500                            750.00
    cost of sales  500 - 200                              300.00
    profit                                                450.00 = 400 + 50

Goods received not invoiced, payables and receivables all back at
nothing; the customer and the vendor each owe and are owed nothing.
Written after review found the return paths were where the books
parted: a return to vendor at the wrong cost, refunds of credit notes
nobody had paid, and customer returns that could only be whole.
"""

import datetime
from decimal import Decimal

from django.db.models import Sum

from apps.accounting.models import Account, AccountType, JournalLine, Payment
from apps.core.models import Company, Party, PartyRole, PartyRoleAssignment
from apps.inventory.models import Item
from apps.inventory.reports import reconcile_to_ledger
from apps.purchasing.models import (
    BillPayment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    vendor_balance,
)
from apps.sales.models import (
    Delivery,
    DeliveryLine,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
    outstanding_balance,
)
from apps.sales.tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 2)


class OutAndBackTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.bags = Item.objects.create(sku="SACK-50", name="50 kg sack", uom=self.uom)
        self.vendor = Party.objects.create(code="V-1", name="Sack Co", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)
        self.payable = Account.objects.create(code="2100", name="AP",
                                              account_type=AccountType.LIABILITY)
        self.ppv = Account.objects.create(code="5150", name="Price variance",
                                          account_type=AccountType.EXPENSE)
        company = Company.get()
        company.purchase_price_variance_account = self.ppv
        company.save()
        self.gap = reconcile_to_ledger()["difference"]
        self.bank_before = self.balance(self.bank)

    def balance(self, account):
        row = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            d=Sum("debit"), c=Sum("credit"))
        return (row["d"] or Decimal("0")) - (row["c"] or Decimal("0"))

    def pay(self, party, direction, amount, account):
        payment = Payment.objects.create(party=party, direction=direction, payment_date=DAY,
                                         amount=Decimal(amount), currency=self.usd,
                                         bank_account=self.bank, counterpart_account=account)
        payment.post()
        return payment

    def buy_and_send_back(self):
        order = PurchaseOrder.objects.create(vendor=self.vendor, order_date=DAY, currency=self.usd)
        PurchaseOrderLine.objects.create(order=order, item=self.bags, uom=self.uom,
                                         quantity=Decimal("100"), unit_price=Decimal("10"))
        order.confirm()
        receipt = GoodsReceipt.objects.create(purchase_order=order, receipt_date=DAY)
        GoodsReceiptLine.objects.create(receipt=receipt, order_line=order.lines.get(),
                                        warehouse=self.warehouse,
                                        quantity_received=Decimal("100"))
        receipt.post()
        bill = order.create_bill(self.payable, bill_date=DAY)
        bill.post()
        BillPayment.objects.create(bill=bill, payment=self.pay(
            self.vendor, "disbursement", "1000", self.payable), amount=Decimal("1000"))
        returned = receipt.create_return({receipt.lines.get(): Decimal("30")})
        (note,) = returned.debit_notes_created
        self.assertEqual(note.refund_due(), Decimal("300.00"))
        BillPayment.objects.create(bill=note, payment=self.pay(
            self.vendor, "receipt", "300", self.payable), amount=Decimal("300"))
        return bill, note

    def sell_and_take_back(self):
        order = SalesOrder.objects.create(customer=self.customer, order_date=DAY,
                                          currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.bags, uom=self.uom,
                                      quantity=Decimal("50"), unit_price=Decimal("25"),
                                      revenue_account=self.revenue)
        order.confirm()
        shipment = Delivery.objects.create(sales_order=order, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=shipment, order_line=order.lines.get(),
                                    warehouse=self.warehouse, quantity_shipped=Decimal("50"))
        shipment.post()
        invoice = order.create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        InvoicePayment.objects.create(invoice=invoice, payment=self.pay(
            self.customer, "receipt", "1250", self.ar), amount=Decimal("1250"))
        back = shipment.create_return(quantities={shipment.lines.get(): Decimal("20")})
        (note,) = back.credit_notes_created
        self.assertEqual((invoice.amount_due(), note.refund_due()),
                         (Decimal("0.00"), Decimal("500.00")))
        InvoicePayment.objects.create(invoice=note, payment=self.pay(
            self.customer, "disbursement", "500", self.ar), amount=Decimal("500"))
        return invoice, note

    def test_the_books_after_every_return_and_refund(self):
        bill, debit_note = self.buy_and_send_back()
        invoice, credit_note = self.sell_and_take_back()

        self.assertEqual(self.bags.on_hand_at(self.warehouse), Decimal("40"))
        self.assertEqual(self.bags.valuation_at(self.warehouse)[1], Decimal("400.00000000"))
        self.assertEqual(reconcile_to_ledger()["difference"], self.gap)
        self.assertEqual({
            "bank": self.balance(self.bank) - self.bank_before,
            "revenue": -self.balance(self.revenue),
            "cost of sales": self.balance(self.cogs),
            "goods received not invoiced": self.balance(self.grni),
            "payables": self.balance(self.payable),
            "receivables": self.balance(self.ar),
            "price variance": self.balance(self.ppv),
        }, {
            "bank": Decimal("50.00"), "revenue": Decimal("750.00"),
            "cost of sales": Decimal("300.00"), "goods received not invoiced": Decimal("0"),
            "payables": Decimal("0"), "receivables": Decimal("0"),
            "price variance": Decimal("0"),
        })
        self.assertEqual((outstanding_balance(self.customer), vendor_balance(self.vendor)),
                         (Decimal("0"), Decimal("0")))
        self.assertEqual([doc.amount_due() for doc in (bill, debit_note, invoice, credit_note)],
                         [Decimal("0")] * 4)
