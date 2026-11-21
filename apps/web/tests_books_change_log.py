"""
Migration web 0001 (apps/core/ledger_log.py): its reverse drops the books tables' triggers and its functions and
keeps the log while it holds a row; run forward again it makes them again, changing nothing that stood.
"""

import datetime
import unittest

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.core.ledger_log import BOOKS_TABLES, LedgerChange, create, present, tables_without_their_trigger


@tag("migration")
@unittest.skipUnless(connection.vendor == "postgresql", "the database's own log is PostgreSQL's")
class TheBooksLogMigrationTests(TransactionTestCase):
    def test_back_drops_the_triggers_and_keeps_the_rows_forward_makes_them_again(self):
        from apps.accounting.models import JournalEntry

        self.assertEqual(tables_without_their_trigger(connection), [])
        create(connection)  # run twice: nothing changes
        self.assertEqual(tables_without_their_trigger(connection), [])
        entry = JournalEntry.objects.create(date=datetime.date(2026, 10, 1), memo="Opening")
        kept = LedgerChange.objects.filter(table_name="accounting_journalentry", row_id=str(entry.pk)).count()
        self.assertEqual(kept, 1)
        executor = MigrationExecutor(connection)
        executor.migrate([("web", None)])
        self.assertEqual(tables_without_their_trigger(connection, BOOKS_TABLES), list(BOOKS_TABLES))
        self.assertTrue(present(connection))  # it holds a row, so it stays
        self.assertEqual(LedgerChange.objects.filter(row_id=str(entry.pk), table_name="accounting_journalentry").count(), kept)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("web"))
        self.assertEqual(tables_without_their_trigger(connection), [])
        JournalEntry.objects.filter(pk=entry.pk).update(memo="Again")
        self.assertEqual(LedgerChange.objects.filter(row_id=str(entry.pk), table_name="accounting_journalentry").count(), 2)
