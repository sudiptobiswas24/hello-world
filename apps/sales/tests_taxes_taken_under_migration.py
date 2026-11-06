"""O446: sales 0084 records, on every document made before it, the position its party has at the migration."""

import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.accounting.models import FiscalPosition, PartyTaxProfile

from .models import Invoice, SalesOrder
from .tests_base import SalesTestCase


@tag("migration")
class ADocumentMadeBeforeTheFieldTests(TransactionTestCase):
    setUp = SalesTestCase.setUp

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_it_is_recorded_under_its_partys_position_then(self):
        lut = FiscalPosition.objects.create(code="LUT", name="Export under LUT")
        PartyTaxProfile.objects.update_or_create(party=self.customer, defaults={"fiscal_position": lut})
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
                                         receivable_account=self.ar)
        order = SalesOrder.objects.create(customer=self.customer, order_date=datetime.date(2026, 3, 1))
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        trust = Party.objects.create(code="C-EX", name="Exempt trust", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=trust, role=PartyRole.CUSTOMER)
        PartyTaxProfile.objects.create(party=trust, fiscal_position=lut, tax_exempt=True, exemption_reference="80G-1")
        exempt = SalesOrder.objects.create(customer=trust, order_date=datetime.date(2026, 3, 1))

        executor = MigrationExecutor(connection)
        executor.migrate([("sales", "0083_index_revisions")])
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

        self.assertEqual(Invoice.objects.get(pk=invoice.pk).taxes_taken_under_id, lut.pk)
        self.assertEqual(SalesOrder.objects.get(pk=order.pk).taxes_taken_under_id, lut.pk)
        self.assertIsNone(SalesOrder.objects.get(pk=exempt.pk).taxes_taken_under_id)
