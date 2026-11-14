"""
review_acct: races on the shared TDS/TCS challan, on PostgreSQL. A failure is a finding.

  DATABASE_URL=postgres://erp:erp@localhost:5432/review_acct python manage.py test apps.e2e.tests_review_acct_races
"""

import datetime
import unittest
from decimal import Decimal as D

from django.db import connection
from django.test import tag

from apps.accounting.models import JournalEntry
from apps.sales.tcs import TcsChallan, TcsCollection
from apps.sales.tests_tcs import TcsTestCase

from .tests_races import RaceCase, fixture, race, race_in_order

SEP, OCT = datetime.date(2026, 9, 20), datetime.date(2026, 10, 5)


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class TcsChallanRaces(RaceCase):
    def setUp(self):
        self.books = fixture(self, TcsTestCase)

    def invariant(self, outcomes):
        """The payable account is exactly what the unpaid rows say it is."""
        books = self.books
        unpaid = sum((row.signed() for row in TcsCollection.objects.all() if not row.is_paid_over() and row.amount), D("0"))
        self.assertEqual(books.ledger("2290"), unpaid, f"{outcomes}: TCS payable differs from the rows still waiting")

    def test_two_challans_for_one_month(self):
        books = self.books
        books.t1()
        pay = lambda number: (lambda: TcsChallan.pay(books.section, "2026-09-01", OCT, books.bank, number, "0510308"))  # noqa: E731
        outcomes = race(JournalEntry, pay("00001"), pay("00002"))
        self.once(outcomes)
        self.assertEqual(TcsChallan.objects.count(), 1)
        self.assertEqual(books.ledger("2290"), D("0"), outcomes)

    def _challan_and_credit(self, challan_first):
        books = self.books
        invoice = books.t1()
        challan = lambda: TcsChallan.pay(books.section, "2026-09-01", OCT, books.bank, "00001", "0510308")  # noqa: E731
        credit = lambda: books.credit(type(invoice).objects.get(pk=invoice.pk), "500", SEP)  # noqa: E731
        # Each order run, the first holding the numbering row before the second starts (race_in_order).
        outcomes = race_in_order(JournalEntry, challan, credit) if challan_first else race_in_order(JournalEntry, credit, challan)
        self.assertEqual(outcomes, ["done", "done"], outcomes)
        self.invariant(outcomes)

    def test_a_challan_and_a_credit_note_giving_tcs_back_the_challan_first(self):
        self._challan_and_credit(challan_first=True)

    def test_a_challan_and_a_credit_note_giving_tcs_back_the_note_first(self):
        self._challan_and_credit(challan_first=False)

    def test_two_credit_notes_at_once_never_give_back_more_than_was_collected(self):
        books = self.books
        invoice = books.t1()
        outcomes = race(JournalEntry,
                        lambda: books.credit(type(invoice).objects.get(pk=invoice.pk), "1500", SEP),
                        lambda: books.credit(type(invoice).objects.get(pk=invoice.pk), "500", SEP))
        given = sum((row.amount for row in TcsCollection.objects.filter(gives_back__isnull=False)), D("0"))
        self.assertLessEqual(given, D("1062.00"), outcomes)
        self.assertEqual(books.ledger("2290"), D("1062.00") - given, outcomes)

    def test_a_challan_and_the_void_of_the_one_before_it(self):
        books = self.books
        books.t1()
        first = TcsChallan.pay(books.section, "2026-09-01", OCT, books.bank, "00001", "0510308")
        outcomes = race(JournalEntry,
                        lambda: TcsChallan.objects.get(pk=first.pk).void(),
                        lambda: TcsChallan.pay(books.section, "2026-09-01", OCT, books.bank, "00002", "0510308"))
        live = TcsChallan.objects.filter(voided_entry__isnull=True)
        paid_over = sum((c.amount for c in live), D("0"))
        # Whatever order, the government has been paid what is owed once: payable = owed less paid over by live challans.
        self.assertEqual(books.ledger("2290"), D("1062.00") - paid_over - sum(
            (c.amount for c in TcsChallan.objects.filter(voided_entry__isnull=False)), D("0")) + sum(
            (c.amount for c in TcsChallan.objects.filter(voided_entry__isnull=False)), D("0")), outcomes)
        self.assertLessEqual(paid_over, D("1062.00"), outcomes)

    def test_two_recordings_of_one_cancellation_reverse_the_invoice_once(self):
        from django.utils import timezone

        books = self.books
        invoice = books.t1()
        record = books.registered(invoice)
        record.cancellation("2", "Wrong quantity")
        from apps.gst.einvoice import EInvoice
        from apps.sales.models import Invoice

        outcomes = race(JournalEntry, *[
            lambda: EInvoice.objects.get(pk=record.pk).record_cancellation(timezone.now())] * 2)
        self.once(outcomes)
        self.assertEqual(Invoice.objects.filter(credits=invoice).count(), 1, outcomes)
        self.assertEqual((books.ledger("2290"), books.ledger("1100")), (D("0"), D("0")), outcomes)

    def _challan_and_cancellation(self, challan_first):
        from django.utils import timezone

        from apps.gst.einvoice import EInvoice

        books = self.books
        invoice = books.t1()
        record = books.registered(invoice)
        record.cancellation("2", "Wrong quantity")
        challan = lambda: TcsChallan.pay(books.section, "2026-09-01", OCT, books.bank, "00001", "0510308")  # noqa: E731
        cancel = lambda: EInvoice.objects.get(pk=record.pk).record_cancellation(timezone.now())  # noqa: E731
        if challan_first:
            challanned, cancelled = race_in_order(JournalEntry, challan, cancel)
        else:
            cancelled, challanned = race_in_order(JournalEntry, cancel, challan)
        self.invariant([challanned, cancelled])
        return invoice, challanned, cancelled

    def test_a_challan_and_the_cancellation_of_the_invoice_it_pays_over_the_cancellation_first(self):
        # The invoice cancelled first gives all its 1,062.00 of TCS back by its credit note; it was the month's only
        # collection, so what waits nets to nothing and the challan, after it, is refused: nothing is paid over.
        invoice, challanned, cancelled = self._challan_and_cancellation(challan_first=False)
        self.assertEqual(cancelled, "done")
        self.assertIn("nets to 0.00: more was given back than collected", challanned)
        self.assertEqual(TcsChallan.objects.count(), 0)

    def test_a_challan_and_the_cancellation_of_the_invoice_it_pays_over_the_challan_first(self):
        # The challan first pays the invoice's TCS over: the cancellation is then refused, the challan standing.
        invoice, challanned, cancelled = self._challan_and_cancellation(challan_first=True)
        self.assertEqual(challanned, "done")
        self.assertIn(f"TCS of 1062.00 on {invoice.number} was paid over by challan", cancelled)
        self.assertIn("void the challan first", cancelled)
        self.assertEqual(TcsChallan.objects.count(), 1)
