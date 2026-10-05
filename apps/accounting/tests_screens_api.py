"""
What the accounts screens ask the server, asked as the people who use
them: an account's ledger with its running balance, page by page, and
who may read it. Refusals first.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import JournalEntry, JournalLine
from .tests import AccountingTestCase


class LedgerTestCase(AccountingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        # Cash: +100 in January, +250 in February, -40 in March, +10 in
        # April; and 999 in March that was never posted.
        for day, amount in [("2026-01-05", "100"), ("2026-02-10", "250"), ("2026-03-15", "-40"),
                            ("2026-04-20", "10")]:
            self.entry(day, Decimal(amount))
        self.entry("2026-03-01", Decimal("999"), post=False)

    def entry(self, day, amount, post=True):
        entry = JournalEntry.objects.create(date=day, memo=f"Cash {amount}")
        if amount > 0:
            JournalLine.objects.create(entry=entry, account=self.cash, debit=amount)
            JournalLine.objects.create(entry=entry, account=self.revenue, credit=amount)
        else:
            JournalLine.objects.create(entry=entry, account=self.revenue, debit=-amount)
            JournalLine.objects.create(entry=entry, account=self.cash, credit=-amount)
        if post:
            entry.post()
        return entry

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ledger(self, role="Bookkeeper", **params):
        return self.as_(role).get(f"/api/accounting/accounts/{self.cash.pk}/ledger/", params)


class LedgerRefusalTests(LedgerTestCase):
    def test_reading_the_chart_is_not_reading_the_books(self):
        self.assertEqual(self.as_("Sales Rep").get(f"/api/accounting/accounts/{self.cash.pk}/").status_code, 200)
        self.assertEqual(self.ledger(role="Sales Rep").status_code, 403)

    def test_a_page_or_date_that_is_not_one_is_refused_in_words(self):
        for params in [{"page": "two"}, {"page": "0"}, {"from": "01/02/2026"}]:
            with self.subTest(params=params):
                response = self.ledger(**params)
                self.assertEqual(response.status_code, 400, response.content)


class LedgerTests(LedgerTestCase):
    def test_a_period_opens_moves_and_closes(self):
        body = self.ledger(**{"from": "2026-02-01", "to": "2026-03-31"}).json()
        self.assertEqual([Decimal(body[key]) for key in ("opening", "debit", "credit", "closing")],
                         [Decimal("100"), Decimal("250"), Decimal("40"), Decimal("310")])
        self.assertEqual([(line["date"], Decimal(line["balance"])) for line in body["lines"]],
                         [("2026-03-15", Decimal("310")), ("2026-02-10", Decimal("350"))])

    def test_page_two_carries_the_balance_of_page_one(self):
        first = self.ledger(**{"from": "2026-02-01", "to": "2026-03-31", "page_size": "1"})
        second = self.ledger(**{"from": "2026-02-01", "to": "2026-03-31", "page_size": "1", "page": "2"})
        self.assertEqual(first["X-Total-Count"], "2")
        self.assertEqual(Decimal(first.json()["lines"][0]["balance"]), Decimal("310"))
        self.assertEqual(Decimal(second.json()["lines"][0]["balance"]), Decimal("350"))

    def test_the_whole_history_foots_to_the_trial_balance(self):
        body = self.ledger().json()
        self.assertEqual(Decimal(body["opening"]), Decimal("0"))
        self.assertEqual(Decimal(body["closing"]), Decimal("320"))  # 100 + 250 - 40 + 10
        trial = self.as_("Bookkeeper").get("/api/accounting/financial-statements/trial-balance/").json()
        [cash] = [row for row in trial["rows"] if row["account"] == "1000"]
        self.assertEqual((cash["account_id"], Decimal(cash["balance"])), (self.cash.pk, Decimal("320")))

    def test_include_zero_is_read_as_a_yes_or_no(self):
        url = "/api/accounting/financial-statements/trial-balance/"
        self.assertEqual(self.as_("Bookkeeper").get(url, {"include_zero": "maybe"}).status_code, 400)
        self.assertEqual(self.as_("Bookkeeper").get(url, {"include_zero": "1"}).status_code, 200)


class ReversedSinceItWasReadTests(LedgerTestCase):
    """A reversal decided under the lock asks the database, not what a
    list prefetched before it: review found a second reversal let through."""

    def test_an_entry_reversed_by_someone_else_meanwhile(self):
        from django.db.models import Prefetch

        entry = JournalEntry.objects.filter(posted=True).first()
        stale = JournalEntry.objects.prefetch_related(Prefetch("reversed_by")).get(pk=entry.pk)
        self.assertEqual(list(stale.reversed_by.all()), [])  # read before the other clerk
        JournalEntry.objects.get(pk=entry.pk).create_reversal(memo="First")
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "already been reversed"):
            stale.create_reversal(memo="Second")
        self.assertEqual(JournalEntry.objects.filter(reverses=entry).count(), 1)
