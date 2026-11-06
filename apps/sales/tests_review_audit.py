import contextlib
import time
from decimal import Decimal

from django.db import connection

from apps.hr.tests_review_audit import trail_off
from .models import Invoice, InvoiceLine
from .tests import SalesTestCase


class Cost(SalesTestCase):
    def build(self, n):
        invoice = Invoice.objects.create(customer=self.customer, invoice_date="2026-01-01",
                                         receivable_account=self.receivable)
        for i in range(n):
            InvoiceLine.objects.create(invoice=invoice, item=self.item, description=f"L{i}", quantity=Decimal("2"),
                                       unit_price=Decimal("50"), revenue_account=self.revenue)
        return invoice

    def test_p31_a_500_line_invoice_with_the_trail_on_and_off(self):
        out = {}
        for off in (False, True):
            ctx = trail_off() if off else contextlib.nullcontext()
            with ctx:
                t = time.perf_counter()
                invoice = self.build(500)
                build = time.perf_counter() - t
                t = time.perf_counter()
                invoice.post()
                out[off] = (build, time.perf_counter() - t)
        print(f"\nPROBE p31 {connection.vendor}: build on {out[False][0]:.2f}s off {out[True][0]:.2f}s; "
              f"post on {out[False][1]:.2f}s off {out[True][1]:.2f}s")
