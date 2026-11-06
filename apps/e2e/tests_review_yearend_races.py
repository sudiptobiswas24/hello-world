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

    def _r3(self, restate_first):
        # Fixed by the author (round 2): one order per test, since a second fixture in one test collides on
        # its codes; and the claim asked for any standing restatement once the receipt was voided. Voided first,
        # the restatement rightly leaves the receipt out and restates the invoice it never paid. The defect
        # is a restatement that reads the voided receipt as money on account.
        fx, books, invoice, receipt = self._setup()
        voider = lambda: Payment.objects.get(pk=receipt.pk).void(on_date=fx.YEAR_END)  # noqa: E731
        make = lambda: Restatement.make(fx.YEAR_END)  # noqa: E731
        out = race_in_order(Restatement, make, voider) if restate_first else race_in_order(Restatement, voider, make)[::-1]
        voided = Payment.objects.get(pk=receipt.pk).is_voided()
        restated = Restatement.objects.filter(voided_on__isnull=True, lines__item_type="accounting.payment",
                                              lines__item_id=receipt.pk).exists()
        if restated and voided:
            self.fail(f"{out}: restated the receipt as at 31 Mar and voided it on 31 Mar")
        return out, voided, restated

    def test_R3_restatement_first_then_a_void_of_the_payment(self):
        out, voided, restated = self._r3(restate_first=True)
        self.assertEqual((out[0], restated, voided), ("done", True, False), out)

    def test_R3_void_of_the_payment_first_then_the_restatement(self):
        out, voided, restated = self._r3(restate_first=False)
        self.assertEqual((out, voided, restated), (["done", "done"], True, False), out)
