"""
An entry reversed twice.

create_reversal() never asked whether the entry had been reversed; the
journal API's reverse action took it back once per click.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine


class AnEntryIsReversedOnceTests(TestCase):
    def setUp(self):
        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
        self.sales = Account.objects.create(code="4000", name="Sales",
                                            account_type=AccountType.INCOME)
        self.entry = JournalEntry.objects.create(date="2026-03-01", memo="x")
        JournalLine.objects.create(entry=self.entry, account=self.cash, debit=Decimal("100"))
        JournalLine.objects.create(entry=self.entry, account=self.sales, credit=Decimal("100"))
        self.entry.post()

    def test_the_second_is_refused(self):
        self.entry.create_reversal()
        with self.assertRaisesMessage(ValidationError, "already been reversed"):
            self.entry.create_reversal()

    def test_over_the_api_too(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("root"))
        url = f"/api/accounting/journal-entries/{self.entry.pk}/reverse/"
        self.assertEqual(client.post(url).status_code, 200)
        self.assertEqual(client.post(url).status_code, 400)
        cash = sum(l.debit - l.credit for l in JournalLine.objects.filter(account=self.cash))
        self.assertEqual(cash, Decimal("0"))

    def test_a_reversal_can_itself_be_reversed(self):
        reversal = self.entry.create_reversal()
        reversal.create_reversal()
