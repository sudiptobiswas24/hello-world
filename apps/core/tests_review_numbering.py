"""Review probes for O257 (numbering behind one row). Probes assert what SHOULD be true."""
import datetime

from django.db import connection, transaction
from django.test import TransactionTestCase, tag

from apps.core.models import DocumentNumbering, DocumentSequence, DocumentSequenceYear


class Boom(Exception):
    pass


class NumberingProbes(TransactionTestCase):
    def mk(self, code, prefix, yearly=True):
        return DocumentSequence.objects.create(code=code, name=code, prefix=prefix)

    def test_p1_a_rolled_back_posting_gives_its_number_back(self):
        self.mk("t.a", "A-")
        day = datetime.date(2026, 5, 1)
        try:
            with transaction.atomic():
                DocumentSequence.next_for("t.a", day)
                raise Boom
        except Boom:
            pass
        self.assertEqual(DocumentSequence.next_for("t.a", day), "A-2026-00001")
        self.assertEqual(DocumentSequence.next_for("t.a", day), "A-2026-00002")

    def test_p2_a_nested_number_of_a_second_type_rolls_back_with_the_first(self):
        self.mk("t.a", "A-")
        self.mk("t.b", "B-")
        day = datetime.date(2026, 5, 1)
        DocumentSequence.next_for("t.a", day), DocumentSequence.next_for("t.b", day)  # 00001 each
        try:
            with transaction.atomic():
                DocumentSequence.next_for("t.a", day)
                with transaction.atomic():  # a savepoint that survives
                    DocumentSequence.next_for("t.b", day)
                raise Boom
        except Boom:
            pass
        self.assertEqual((DocumentSequence.next_for("t.a", day), DocumentSequence.next_for("t.b", day)),
                         ("A-2026-00002", "B-2026-00002"))

    def test_p2b_an_inner_failure_caught_by_the_caller_gives_only_its_own_number_back(self):
        self.mk("t.a", "A-")
        self.mk("t.b", "B-")
        day = datetime.date(2026, 5, 1)
        with transaction.atomic():
            first = DocumentSequence.next_for("t.a", day)
            try:
                with transaction.atomic():
                    DocumentSequence.next_for("t.b", day)
                    raise Boom
            except Boom:
                pass
            second = DocumentSequence.next_for("t.b", day)
        self.assertEqual((first, second), ("A-2026-00001", "B-2026-00001"))

    def test_p3_year_boundary_counts_per_year_and_a_rolled_back_first_of_the_year_is_not_a_gap(self):
        self.mk("t.a", "A-")
        self.assertEqual(DocumentSequence.next_for("t.a", datetime.date(2026, 12, 31)), "A-2026-00001")
        try:
            with transaction.atomic():
                self.assertEqual(DocumentSequence.next_for("t.a", datetime.date(2027, 1, 1)), "A-2027-00001")
                raise Boom
        except Boom:
            pass
        self.assertEqual(DocumentSequence.next_for("t.a", datetime.date(2026, 12, 31)), "A-2026-00002")
        self.assertEqual(DocumentSequence.next_for("t.a", datetime.date(2027, 1, 1)), "A-2027-00001")
        self.assertEqual(sorted(DocumentSequenceYear.objects.values_list("year", "next_number")),
                         [(2026, 3), (2027, 2)])

    def test_p4_a_first_number_of_a_type_that_rolls_back_leaves_no_sequence_behind_to_skip(self):
        try:
            with transaction.atomic():
                DocumentSequence.next_for("t.new", datetime.date(2026, 5, 1), name="New", prefix="N-")
                raise Boom
        except Boom:
            pass
        self.assertEqual(DocumentSequence.next_for("t.new", datetime.date(2026, 5, 1), name="New", prefix="N-"),
                         "N-2026-00001")

    def test_p5_a_save_that_numbers_and_fails_leaves_no_gap(self):
        # Changed by fix_g23b3 (decided by the coordinator): next_for opens its own transaction, as every method
        # that locks does, so a number asked for outside one is taken, not refused. What GST Rule 46(b) needs is
        # that the caller numbers and saves in one transaction: a lead refused after its number leaves no gap.
        from django.core.exceptions import ValidationError

        from apps.sales.crm import Lead, LeadStatus

        refused = Lead(company_name="Acme", status=LeadStatus.LOST)
        with self.assertRaises(ValidationError):
            refused.save()
        self.assertEqual(refused.number, "", "the number the refused save took was left on the object")
        made = Lead(company_name="Acme")
        made.save()
        self.assertTrue(made.number.endswith("-00001"), made.number)

    def test_p5b_hold_outside_a_transaction_refuses(self):
        with self.assertRaises(RuntimeError):
            DocumentNumbering.hold()

    def test_p6_the_row_is_there_after_a_flush_and_a_missing_one_is_made_not_a_500(self):
        self.assertTrue(DocumentNumbering.objects.filter(pk=1).exists(), "row absent at the start of a test after a flush")
        DocumentNumbering.objects.all().delete()
        self.mk("t.a", "A-")
        with transaction.atomic():
            self.assertEqual(DocumentSequence.next_for("t.a", datetime.date(2026, 5, 1)), "A-2026-00001")
        self.assertEqual(DocumentNumbering.objects.count(), 1)


@tag("migration")
class Migration0042Probes(TransactionTestCase):
    before = [("core", "0041_secondfactor_sealed_secret")]
    after = [("core", "0042_documentnumbering")]

    def test_forward_back_forward(self):
        from django.db.migrations.executor import MigrationExecutor

        def at(targets):
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
            return MigrationExecutor(connection)

        try:
            ex = at(self.before)
            self.assertNotIn("core_documentnumbering", connection.introspection.table_names())
            ex = at(self.after)
            rows = ex.loader.project_state(self.after).apps.get_model("core", "DocumentNumbering")
            self.assertEqual(list(rows.objects.values_list("pk", flat=True)), [1])
            ex = at(self.before)
            self.assertNotIn("core_documentnumbering", connection.introspection.table_names())
            ex = at(self.after)
            rows = ex.loader.project_state(self.after).apps.get_model("core", "DocumentNumbering")
            self.assertEqual(list(rows.objects.values_list("pk", flat=True)), [1])
            # forward again over a row already there (a database that had the table and was migrated twice)
            ex.loader.project_state(self.after).apps.get_model("core", "DocumentNumbering").objects.get_or_create(pk=1)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        # and with the leaf applied, a number comes out (post_migrate row)
        with transaction.atomic():
            DocumentSequence.next_for("t.m", datetime.date(2026, 5, 1), name="M", prefix="M-")
