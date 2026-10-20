"""Review probes for O280 / O313 (review_blockers). Not product tests."""
import datetime
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase, tag
import unittest

from apps.accounting.models import Account, AccountType, BooksOpening, JournalEntry
from apps.core.models import Company
from apps.inventory.adjustments import AdjustmentReason, StockAdjustment
from apps.e2e.tests_races import race, fixture

from .importer import run
from .tests import GO_LIVE, ImportTestCase

BAL = "account,debit,credit\n1010,250000,\n3900,,250000\n"
OTHER = "account,debit,credit\n1200x,,\n"


class Probes(ImportTestCase):
    def test_second_file_of_other_accounts(self):
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        Account.objects.create(code="2500", name="Loan", account_type=AccountType.LIABILITY)
        self.assertTrue(run("opening_balances", BAL, commit=True, date=GO_LIVE).committed)
        other = "account,debit,credit\n2500,,1000\n3900,1000,\n"
        r = run("opening_balances", other, commit=True, date=GO_LIVE)
        print("OTHER ACCOUNTS 2nd:", r.committed, r.errors)

    def test_reverse_then_second_then_third(self):
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        self.assertTrue(run("opening_balances", BAL, commit=True, date=GO_LIVE).committed)
        JournalEntry.objects.get(reference="Opening balances").reverse_by_hand(memo="x")
        self.assertTrue(run("opening_balances", BAL, commit=True, date=GO_LIVE).committed)
        r = run("opening_balances", BAL, commit=True, date=GO_LIVE)
        self.assertFalse(r.committed)
        print("entries", JournalEntry.objects.filter(reference="Opening balances").count())

    def test_dry_run_then_commit_ok_and_failed_file_leaves_no_entry(self):
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        bad = "account,debit,credit\n1010,5,\n3900,,6\n"
        self.assertFalse(run("opening_balances", bad, commit=True, date=GO_LIVE).committed)
        self.assertEqual(JournalEntry.objects.filter(reference="Opening balances").count(), 0)
        self.assertTrue(run("opening_balances", BAL, commit=True, date=GO_LIVE).committed)

    def test_open_invoice_twice_and_after_credit(self):
        csv = "customer,reference,date,amount\nC-1,INV-9,2026-09-01,100\n"
        from apps.imports.importer import PER_ROW
        import inspect
        print("open_invoices cols:", __import__("apps.imports.importer", fromlist=["x"]).__dict__.get("COLUMNS", None) and "")

    def test_stock_other_warehouse_lot_and_two_rows(self):
        AdjustmentReason.objects.create(code="OPENING", name="o", account=self.opening)
        S = "sku,warehouse,quantity,unit_cost,lot\nWDG-1,WH1,10,3,L1\nWDG-1,WH1,10,3,L1\n"
        r = run("opening_stock", S, commit=True, date=GO_LIVE, reason="OPENING")
        print("stock dup rows same file:", r.committed, r.errors)
        r = run("opening_stock", "sku,warehouse,quantity,unit_cost,lot\nWDG-1,WH1,10,3,L1\n", commit=True, date=GO_LIVE, reason="OPENING")
        print("stock again lot:", r.committed, r.errors)
        r = run("opening_stock", "sku,warehouse,quantity,unit_cost,lot\nWDG-1,WH1,10,3,L2\n", commit=True, date=GO_LIVE, reason="OPENING")
        print("stock other lot:", r.committed, r.errors)
        # a second file with a good new row and an already-brought-in row: all refused?
        r = run("opening_stock", "sku,warehouse,quantity,unit_cost,lot\nWDG-1,WH1,10,3,L2\nWDG-1,WH1,5,3,L9\n", commit=True, date=GO_LIVE, reason="OPENING")
        print("stock mixed:", r.committed, r.errors)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL")
class Races(TransactionTestCase):
    def setUp(self):
        f = fixture(self, ImportTestCase)
        self.opening, self.balance = f.opening, f.balance

    def test_two_opening_balance_files_at_once(self):
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        out = race(JournalEntry, *[lambda: run("opening_balances", BAL, commit=True, date=GO_LIVE)] * 2)
        bank = Account.objects.get(code="1010")
        print("RACE balances:", out, "bank", self.balance(bank))
        self.assertEqual(self.balance(bank), 250000)

    def test_two_opening_stock_files_at_once(self):
        AdjustmentReason.objects.create(code="OPENING", name="o", account=self.opening)
        out = race(StockAdjustment, *[lambda: run("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,1000,3.5\n", commit=True, date=GO_LIVE, reason="OPENING")] * 2)
        from apps.inventory.models import StockMovement
        from django.db.models import Sum
        print("RACE stock:", [getattr(o, 'committed', o) for o in out], StockMovement.objects.aggregate(t=Sum("quantity")))
