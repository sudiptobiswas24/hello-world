"""
The plant's day, not Greenwich's.

At 01:30 on 4 October in Kolkata it is still 3 October in UTC. Anything
the plant does then - a reversal, a return, a reading - happened on the
4th. Found running the suite on Asia/Kolkata: a sales return booked
after midnight was dated the day before.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine

from .models import to_date

# 20:00 UTC on the 3rd is 01:30 IST on the 4th.
AFTER_MIDNIGHT = datetime.datetime(2026, 10, 3, 20, 0, tzinfo=datetime.timezone.utc)
PLANT_DAY = datetime.date(2026, 10, 4)


@override_settings(TIME_ZONE="Asia/Kolkata")
class ThePlantsDayTests(TestCase):
    def test_a_moment_falls_on_the_plants_day(self):
        self.assertEqual(to_date(AFTER_MIDNIGHT), PLANT_DAY)

    def test_a_naive_moment_is_taken_as_written(self):
        self.assertEqual(to_date(datetime.datetime(2026, 10, 3, 23, 0)),
                         datetime.date(2026, 10, 3))

    def test_what_is_done_after_midnight_is_dated_that_day(self):
        cash = Account.objects.create(code="1100", name="Cash", account_type=AccountType.ASSET)
        sales = Account.objects.create(code="4000", name="Sales",
                                       account_type=AccountType.INCOME)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 10, 1), memo="Sale")
        JournalLine.objects.create(entry=entry, account=cash, debit=Decimal("10"))
        JournalLine.objects.create(entry=entry, account=sales, credit=Decimal("10"))
        entry.post()
        with patch.object(timezone, "now", return_value=AFTER_MIDNIGHT):
            reversal = entry.create_reversal()
        self.assertEqual(reversal.date, PLANT_DAY)
