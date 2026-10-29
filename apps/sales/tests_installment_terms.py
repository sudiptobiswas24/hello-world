"""
Installment terms, end to end.

The question that stops being simple: "is this invoice overdue" and "how
much is late" are no longer the same thing. On a 50/50 term the deposit
can be badly overdue while the balance is not due for another month.
"""

import datetime
from decimal import Decimal

from apps.core.models import PaymentTerms, PaymentTermsLine

from .models import DunningLevel, SettlementStatus, ar_aging, run_dunning
from .tests_base import SalesTestCase


class InstallmentInvoiceTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.split = PaymentTerms.objects.create(code="5050", name="50/50", net_days=30)
        PaymentTermsLine.objects.create(
            terms=self.split, sequence=1, percent=Decimal("50"), days=0
        )
        PaymentTermsLine.objects.create(
            terms=self.split, sequence=2, percent=Decimal("50"), days=30
        )
        self.customer.payment_terms = self.split
        self.customer.save()

    def split_invoice(self):
        return self.bill(self.make_order("10", "100"), on=datetime.date(2026, 3, 1))

    def test_the_invoice_carries_a_schedule(self):
        rows = self.split_invoice().installments()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["due_date"], datetime.date(2026, 3, 1))
        self.assertEqual(rows[1]["due_date"], datetime.date(2026, 3, 31))
        self.assertEqual(rows[0]["amount"], Decimal("500.00"))

    def test_only_the_deposit_is_overdue_at_first(self):
        invoice = self.split_invoice()

        as_of = datetime.date(2026, 3, 15)
        self.assertTrue(invoice.is_overdue(as_of))
        self.assertEqual(invoice.amount_overdue(as_of), Decimal("500.00"))
        self.assertEqual(invoice.amount_due(), Decimal("1000.00"))

    def test_paying_the_deposit_stops_it_being_overdue(self):
        invoice = self.split_invoice()
        self.allocate(self.receipt(Decimal("500"), on=datetime.date(2026, 3, 2)),
                      invoice, Decimal("500"))

        as_of = datetime.date(2026, 3, 15)
        self.assertFalse(invoice.is_overdue(as_of))
        self.assertEqual(invoice.amount_overdue(as_of), Decimal("0"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PARTIAL)

    def test_the_balance_falls_due_later(self):
        invoice = self.split_invoice()
        self.allocate(self.receipt(Decimal("500"), on=datetime.date(2026, 3, 2)),
                      invoice, Decimal("500"))

        as_of = datetime.date(2026, 4, 15)
        self.assertTrue(invoice.is_overdue(as_of))
        self.assertEqual(invoice.amount_overdue(as_of), Decimal("500.00"))

    def test_days_overdue_counts_from_the_earliest_unpaid_installment(self):
        invoice = self.split_invoice()
        self.assertEqual(invoice.days_overdue(datetime.date(2026, 4, 10)), 40)

    def test_a_plain_term_still_behaves_as_one_installment(self):
        self.customer.payment_terms = self.terms
        self.customer.save()
        invoice = self.bill(self.make_order("10", "100"), on=datetime.date(2026, 3, 1))

        self.assertEqual(len(invoice.installments()), 1)
        self.assertEqual(invoice.amount_overdue(datetime.date(2026, 5, 1)), Decimal("1000"))


class InstallmentAgingTests(InstallmentInvoiceTests):
    def test_one_invoice_can_sit_in_two_buckets(self):
        """Showing a single line for the whole invoice would put all of it
        in the wrong bucket either way."""
        self.split_invoice()

        aging = ar_aging(as_of=datetime.date(2026, 3, 15))

        self.assertEqual(aging["1-30"]["total"], Decimal("500.00"))
        self.assertEqual(aging["current"]["total"], Decimal("500.00"))

    def test_the_buckets_still_add_up_to_the_balance(self):
        from .models import outstanding_balance

        self.split_invoice()
        aging = ar_aging(as_of=datetime.date(2026, 3, 15))

        total = sum(bucket["total"] for bucket in aging.values())
        self.assertEqual(total, outstanding_balance(self.customer))

    def test_a_settled_installment_leaves_the_report(self):
        invoice = self.split_invoice()
        self.allocate(self.receipt(Decimal("500"), on=datetime.date(2026, 3, 2)),
                      invoice, Decimal("500"))

        aging = ar_aging(as_of=datetime.date(2026, 3, 15))

        self.assertEqual(aging["1-30"]["total"], Decimal("0"))
        self.assertEqual(aging["current"]["total"], Decimal("500.00"))


class InstallmentDunningTests(InstallmentInvoiceTests):
    def test_it_chases_only_what_is_late(self):
        """Chasing a customer for a balance that is not due for another
        month is how you lose the argument about the half that is."""
        DunningLevel.objects.create(name="First", days_overdue=7)
        invoice = self.split_invoice()

        run_dunning(as_of=datetime.date(2026, 3, 15), send=False)

        notice = invoice.dunning_notices.get()
        self.assertEqual(notice.amount_due, Decimal("500.00"))
        self.assertEqual(notice.days_overdue, 14)

    def test_a_paid_deposit_is_not_chased(self):
        DunningLevel.objects.create(name="First", days_overdue=7)
        invoice = self.split_invoice()
        self.allocate(self.receipt(Decimal("500"), on=datetime.date(2026, 3, 2)),
                      invoice, Decimal("500"))

        run_dunning(as_of=datetime.date(2026, 3, 15), send=False)

        self.assertEqual(invoice.dunning_notices.count(), 0)
