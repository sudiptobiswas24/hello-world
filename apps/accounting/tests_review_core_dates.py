"""
Review probes for fix_core's O15 (g22 review_core): Payment.void is dated by correction_date().
Before the payment, in a closed period, in the future, on the same day.
"""
import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import AccountingPeriod, JournalEntry
from .tests_reconciliation import ReconciliationTestCase


class PaymentVoidDatingTests(ReconciliationTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.paid = self.receipt("1000", on=self.today - datetime.timedelta(days=10))

    def entries(self):
        return JournalEntry.objects.count()

    def test_before_the_payment_is_refused_and_nothing_is_written(self):
        before = self.entries()
        with self.assertRaises(ValidationError):
            self.paid.void(on_date=self.paid.payment_date - datetime.timedelta(days=1))
        self.paid.refresh_from_db()
        self.assertEqual((self.paid.is_voided(), self.entries()), (False, before))

    def test_a_day_still_to_come_is_refused_and_nothing_is_written(self):
        before = self.entries()
        with self.assertRaises(ValidationError):
            self.paid.void(on_date=self.today + datetime.timedelta(days=1))
        self.paid.refresh_from_db()
        self.assertEqual((self.paid.is_voided(), self.entries()), (False, before))

    def test_the_day_of_the_payment_and_today_are_allowed_and_the_reversal_carries_the_day(self):
        self.paid.void(on_date=self.paid.payment_date)
        self.assertEqual(self.paid.voided_entry.date, self.paid.payment_date)

    def test_the_default_is_today(self):
        self.paid.void()
        self.assertEqual(self.paid.voided_entry.date, self.today)

    def test_a_closed_period_refuses_it_and_the_payment_stands(self):
        AccountingPeriod.objects.create(name="Closed", start_date=self.today - datetime.timedelta(days=5),
                                        end_date=self.today + datetime.timedelta(days=5), closed=True)
        before = self.entries()
        with self.assertRaises(ValidationError):
            self.paid.void(on_date=self.today)
        self.paid.refresh_from_db()
        self.assertEqual((self.paid.is_voided(), self.entries()), (False, before))
