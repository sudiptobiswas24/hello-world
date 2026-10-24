"""O287, decided: migration 0079 keeps on every invoice posted before 0078 the B2CL limit as it stood then."""
import contextlib
import io
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag("migration")
class TheKeptLimitIsFilledMigrationTests(TransactionTestCase):
    before = [("sales", "0078_b2cl_limit_kept_at_posting")]
    after = [("sales", "0079_b2cl_limit_filled")]

    def test_posted_invoices_take_the_limit_as_it_stands_and_the_count_is_said(self):
        from apps.e2e.tests_races import fixture
        from apps.gst.tests import GstReturnTestCase

        from .models import Invoice, InvoiceLine

        MigrationExecutor(connection).migrate(self.before)
        try:
            made = fixture(self, GstReturnTestCase)
            retail = made.party("KA-UNREG", gst_state="29", gst_registration="unregistered")
            first = made.sell(retail, "150000", taxes=[made.igst])
            second = made.sell(retail, "90000", taxes=[made.igst])
            draft = Invoice.objects.create(customer=retail, invoice_date=first.invoice_date,
                                           receivable_account=made.ar, currency=made.inr)
            InvoiceLine.objects.create(invoice=draft, item=made.sack, quantity=Decimal("1"),
                                       unit_price=Decimal("5"), revenue_account=made.revenue)
            # As posted before 0078 kept anything; the limit has since been raised.
            Invoice.objects.filter(pk__in=[first.pk, second.pk]).update(b2cl_limit=None)
            made.settings.b2cl_limit = Decimal("250000")
            made.settings.save()
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                MigrationExecutor(connection).migrate(self.after)
            kept = dict(Invoice.objects.values_list("pk", "b2cl_limit"))
            self.assertEqual((kept[first.pk], kept[second.pk], kept[draft.pk]),
                             (Decimal("250000.00"), Decimal("250000.00"), None))
            self.assertIn("kept the B2CL limit 250000.00 on 2 posted invoice(s)", said.getvalue())
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
