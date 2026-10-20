"""Second audit, lane money: probes that assert what SHOULD be true."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Company, Currency

from .models import (Account, AccountType, AccountingPeriod, BooksOpening, JournalEntry, JournalLine,
                     RecurringJournal)
from .recurring import RecurringJournalLine

D = Decimal


class Books(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        self.usd = Currency.objects.create(code="USD", name="Dollar")
        Company.objects.create(name="Deccan Polysacks", base_currency=self.inr)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.rent = Account.objects.create(code="5100", name="Rent", account_type=AccountType.EXPENSE)
        self.equity = Account.objects.create(code="3000", name="Opening", account_type=AccountType.EQUITY)

    def entry(self, day, amount="100", post=True):
        entry = JournalEntry.objects.create(date=day, memo="x")
        JournalLine.objects.create(entry=entry, account=self.rent, debit=D(amount))
        JournalLine.objects.create(entry=entry, account=self.bank, credit=D(amount))
        if post:
            entry.post()
        return entry


class AccountTests(Books):
    def test_an_account_with_posted_rupees_cannot_become_a_dollar_account(self):
        """M2: only holds_money and account_type are frozen once posted; currency is not."""
        self.entry(datetime.date(2026, 9, 1))
        self.bank.currency = self.usd
        with self.assertRaises(ValidationError):
            self.bank.save()


class OpeningTests(Books):
    def test_nothing_is_posted_before_the_day_the_books_opened(self):
        """M3: opening balances stand at the opening day; an entry dated before it moves them silently."""
        BooksOpening.objects.create(opened_on=datetime.date(2026, 4, 1), opening_account=self.equity)
        with self.assertRaises(ValidationError):
            self.entry(datetime.date(2026, 3, 15))


class RecurringTests(Books):
    def test_a_schedule_is_not_taken_for_a_day_that_has_not_come(self):
        """M4: generate(on_date=) has no correction_date rule: an auto-posting schedule posts a year ahead."""
        schedule = RecurringJournal.objects.create(code="RENT", memo="Rent", start_date=datetime.date(2026, 9, 1),
                                                   auto_post=True)
        RecurringJournalLine.objects.create(schedule=schedule, account=self.rent, debit=D("100"))
        RecurringJournalLine.objects.create(schedule=schedule, account=self.bank, credit=D("100"))
        ahead = timezone.localdate() + datetime.timedelta(days=400)
        with self.assertRaises(ValidationError):
            schedule.generate_one(on_date=ahead)


class ReportsAnswerBadInputTests(Books):
    URLS = [
        "/api/accounting/financial-statements/trial-balance/?as_of=garbage",
        "/api/accounting/financial-statements/trial-balance/?start=2026-09-30&as_of=2026-09-01",
        "/api/accounting/financial-statements/profit-and-loss/?start=2026-13-45",
        "/api/accounting/financial-statements/profit-and-loss/?start=2026-09-30&end=2026-09-01",
        "/api/accounting/financial-statements/balance-sheet/?as_of=notadate",
        "/api/accounting/accounts/1/ledger/?from=zzz",
        "/api/accounting/accounts/1/ledger/?to=2026-99-99",
        "/api/gst/gstr1/?period=2026-9", "/api/gst/gstr1/?period=0000-00", "/api/gst/gstr1/?period=99999-01",
        "/api/gst/gstr3b/?period=abc", "/api/gst/gstr1/json/?period=2026-00",
        "/api/gst/itc04/?start=2026-10-01&end=2026-04-01", "/api/gst/itc04/?start=x&end=y",
        "/api/gst/gstr2b/match/?period=2026-9",
    ]

    def test_no_report_answers_a_bad_date_with_a_server_error(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        from .gst import GstSettings

        client = APIClient(raise_request_exception=False)
        client.force_authenticate(User.objects.create_superuser("boss", password="x"))
        failed = {url: client.get(url).status_code for url in self.URLS}
        self.assertEqual({url: code for url, code in failed.items() if code >= 500}, {})


from .models import PaymentDirection  # noqa: E402
from .tests_fx_settlement import FxSettlementTestCase  # noqa: E402


class ForeignInvoiceCycleTests(FxSettlementTestCase):
    def test_random_part_payments_voids_and_reapplications_clear_the_receivable_to_nothing(self):
        """M13: EUR 1,000.07 booked at 1.20; paid in 3 to 5 parts at moving rates; every other invoice has one part
        bounced and paid again. Once nothing is due on any of them the receivable nets to exactly nothing."""
        import random

        from apps.sales.models import InvoicePayment

        rng = random.Random(7)
        day = datetime.date(2026, 2, 1)
        for round_number in range(12):
            invoice = self.euro_invoice("1000.07")
            parts = rng.randint(3, 5)
            cuts = sorted(rng.sample(range(1, 100007), parts - 1))
            sizes = [D(b - a) / D(100) for a, b in zip([0] + cuts, cuts + [100007])]
            for index, size in enumerate(sizes):
                day += datetime.timedelta(days=1)
                self.rate_moves_to(str(D(rng.randint(100000, 140000)) / D(100000)), on=day)
                receipt = self.euro_payment(PaymentDirection.RECEIPT, str(size), on=day)
                InvoicePayment.objects.create(invoice=invoice, payment=receipt, amount=size, date=day)
                if index == 1 and round_number % 2:
                    receipt.void(memo="bounced", on_date=day)
                    again = self.euro_payment(PaymentDirection.RECEIPT, str(size), on=day)
                    InvoicePayment.objects.create(invoice=invoice, payment=again, amount=size, date=day)
            invoice.refresh_from_db()
            self.assertEqual(invoice.amount_due(), D("0"), round_number)
        self.assertEqual(self.balance(self.ar), D("0"))


class InertSwitchTests(Books):
    def test_nothing_is_posted_to_an_account_switched_off(self):
        """M14: Account.is_active is read for money accounts only; a switched-off revenue or expense account still posts."""
        self.rent.is_active = False
        self.rent.save()
        with self.assertRaises(ValidationError):
            self.entry(datetime.date(2026, 9, 1))

    def test_a_switched_off_cost_centre_takes_no_new_line(self):
        """M15: CostCentre.is_active is a filter on the screen and nothing else."""
        from .analytic import CostCentre

        centre = CostCentre.objects.create(code="CC1", name="Old line", is_active=False)
        entry = JournalEntry.objects.create(date=datetime.date(2026, 9, 1), memo="x")
        with self.assertRaises(ValidationError):
            JournalLine.objects.create(entry=entry, account=self.rent, debit=D("5"), cost_centre=centre)
