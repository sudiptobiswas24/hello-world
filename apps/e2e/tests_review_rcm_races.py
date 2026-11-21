"""Review probes (review_rcm): reverse-charge races on PostgreSQL."""

import unittest
from decimal import Decimal as D

from django.db import connection
from django.test import tag

from .tests_races import RaceCase, fixture, race, race_in_order


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class ReviewReverseChargeRaces(RaceCase):
    def books(self):
        from apps.gst import tests_rcm_cash as rcm_fixture

        return fixture(self, rcm_fixture.ReverseChargeCashTestCase)

    def test_one_payment_vouchered_twice_at_once_makes_one_set(self):
        from apps.gst.reverse_charge import PaymentVoucher

        books = self.books()
        payment = books.paid_supplier(books.freight_bill(vendor=books.gta), "4000")
        outcomes = race(PaymentVoucher, lambda: PaymentVoucher.issue_for(payment), lambda: PaymentVoucher.issue_for(payment))
        self.once(outcomes)
        self.assertEqual(PaymentVoucher.objects.count(), 1)
        self.assertNotIn("duplicate key", " ".join(str(o) for o in outcomes))

    def test_a_self_invoice_racing_the_whole_debit_note_never_stands_on_a_bill_taken_back(self):
        from apps.gst.reverse_charge import SelfInvoice
        from apps.gst.tests import DAY

        for note_first in (True, False):
            books = self.books() if note_first else self.books()
            bill = books.freight_bill()
            note = lambda: bill.create_debit_note(on_date=DAY)  # noqa: E731
            issue = lambda: SelfInvoice.issue(bill, on_date=DAY)  # noqa: E731
            outcomes = race_in_order(SelfInvoice, note, issue) if note_first else race_in_order(SelfInvoice, issue, note)
            standing = SelfInvoice.objects.filter(bill=bill, cancelled_on__isnull=True).count()
            self.assertEqual(standing, 0, (note_first, outcomes))
            break  # one order only: the note holding the numbering row first

    def test_a_challan_and_its_void_at_once_end_with_the_tax_owed_or_paid_never_both(self):
        from apps.gst.reverse_charge import GstCashPayment

        books = self.books()
        books.freight_bill()
        first = books.pay()
        outcomes = race(GstCashPayment, lambda: GstCashPayment.objects.get(pk=first.pk).void(),
                        lambda: GstCashPayment.objects.get(pk=first.pk).void())
        self.once(outcomes)
        self.assertEqual((books.ledger("2291"), books.ledger("1010")), (D("500.00"), D("0.00")))
