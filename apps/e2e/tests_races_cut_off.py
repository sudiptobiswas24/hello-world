"""
A bank's reconciled-from day set as an entry dated before it posts (O616), on PostgreSQL.

JournalEntry.post() asks whether a bank on its lines is reconciled from a later day (O615); Account._check_cut_off()
moves the day. Neither holds the other's rows. What orders them is the company's numbering row: post() is
@serialised, so it locks its entry, and every row lock takes the numbering row first (row_lock_query, O257);
_check_cut_off() holds the account through row_lock_query too. The second to arrive waits for the first to commit,
and reads the day after the wait. Either order ends as the two would one after the other: the day first, and the
backdated entry is refused; the entry first, and the day set after it takes it as cleared, which is the rule as
decided for anything posted before the day is set.
"""

import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine
from apps.core.models import Company, Currency

from .tests_races import RaceCase, race_in_order

MARCH_15, APRIL_1 = datetime.date(2026, 3, 15), datetime.date(2026, 4, 1)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TheDaySetAsABackdatedEntryPostsTests(RaceCase):
    def setUp(self):
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Co", base_currency=inr)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        power = Account.objects.create(code="5200", name="Power", account_type=AccountType.EXPENSE)
        # The power bill of 15 March, keyed by hand and not posted yet: 500 out of the bank.
        self.entry = JournalEntry.objects.create(date=MARCH_15, memo="Power bill, March")
        JournalLine.objects.create(entry=self.entry, account=power, debit=Decimal("500"), credit=Decimal("0"))
        JournalLine.objects.create(entry=self.entry, account=self.bank, debit=Decimal("0"), credit=Decimal("500"))

    def set_the_day(self):
        bank = Account.objects.get(pk=self.bank.pk)
        bank.reconciled_from = APRIL_1
        bank.save()

    def post_the_entry(self):
        JournalEntry.objects.get(pk=self.entry.pk).post()

    def settled(self):
        self.bank.refresh_from_db()
        self.entry.refresh_from_db()
        return self.bank.reconciled_from, self.entry.posted

    def test_the_day_first_refuses_the_entry_dated_before_it(self):
        day, entry = race_in_order((Account, JournalEntry), self.set_the_day, self.post_the_entry)
        self.assertEqual(day, "done")
        self.assertTrue(entry.startswith("refused:") and "reconciled with its statements from 2026-04-01" in entry,
                        entry)
        self.assertEqual(self.settled(), (APRIL_1, False))

    def test_the_entry_first_posts_and_the_day_set_after_takes_it_as_cleared(self):
        entry, day = race_in_order((Account, JournalEntry), self.post_the_entry, self.set_the_day)
        self.assertEqual((entry, day), ("done", "done"))
        self.assertEqual(self.settled(), (APRIL_1, True))
