from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Account, AccountType, JournalEntry, JournalLine


class AccountingTestCase(TestCase):
    def setUp(self):
        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET)
        self.revenue = Account.objects.create(
            code="4000", name="Sales Revenue", account_type=AccountType.INCOME
        )

    def make_entry(self, debit=Decimal("100"), credit=Decimal("100")):
        entry = JournalEntry.objects.create(date="2026-01-01", memo="Test sale")
        JournalLine.objects.create(entry=entry, account=self.cash, debit=debit)
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=credit)
        return entry


class AccountHierarchyTests(AccountingTestCase):
    def test_subaccount_must_match_parent_type(self):
        petty_cash = Account(
            code="1001", name="Petty Cash", account_type=AccountType.LIABILITY, parent=self.cash
        )
        with self.assertRaises(ValidationError):
            petty_cash.full_clean()

    def test_subaccount_with_matching_type_is_valid(self):
        petty_cash = Account(
            code="1001", name="Petty Cash", account_type=AccountType.ASSET, parent=self.cash
        )
        petty_cash.full_clean()  # should not raise


class JournalLineValidationTests(AccountingTestCase):
    def test_line_cannot_have_both_debit_and_credit(self):
        entry = JournalEntry.objects.create(date="2026-01-01")
        line = JournalLine(entry=entry, account=self.cash, debit=Decimal("10"), credit=Decimal("10"))
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_line_must_have_debit_or_credit(self):
        entry = JournalEntry.objects.create(date="2026-01-01")
        line = JournalLine(entry=entry, account=self.cash)
        with self.assertRaises(ValidationError):
            line.full_clean()


class PostingTests(AccountingTestCase):
    def test_balanced_entry_posts_successfully(self):
        entry = self.make_entry(Decimal("100"), Decimal("100"))
        entry.post()
        entry.refresh_from_db()
        self.assertTrue(entry.posted)
        self.assertIsNotNone(entry.posted_at)

    def test_unbalanced_entry_cannot_post(self):
        entry = self.make_entry(Decimal("100"), Decimal("90"))
        with self.assertRaises(ValidationError):
            entry.post()
        entry.refresh_from_db()
        self.assertFalse(entry.posted)

    def test_empty_entry_cannot_post(self):
        entry = JournalEntry.objects.create(date="2026-01-01")
        with self.assertRaises(ValidationError):
            entry.post()

    def test_already_posted_entry_cannot_post_again(self):
        entry = self.make_entry()
        entry.post()
        with self.assertRaises(ValidationError):
            entry.post()


class ImmutabilityTests(AccountingTestCase):
    def test_posted_entry_cannot_be_edited(self):
        entry = self.make_entry()
        entry.post()
        entry.memo = "changed after posting"
        with self.assertRaises(ValidationError):
            entry.save()

    def test_posted_entry_cannot_be_deleted(self):
        entry = self.make_entry()
        entry.post()
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_line_on_posted_entry_cannot_be_edited(self):
        entry = self.make_entry()
        entry.post()
        line = entry.lines.first()
        line.debit = Decimal("999")
        with self.assertRaises(ValidationError):
            line.save()

    def test_line_on_posted_entry_cannot_be_deleted(self):
        entry = self.make_entry()
        entry.post()
        line = entry.lines.first()
        with self.assertRaises(ValidationError):
            line.delete()

    def test_draft_entry_can_be_freely_edited(self):
        entry = self.make_entry()
        entry.memo = "still a draft"
        entry.save()  # should not raise
        entry.delete()  # should not raise


class ReversalTests(AccountingTestCase):
    def test_reversal_swaps_debits_and_credits_and_is_balanced(self):
        entry = self.make_entry(Decimal("100"), Decimal("100"))
        entry.post()

        reversal = entry.create_reversal()

        self.assertTrue(reversal.posted)
        self.assertEqual(reversal.reverses, entry)
        cash_line = reversal.lines.get(account=self.cash)
        revenue_line = reversal.lines.get(account=self.revenue)
        self.assertEqual(cash_line.credit, Decimal("100"))
        self.assertEqual(cash_line.debit, Decimal("0"))
        self.assertEqual(revenue_line.debit, Decimal("100"))
        self.assertEqual(revenue_line.credit, Decimal("0"))

    def test_net_effect_of_entry_and_reversal_is_zero(self):
        entry = self.make_entry(Decimal("100"), Decimal("100"))
        entry.post()
        entry.create_reversal()

        debit_total = sum(line.debit for line in self.cash.lines.all())
        credit_total = sum(line.credit for line in self.cash.lines.all())
        self.assertEqual(debit_total, credit_total)

    def test_cannot_reverse_an_unposted_entry(self):
        entry = self.make_entry()
        with self.assertRaises(ValidationError):
            entry.create_reversal()
