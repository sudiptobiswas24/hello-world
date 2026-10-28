"""review_yearend races (PostgreSQL): a restatement against a payment and an allocation, both orders; filing against a disposal."""
import datetime
import unittest
from decimal import Decimal

from django.db import connection
from django.test import tag

from apps.accounting.models import Payment
from apps.accounting.restatement import Restatement
from apps.e2e.tests_races import RaceCase, fixture, race, race_in_order
from apps.sales.models import Invoice, InvoicePayment

D = datetime.date


@tag("race")
@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class Probes(RaceCase):
    def _setup(self):
        from apps.accounting import tests_restatement as fx

        books = fixture(self, fx.RestatementTestCase)
        invoice = books.invoice()
        receipt = books.receipt("1000", D(2026, 3, 20))
        return fx, books, invoice, receipt

    def _apply(self, invoice, receipt):
        return lambda: InvoicePayment.objects.create(
            invoice=Invoice.objects.get(pk=invoice.pk), payment=Payment.objects.get(pk=receipt.pk),
            amount=Decimal("1000"), date=D(2026, 3, 31))

    def _check(self, invoice, outcomes):
        restated = Restatement.objects.filter(lines__item_id=invoice.pk, lines__item_type="sales.invoice",
                                              voided_on__isnull=True).exists()
        paid = InvoicePayment.objects.filter(invoice=invoice, date__lte=D(2026, 3, 31)).exists()
        self.assertFalse(restated and paid, f"{outcomes}: restated open and paid on the same day")
        self.assertEqual(sorted(o == "done" for o in outcomes)[1], True, outcomes)

    def test_R1_restatement_first_then_allocation(self):
        fx, books, invoice, receipt = self._setup()
        out = race_in_order(Restatement, lambda: Restatement.make(fx.YEAR_END), self._apply(invoice, receipt))
        self._check(invoice, out)
        self.assertTrue(out[0] == "done" and out[1] != "done", out)

    def test_R2_allocation_first_then_restatement(self):
        fx, books, invoice, receipt = self._setup()
        out = race_in_order(Restatement, self._apply(invoice, receipt), lambda: Restatement.make(fx.YEAR_END))
        self._check(invoice, out)
        self.assertTrue(out[0] == "done", out)

    def test_R3_restatement_against_a_void_of_the_payment_both_orders(self):
        for restate_first in (True, False):
            fx, books, invoice, receipt = self._setup()
            voider = lambda: Payment.objects.get(pk=receipt.pk).void(on_date=fx.YEAR_END)  # noqa: E731
            make = lambda: Restatement.make(fx.YEAR_END)  # noqa: E731
            out = race_in_order(Restatement, make, voider) if restate_first else race_in_order(Restatement, voider, make)[::-1]
            payment = Payment.objects.get(pk=receipt.pk)
            voided = payment.is_voided()
            made = Restatement.objects.filter(voided_on__isnull=True).exists()
            # a payment voided on 31 Mar while a standing restatement read it as unapplied money on account
            # (or the invoice as paid) is a restatement of a figure that no longer holds
            if made and voided:
                line = Restatement.objects.get().lines.first()
                self.fail(f"{out}: restated {line.label} as at 31 Mar and voided the receipt on 31 Mar")
