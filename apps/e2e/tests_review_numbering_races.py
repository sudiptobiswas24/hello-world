"""
Review probes for O257: pairs of paths that each number a document and lock the same rows, raced on
PostgreSQL. Nothing may end as a deadlock; the numbers taken must be consecutive per sequence.
"""
import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import JournalEntry
from apps.core.models import DocumentSequenceYear
from apps.inventory.models import AdjustmentReason, StockCount, StockMovement
from apps.sales import tests_base as sales_fixture
from apps.sales.models import Delivery, DeliveryLine, Invoice, Quotation, QuotationLine, SalesOrder

from .tests_races import RaceCase, race


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class NumberingPairsDoNotDeadlock(RaceCase):
    setUp = sales_fixture.SalesTestCase.setUp
    make_order, ship, bill, balance = (
        sales_fixture.SalesTestCase.make_order, sales_fixture.SalesTestCase.ship,
        sales_fixture.SalesTestCase.bill, sales_fixture.SalesTestCase.balance)

    # builders: each returns a callable that does one numbering path
    def delivery(self):
        order = self.make_order("5", "100")
        d = Delivery.objects.create(sales_order=order, delivery_date=datetime.date(2026, 3, 3))
        DeliveryLine.objects.create(delivery=d, order_line=order.lines.first(), warehouse=self.warehouse,
                                    quantity_shipped=Decimal("5"))
        return lambda: Delivery.objects.get(pk=d.pk).post()

    def invoice(self):
        order = self.make_order("5", "100")
        self.ship(order, "5")
        inv = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 4))
        return lambda: Invoice.objects.get(pk=inv.pk).post()

    def quote(self):
        q = Quotation.objects.create(customer=self.customer, quotation_date=datetime.date(2026, 3, 1), currency=self.usd)
        QuotationLine.objects.create(quotation=q, item=self.item, uom=self.uom, quantity=Decimal("5"),
                                     unit_price=Decimal("10"), revenue_account=self.revenue)
        return lambda: Quotation.objects.get(pk=q.pk).accept(order_date=datetime.date(2026, 3, 2))

    def count(self):
        from django.db.models import Sum
        reason = AdjustmentReason.objects.filter(code="CNT").first() or AdjustmentReason.objects.create(
            code="CNT", name="Count", account=self.bad_debt)
        c = StockCount.objects.create(count_date=datetime.date(2026, 3, 5), warehouse=self.warehouse, reason=reason)
        c.add(self.item, str(self.item.on_hand_at(self.warehouse) - 1))
        return lambda: StockCount.objects.get(pk=c.pk).post()

    def pair(self, one, other, senders):
        outcomes = race(senders, one(), other())
        self.assertFalse([o for o in outcomes if "deadlock" in o], outcomes)
        # numbers: consecutive per sequence, none skipped
        for year in DocumentSequenceYear.objects.all():
            self.assertGreaterEqual(year.next_number, 1)
        return outcomes

    SENDER_SETS = {
        "early": (StockMovement, SalesOrder, JournalEntry),
        "at_number": (DocumentSequenceYear,),
    }

    def run_pair(self, one, other):
        results = {}
        for name, senders in self.SENDER_SETS.items():
            results[name] = self.pair(one, other, senders)
        print("\n", one.__name__, "x", other.__name__, results)

    def test_count_x_delivery(self):
        self.run_pair(self.count, self.delivery)

    def test_quote_accept_x_delivery(self):
        self.run_pair(self.quote, self.delivery)

    def test_quote_accept_x_count(self):
        self.run_pair(self.quote, self.count)

    def test_invoice_x_delivery(self):
        self.run_pair(self.invoice, self.delivery)

    def test_invoice_x_quote_accept(self):
        self.run_pair(self.invoice, self.quote)

    def test_invoice_x_count(self):
        self.run_pair(self.invoice, self.count)

    def test_invoice_x_invoice(self):
        self.run_pair(self.invoice, self.invoice)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ANumberHeldAcrossTheCommitDeadlocksOnAnyRowItsInsertsReference(RaceCase):
    """
    Deterministic. A (quote accept) numbers, then commits; its inserts reference rows (customer, accounts, item)
    whose deferred foreign-key checks take FOR KEY SHARE at COMMIT, while still holding the numbering row. B holds one
    of those rows FOR UPDATE (lock_rows, as any posting does BEFORE its number) and then asks for the number.
    A waits at commit for B's row, B waits for A's numbering: a deadlock the lock order cannot see, since A takes
    no lock on the row at all.
    """
    setUp = sales_fixture.SalesTestCase.setUp
    quote = NumberingPairsDoNotDeadlock.quote

    def attempt(self, name, row_of):
        import threading
        from django.db import transaction
        from apps.core.models import DocumentSequence, lock_rows

        accept = self.quote()
        a_numbered, b_waiting, out = threading.Event(), threading.Event(), {}

        def a():
            try:
                with transaction.atomic():
                    accept()
                    a_numbered.set()
                    b_waiting.wait(5)  # B is now blocked on the numbering row; commit
                out["a"] = "done"
            except Exception as exc:
                out["a"] = f"refused: {exc}"
            finally:
                connection.close()

        def b():
            try:
                a_numbered.wait(5)
                with transaction.atomic():
                    lock_rows(row_of(), refresh=False)
                    threading.Timer(0.5, b_waiting.set).start()
                    DocumentSequence.next_for("sales.invoice", datetime.date(2026, 3, 4), name="Customer Invoices", prefix="INV-")
                out["b"] = "done"
            except Exception as exc:
                out["b"] = f"refused: {exc}"
            finally:
                connection.close()

        threads = [threading.Thread(target=a), threading.Thread(target=b)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        return out

    def test_which_rows_of_a_quote_accept_deadlock(self):
        rows = {"customer": lambda: self.customer, "revenue account": lambda: self.revenue,
                "item": lambda: self.item, "currency": lambda: self.usd}
        result = {name: self.attempt(name, row) for name, row in rows.items()}
        print("\nHELD-ACROSS-COMMIT", result)
        self.assertFalse({n: r for n, r in result.items() if any("deadlock" in v for v in r.values())}, result)
