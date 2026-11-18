"""
O600: purchasing 0095 gives each discount for paying early what the debit notes posted before it undid of it, newest
first over what was not withdrawn, as a note undoes from here. Each note booked the whole beside the newest
discount then, so a bill whose undone discounts were booked to more than one account is named, and so is one
whose notes undid more than its discounts hold.
"""

import io
from contextlib import redirect_stdout
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

from apps.e2e.tests_races import fixture

BEFORE, AFTER = ("purchasing", "0094_one_standing_discount_a_payment"), ("purchasing", "0095_what_a_note_undid_of_each_discount")


@tag("migration")
class WhatEachNoteUndidIsKeptTests(TransactionTestCase):
    # Not serialized_rollback, as the other migration tests: the schema is put back to the latest instead.

    def setUp(self):
        from apps.accounting.tests_settlement_and_reconciliation_facts import SettlementDiscountOnAPartPayment

        self.made = fixture(self, SettlementDiscountOnAPartPayment)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def forward(self, document, more_undone=None):
        """Back to before 0095, the notes' total moved there when asked, and forward again: what it said."""
        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        Document = executor.loader.project_state([BEFORE]).apps.get_model("purchasing", "Bill")
        if more_undone is not None:
            Document.objects.filter(debits_id=document.pk).update(reversed_discount=more_undone)
        with redirect_stdout(io.StringIO()) as said:
            MigrationExecutor(connection).migrate([AFTER])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        return said.getvalue()

    def undone(self, document):
        from .models import BillDiscount

        return {row.payment_id: row.undone_amount for row in BillDiscount.objects.filter(bill=document)}

    def test_a_half_note_undid_the_newest_discount_and_says_nothing(self):
        document, (p1, p2), _accounts = self.made.two_accounts("purchasing")
        self.made.note(document, "0.5")
        said = self.forward(document)
        self.assertEqual(self.undone(document), {p1.pk: Decimal("0.00"), p2.pk: Decimal("10.00")})
        self.assertEqual(said, "")

    def test_a_whole_note_across_two_accounts_is_named(self):
        document, (p1, p2), (first, later) = self.made.two_accounts("purchasing")
        self.made.note(document)
        said = self.forward(document)
        self.assertEqual(self.undone(document), {p1.pk: Decimal("10.00"), p2.pk: Decimal("10.00")})
        self.assertEqual(said.splitlines(), [
            f"purchasing 0095: {document.number}'s debit notes undid discounts booked to {first.code}, {later.code}, "
            "and each booked what it undid beside the newest: those accounts may hold the difference (O600).",
        ])

    def test_notes_that_undid_more_than_the_discounts_hold_are_named(self):
        document, (p1, p2), _accounts = self.made.two_accounts("purchasing")
        self.made.note(document, "0.5")
        said = self.forward(document, more_undone=Decimal("25.00"))
        self.assertEqual(self.undone(document), {p1.pk: Decimal("10.00"), p2.pk: Decimal("10.00")})
        self.assertIn(f"purchasing 0095: {document.number}'s debit notes undid 25.00 of its settlement discounts, 5.00 "
                      "more than they hold after withdrawals; it is set on none.", said)
