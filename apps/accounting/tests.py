from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Account, AccountType, JournalEntry, JournalLine


class AccountingTestCase(TestCase):
    def setUp(self):
        self.cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
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


# Regression tests from the second money audit (docs/handoff/reports/g23_audit2_money.md).
import datetime  # noqa: E402

from django.utils import timezone  # noqa: E402

from apps.core.models import Company, Currency  # noqa: E402

D = Decimal


class MoneyBooks(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="Dollar")
        Company.objects.create(name="Deccan Polysacks", base_currency=self.inr)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.rent = Account.objects.create(code="5100", name="Rent", account_type=AccountType.EXPENSE)
        self.equity = Account.objects.create(code="3000", name="Opening", account_type=AccountType.EQUITY)

    def entry(self, day, amount="100", post=True, **options):
        entry = JournalEntry.objects.create(date=day, memo="x")
        JournalLine.objects.create(entry=entry, account=self.rent, debit=D(amount))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=D(amount))
        if post:
            entry.post(**options)
        return entry


class NothingBeforeTheBooksOpenedTests(MoneyBooks):
    """O281: the opening balances stand at the opening day; only the old system's open documents keep older dates."""

    def setUp(self):
        super().setUp()
        from .models import BooksOpening

        BooksOpening.objects.create(opened_on=datetime.date(2026, 4, 1), opening_account=self.equity)

    def test_an_entry_dated_before_the_books_opened_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "The books opened on 2026-04-01"):
            self.entry(datetime.date(2026, 3, 15))
        self.assertFalse(JournalEntry.objects.filter(posted=True).exists())

    def test_an_entry_on_the_opening_day_and_after_posts(self):
        self.entry(datetime.date(2026, 4, 1))
        self.entry(datetime.date(2026, 4, 2))
        self.assertEqual(JournalEntry.objects.filter(posted=True).count(), 2)

    def test_an_open_document_from_the_old_system_keeps_its_date(self):
        self.assertTrue(self.entry(datetime.date(2026, 3, 15), opening_balance=True).posted)

    def test_a_reversal_is_not_dated_before_the_books_opened_either(self):
        entry = self.entry(datetime.date(2026, 3, 15), opening_balance=True)
        with self.assertRaises(ValidationError):
            entry.create_reversal(entry_date=datetime.date(2026, 3, 20))
        self.assertTrue(entry.create_reversal(entry_date=datetime.date(2026, 4, 5)).posted)


class CurrencyFrozenOncePostedTests(MoneyBooks):
    """O282: an account's currency is what its posted amounts were written in."""

    def test_an_account_with_posted_rupees_cannot_become_a_dollar_account(self):
        self.entry(datetime.date(2026, 9, 1))
        self.bank.currency = self.usd
        with self.assertRaisesMessage(ValidationError, "has posted entries in the currency"):
            self.bank.save()
        self.bank.refresh_from_db()
        self.assertIsNone(self.bank.currency_id)

    def test_naming_the_base_currency_on_a_posted_account_is_no_change(self):
        self.entry(datetime.date(2026, 9, 1))
        self.bank.currency = self.inr
        self.bank.save()
        self.bank.currency = None
        self.bank.save()

    def test_an_account_with_nothing_posted_may_change_currency(self):
        self.entry(datetime.date(2026, 9, 1), post=False)
        self.bank.currency = self.usd
        self.bank.save()

    def test_the_api_refuses_it_beside_the_field(self):
        from apps.core.tests_roles import RolesTestCase

        RolesTestCase.setUp(self)
        self.entry(datetime.date(2026, 9, 1))
        client = RolesTestCase.as_(self, "Bookkeeper")
        response = client.patch(f"/api/accounting/accounts/{self.bank.pk}/", {"currency": self.usd.pk}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("currency", response.json())


class SwitchedOffTakesNothingNewTests(MoneyBooks):
    """O283: an account or cost centre switched off takes no new posting; a reversal of what it holds does."""

    def test_nothing_is_posted_to_an_account_switched_off(self):
        self.rent.is_active = False
        self.rent.save()
        with self.assertRaisesMessage(ValidationError, "is switched off; it takes nothing new"):
            self.entry(datetime.date(2026, 9, 1))

    def test_a_reversal_still_takes_back_what_a_switched_off_account_holds(self):
        entry = self.entry(datetime.date(2026, 9, 1))
        self.rent.is_active = False
        self.rent.save()
        self.assertTrue(entry.create_reversal(entry_date=datetime.date(2026, 9, 2)).posted)

    def test_a_switched_off_cost_centre_takes_no_new_line(self):
        from .analytic import CostCentre

        centre = CostCentre.objects.create(code="CC1", name="Old line", is_active=False)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 9, 1), memo="x")
        with self.assertRaisesMessage(ValidationError, "is switched off; it takes no new line"):
            JournalLine.objects.create(entry=entry, account=self.rent, debit=D("5"), cost_centre=centre)

    def test_a_cost_centre_switched_off_after_the_line_was_keyed_refuses_the_post(self):
        from .analytic import CostCentre

        centre = CostCentre.objects.create(code="CC1", name="Old line")
        entry = JournalEntry.objects.create(date=datetime.date(2026, 9, 1), memo="x")
        JournalLine.objects.create(entry=entry, account=self.rent, debit=D("5"), cost_centre=centre)
        JournalLine.objects.create(entry=entry, account=self.bank, credit=D("5"))
        CostCentre.objects.filter(pk=centre.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            entry.post()
        centre.is_active = True
        centre.save()
        entry.post()
        CostCentre.objects.filter(pk=centre.pk).update(is_active=False)
        self.assertTrue(entry.create_reversal(entry_date=datetime.date(2026, 9, 2)).posted)
