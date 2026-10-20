"""Second audit, lane money: two people at once. Prove on PostgreSQL (race() is no proof on SQLite)."""

import datetime
from decimal import Decimal

from django.db import connection

from apps.core.models import Company, Currency
from apps.e2e.tests_races import RaceCase, race

from .bank_import import import_lines
from .models import Account, AccountType, BankStatement, BankStatementLine

D = Decimal
FILE = "Date,Narration,Ref No,Withdrawal,Deposit\n05-09-2026,NEFT in,UTR1,,500.00\n06-09-2026,Charges,C1,10.00,\n"


class BankImportRaceTests(RaceCase):
    def test_the_same_file_imported_twice_at_once_adds_its_rows_once(self):
        """M9: import_lines reads the lines already there before it takes any lock; two clicks both see none."""
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Deccan Polysacks", base_currency=inr)
        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        statement = BankStatement.objects.create(
            bank_account=bank, start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30),
            opening_balance=D("0"), closing_balance=D("490.00"))
        mine = lambda: import_lines(BankStatement.objects.get(pk=statement.pk), FILE, commit=True)
        outcomes = race(BankStatementLine, mine, mine)
        self.assertEqual(BankStatementLine.objects.filter(statement=statement).count(), 2, outcomes)
