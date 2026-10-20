"""Second audit, lane money: the books' opening brought in once."""

import datetime

from apps.accounting.models import Account, AccountType, BooksOpening, JournalEntry

from .importer import run
from .tests import GO_LIVE, ImportTestCase


class OpeningBalancesOnceTests(ImportTestCase):
    def test_the_same_opening_balances_brought_in_twice_are_refused_the_second_time(self):
        """M17: no guard: a second run (a re-click, a re-upload after a failed one elsewhere) posts the whole entry again."""
        bank = Account.objects.get(code="1010")
        BooksOpening.objects.create(opened_on=GO_LIVE, opening_account=self.opening)
        file = "account,debit,credit\n1010,250000,\n3900,,250000\n"
        first = run("opening_balances", file, commit=True, date=GO_LIVE)
        self.assertTrue(first.committed, first.errors)
        second = run("opening_balances", file, commit=True, date=GO_LIVE)
        self.assertFalse(second.committed)
        self.assertEqual(self.balance(bank), 250000)
