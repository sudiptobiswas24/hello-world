"""Installment terms on the purchase side, and what they do to a payment run."""

import datetime
from decimal import Decimal

from apps.core.models import PaymentTerms, PaymentTermsLine

from .models import ap_aging, payment_run
from .tests_lifecycle import PurchasingLifecycleTestCase


class InstallmentBillTests(PurchasingLifecycleTestCase):
    def setUp(self):
        super().setUp()
        split = PaymentTerms.objects.create(code="5050", name="50/50", net_days=30)
        PaymentTermsLine.objects.create(terms=split, sequence=1, percent=Decimal("50"), days=0)
        PaymentTermsLine.objects.create(terms=split, sequence=2, percent=Decimal("50"), days=30)
        self.vendor.payment_terms = split
        self.vendor.save()

    def split_bill(self):
        bill = self.make_bill("10", "5")
        bill.bill_date = datetime.date(2026, 1, 1)
        bill.post()
        return bill

    def test_the_bill_carries_a_schedule(self):
        rows = self.split_bill().installments()
        self.assertEqual([row["amount"] for row in rows],
                         [Decimal("25.00"), Decimal("25.00")])

    def test_only_the_first_half_is_overdue_at_first(self):
        bill = self.split_bill()
        as_of = datetime.date(2026, 1, 15)
        self.assertEqual(bill.amount_overdue(as_of), Decimal("25.00"))
        self.assertEqual(bill.amount_due(), Decimal("50.00"))

    def test_the_payment_run_pays_what_is_due_not_the_whole_bill(self):
        """Paying it all early is the company's cash, given away for
        nothing."""
        self.split_bill()

        rows = payment_run(due_by=datetime.date(2026, 1, 15))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], Decimal("25.00"))

    def test_the_run_picks_the_rest_up_later(self):
        self.split_bill()
        rows = payment_run(due_by=datetime.date(2026, 2, 15))
        self.assertEqual(rows[0]["total"], Decimal("50.00"))

    def test_aging_splits_across_buckets(self):
        self.split_bill()
        aging = ap_aging(as_of=datetime.date(2026, 1, 15))
        self.assertEqual(aging["1-30"]["total"], Decimal("25.00"))
        self.assertEqual(aging["current"]["total"], Decimal("25.00"))
