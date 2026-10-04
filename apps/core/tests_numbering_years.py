"""
Document numbers when the year goes back.

A sequence kept one counter and reset it whenever a document's year
differed from the last one numbered, in either direction. A 2026
invoice reset it to 1; a late 2025 invoice then reset it to 1 again,
and INV-2025-00001 already existed: posting failed on the unique
number. Entering March's documents in April is every year end.
"""

import datetime

from django.test import TestCase, TransactionTestCase, tag

from .models import DocumentSequence


class EachYearCountsOnItsOwnTests(TestCase):
    def number(self, day):
        return DocumentSequence.next_for("test.invoice", day, name="Invoices", prefix="INV-")

    def test_going_back_a_year_carries_on_from_where_that_year_was(self):
        numbers = [self.number(datetime.date(2025, 12, 30)),
                   self.number(datetime.date(2025, 12, 31)),
                   self.number(datetime.date(2026, 1, 2)),
                   self.number(datetime.date(2025, 12, 31)),
                   self.number(datetime.date(2026, 1, 3))]
        self.assertEqual(numbers, ["INV-2025-00001", "INV-2025-00002", "INV-2026-00001",
                                   "INV-2025-00003", "INV-2026-00002"])

    def test_peek_reads_the_year_asked(self):
        self.number(datetime.date(2025, 12, 31))
        self.number(datetime.date(2026, 1, 2))
        sequence = DocumentSequence.objects.get(code="test.invoice")
        self.assertEqual((sequence.peek(datetime.date(2025, 6, 1)),
                          sequence.peek(datetime.date(2026, 6, 1)),
                          sequence.peek(datetime.date(2027, 6, 1))),
                         ("INV-2025-00002", "INV-2026-00002", "INV-2027-00001"))

    def test_a_sequence_that_never_resets_still_counts_on(self):
        sequence = DocumentSequence.objects.create(code="test.plain", name="Plain", prefix="P-",
                                                   include_year=False, reset_yearly=False)
        self.assertEqual([sequence.next_value(datetime.date(2025, 1, 1)),
                          sequence.next_value(datetime.date(2026, 1, 1)),
                          sequence.next_value(datetime.date(2025, 1, 1))],
                         ["P-00001", "P-00002", "P-00003"])


# Slow: it unwinds every later migration and replays them.
@tag("migration")
class CountersCarryAcrossTests(TransactionTestCase):
    before = [("core", "0016_company_net_pay_account")]
    after = [("core", "0017_sequence_counter_per_year")]

    def test_the_current_year_keeps_its_place(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        sequence = apps.get_model("core", "DocumentSequence")
        sequence.objects.create(code="x.yearly", name="Y", current_year=2026, next_number=42)
        sequence.objects.create(code="x.unused", name="U", current_year=None, next_number=1)
        sequence.objects.create(code="x.plain", name="P", reset_yearly=False, next_number=9)

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        year = executor.loader.project_state(self.after).apps.get_model(
            "core", "DocumentSequenceYear")
        self.assertEqual([(row.sequence.code, row.year, row.next_number)
                          for row in year.objects.filter(sequence__code__startswith="x.")],
                         [("x.yearly", 2026, 42)])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
