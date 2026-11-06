"""
What the accounts screens ask the server, asked as the people who use
them: an account's ledger with its running balance, page by page, and
who may read it; an account made and changed from its form, and what the
form is refused, beside the box it is about. Refusals first.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import Account, AccountType, JournalEntry, JournalLine
from .tests import AccountingTestCase

ACCOUNTS = "/api/accounting/accounts/"


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


class AccountFormTestCase(LedgerTestCase):
    """
    The account form, as the people who keep the chart and those who only
    read it. 1000 Cash holds money and has entries posted through it, 4000
    Sales Revenue has them posted to it, and 2400 Secured loans is a
    liability nothing has touched.
    """

    def setUp(self):
        super().setUp()
        self.loans = Account.objects.create(code="2400", name="Secured loans", account_type=AccountType.LIABILITY)

    def term_loan(self):
        return Account.objects.create(code="2401", name="Term loan", account_type=AccountType.LIABILITY,
                                      parent=self.loans)


class AccountFormRefusalTests(AccountFormTestCase):
    def test_a_parent_of_another_kind_is_refused_beside_the_parent(self):
        response = self.as_("Bookkeeper").post(ACCOUNTS, {
            "code": "2410", "name": "Cash credit", "account_type": "liability", "parent": self.cash.pk}, format="json")
        self.assertEqual((response.status_code, response.json()), (400, {
            "parent": ["A sub-account must have the same account_type as its parent."]}))
        self.assertFalse(Account.objects.filter(code="2410").exists())

    def test_its_own_parent_is_refused_beside_the_parent(self):
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.loans.pk}/", {"parent": self.loans.pk}, format="json")
        self.assertEqual((response.status_code, response.json()), (400, {
            "parent": ["An account cannot be its own parent."]}))
        self.loans.refresh_from_db()
        self.assertIsNone(self.loans.parent)

    def test_an_account_already_under_it_is_named_beside_the_parent(self):
        term = self.term_loan()
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.loans.pk}/", {"parent": term.pk}, format="json")
        self.assertEqual((response.status_code, response.json()), (400, {
            "parent": ["2401 - Term loan is already under 2400 - Secured loans; it cannot also be above it."]}))
        self.loans.refresh_from_db()
        self.assertIsNone(self.loans.parent)

    def test_a_kind_posted_to_is_kept_beside_the_kind(self):
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.revenue.pk}/", {"account_type": "expense"},
                                                format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(list(response.json()), ["account_type"])
        self.assertIn("4000 - Sales Revenue has posted entries, reported as income",
                      " ".join(response.json()["account_type"]))
        self.revenue.refresh_from_db()
        self.assertEqual(self.revenue.account_type, AccountType.INCOME)

    def test_a_kind_its_sub_accounts_are_not_is_refused_beside_the_kind(self):
        self.term_loan()
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.loans.pk}/", {"account_type": "equity"},
                                                format="json")
        self.assertEqual((response.status_code, response.json()), (400, {
            "account_type": ["Its sub-accounts are of the old type; change them first."]}))
        self.loans.refresh_from_db()
        self.assertEqual(self.loans.account_type, AccountType.LIABILITY)

    def test_money_posted_through_is_not_unmarked(self):
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.cash.pk}/", {"holds_money": False}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(list(response.json()), ["holds_money"])
        self.assertIn("1000 - Cash has posted entries as a bank, cash or card account",
                      " ".join(response.json()["holds_money"]))
        self.cash.refresh_from_db()
        self.assertTrue(self.cash.holds_money)

    def test_an_account_posted_to_is_not_deleted(self):
        response = self.as_("Bookkeeper").delete(f"{ACCOUNTS}{self.cash.pk}/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Still used by journal lines", " ".join(response.json()["non_field_errors"]))
        self.assertTrue(Account.objects.filter(pk=self.cash.pk).exists())

    def test_whoever_only_reads_the_chart_keeps_none_of_it(self):
        for role in ("GST Officer", "Sales Rep"):
            with self.subTest(role=role):
                reader = self.as_(role)
                self.assertEqual(reader.get(f"{ACCOUNTS}{self.loans.pk}/").status_code, 200)
                made = reader.post(ACCOUNTS, {"code": "2410", "name": "Cash credit", "account_type": "liability"},
                                   format="json")
                changed = reader.patch(f"{ACCOUNTS}{self.loans.pk}/", {"name": "Loans"}, format="json")
                deleted = reader.delete(f"{ACCOUNTS}{self.loans.pk}/")
                self.assertEqual((made.status_code, changed.status_code, deleted.status_code), (403, 403, 403))
        self.assertFalse(Account.objects.filter(code="2410").exists())
        self.assertEqual(Account.objects.get(pk=self.loans.pk).name, "Secured loans")


class AccountFormTests(AccountFormTestCase):
    def test_a_cash_credit_is_made_a_liability_money_moves_through(self):
        response = self.as_("Bookkeeper").post(ACCOUNTS, {
            "code": "1030", "name": "SBI cash credit", "account_type": "liability", "parent": None, "currency": None,
            "is_active": True, "holds_money": True}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = Account.objects.get(code="1030")
        self.assertEqual((made.name, made.account_type, made.parent, made.currency, made.is_active, made.holds_money),
                         ("SBI cash credit", AccountType.LIABILITY, None, None, True, True))

    def test_a_rename_changes_the_name_alone(self):
        response = self.as_("Bookkeeper").patch(f"{ACCOUNTS}{self.cash.pk}/", {"name": "Cash in hand"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.cash.refresh_from_db()
        self.assertEqual((self.cash.code, self.cash.name, self.cash.account_type, self.cash.holds_money),
                         ("1000", "Cash in hand", AccountType.ASSET, True))

    def test_a_sub_account_is_shown_under_its_parents_name(self):
        response = self.as_("Bookkeeper").post(ACCOUNTS, {
            "code": "2410", "name": "Cash credit", "account_type": "liability", "parent": self.loans.pk},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["parent_code"], response.json()["parent_name"]), ("2400", "Secured loans"))
        top = self.as_("GST Officer").get(f"{ACCOUNTS}{self.loans.pk}/").json()
        self.assertEqual((top["parent"], top["parent_code"], top["parent_name"]), (None, "", ""))

    def test_the_parent_is_offered_from_the_kind_chosen(self):
        def offered(kind):
            return [row["code"] for row in self.as_("Bookkeeper").get(
                ACCOUNTS, {"account_type": kind, "page_size": 8}).json()]

        self.assertEqual((offered("liability"), offered("asset")), (["2400"], ["1000"]))

    def test_an_account_nothing_uses_is_deleted(self):
        response = self.as_("Bookkeeper").delete(f"{ACCOUNTS}{self.loans.pk}/")
        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(Account.objects.filter(pk=self.loans.pk).exists())
