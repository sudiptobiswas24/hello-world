"""
O250: sales 0086 makes each discount for paying early taken before it a row of its own, with its entry, its
day and its withdrawal; no payment is guessed. Back, an invoice keeps one in its fields again, and one holding
two refuses to go back rather than lose one.
"""

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.e2e.tests_races import fixture

BEFORE, AFTER = ("sales", "0085_where_goods_went"), ("sales", "0086_a_discount_a_row")


@tag("migration")
class EachInvoiceDiscountBecomesItsRowTests(TransactionTestCase):
    # Not serialized_rollback, as the other migration tests: the schema is put back to the latest instead.

    def setUp(self):
        from .tests_discount_returned import DiscountReturnedTests

        self.made = fixture(self, DiscountReturnedTests)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_a_discount_and_its_withdrawal_come_through_as_its_row(self):
        from .models import InvoiceDiscount
        from .tests_discount_returned import RETURNED

        invoice, (cheque,) = self.made.discounted("980")
        cheque.void(memo="Returned unpaid", on_date=RETURNED)
        kept = InvoiceDiscount.objects.get(invoice=invoice)

        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        Invoice = executor.loader.project_state([BEFORE]).apps.get_model("sales", "Invoice")
        self.assertEqual(Invoice.objects.filter(pk=invoice.pk).values_list(
            "settlement_discount_amount", "settlement_discount_entry_id", "settlement_discount_withdrawn",
            "settlement_discount_withdrawal_entry_id").get(),
            (Decimal("20.00"), kept.journal_entry_id, Decimal("20.00"), kept.withdrawal_entry_id))
        MigrationExecutor(connection).migrate([AFTER])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        rows = [(row.payment_id, row.date, row.amount, row.journal_entry_id, row.withdrawn_amount,
                 row.withdrawal_entry_id) for row in InvoiceDiscount.objects.all()]
        self.assertEqual(rows, [(None, kept.date, Decimal("20.00"), kept.journal_entry_id, Decimal("20.00"),
                                 kept.withdrawal_entry_id)])

    def test_an_invoice_holding_two_does_not_go_back(self):
        from .models import InvoiceDiscount
        from .tests_discount_returned import PAID

        made = self.made
        invoice = made.bill(made.make_order("10", "100"))
        for amount in ("490", "490"):
            receipt = made.receipt(amount, on=PAID)
            made.allocate(receipt, invoice, amount)
            invoice.apply_settlement_discount(payment=receipt)
        with self.assertRaisesMessage(RuntimeError, "more than one settlement discount"):
            MigrationExecutor(connection).migrate([BEFORE])
        self.assertEqual(InvoiceDiscount.objects.filter(invoice=invoice).count(), 2)
