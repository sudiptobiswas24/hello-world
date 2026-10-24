"""
A line of 100 at 10.00 shipped and invoiced: 1,000.00 fetched. Four
credited back: 960.00. The rate is each document's own posted rate.
"""

import datetime
from decimal import Decimal

from .models import Delivery, DeliveryLine, Invoice, SalesOrder, SalesOrderLine
from .tests_base import SalesTestCase

DAY = datetime.date(2026, 3, 1)


class LineRevenueTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.sale = SalesOrder.objects.create(customer=self.customer, order_date=DAY,
                                              currency=self.usd)
        self.line = SalesOrderLine.objects.create(
            order=self.sale, item=self.item, uom=self.uom, quantity=Decimal("100"),
            unit_price=Decimal("10"), revenue_account=self.revenue, warehouse=self.warehouse)
        self.sale.confirm()
        delivery = Delivery.objects.create(sales_order=self.sale, delivery_date=DAY)
        DeliveryLine.objects.create(delivery=delivery, order_line=self.line,
                                    warehouse=self.warehouse, quantity_shipped=Decimal("100"))
        delivery.post()

    def test_billed_less_credited(self):
        self.assertEqual(self.line.revenue_in_base(), Decimal("0.00"))
        invoice = self.sale.create_invoice(self.ar, invoice_date=DAY)
        self.assertEqual(self.line.revenue_in_base(), Decimal("0.00"))
        invoice.post()
        self.assertEqual(self.line.revenue_in_base(), Decimal("1000.00"))
        invoice.create_credit_note(quantities={invoice.lines.get(): Decimal("4")})
        self.assertEqual(self.line.revenue_in_base(), Decimal("960.00"))

    def test_at_the_rate_it_was_posted_at(self):
        invoice = self.sale.create_invoice(self.ar, invoice_date=DAY)
        invoice.post()
        Invoice.objects.filter(pk=invoice.pk).update(exchange_rate=Decimal("83.5"))
        self.assertEqual(self.line.revenue_in_base(), Decimal("83500.00"))
