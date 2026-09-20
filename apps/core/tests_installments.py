"""
Installment payment terms.

"50% on order, 50% on delivery" and "30/60/90" are ordinary B2B terms
that could not be expressed at all when a term was a single net_days.
The interesting consequences are downstream: on a 50/50 term the deposit
can be badly overdue while the balance is not due for another month, so
"is this invoice overdue" and "how much is late" stop being the same
question.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.settlement import amount_overdue, installment_schedule

from .models import PaymentTerms, PaymentTermsLine


class ScheduleTests(TestCase):
    def terms_with(self, *lines, **kwargs):
        terms = PaymentTerms.objects.create(
            code=kwargs.pop("code", "T"), name="Terms", net_days=kwargs.pop("net_days", 30)
        )
        for index, (percent, days) in enumerate(lines):
            PaymentTermsLine.objects.create(
                terms=terms, sequence=index, percent=Decimal(percent), days=days
            )
        return terms

    def test_a_term_with_no_lines_is_a_single_installment(self):
        """Every simple term has to keep working unchanged."""
        terms = PaymentTerms.objects.create(code="N30", name="Net 30", net_days=30)
        schedule = terms.schedule(datetime.date(2026, 1, 1), Decimal("1000"))

        self.assertEqual(schedule, [(datetime.date(2026, 1, 31), Decimal("1000"))])

    def test_fifty_fifty(self):
        terms = self.terms_with(("50", 0), ("50", 30))
        schedule = terms.schedule(datetime.date(2026, 1, 1), Decimal("1000"))

        self.assertEqual(schedule, [
            (datetime.date(2026, 1, 1), Decimal("500.00")),
            (datetime.date(2026, 1, 31), Decimal("500.00")),
        ])

    def test_thirty_sixty_ninety(self):
        terms = self.terms_with(("33.333", 30), ("33.333", 60), ("33.334", 90))
        schedule = terms.schedule(datetime.date(2026, 1, 1), Decimal("1000"))

        self.assertEqual([amount for _, amount in schedule],
                         [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")])

    def test_the_rounding_remainder_goes_to_the_last_installment(self):
        """Three thirds of a penny-odd total must still come to the total."""
        terms = self.terms_with(("33.333", 30), ("33.333", 60), ("33.334", 90))
        schedule = terms.schedule(datetime.date(2026, 1, 1), Decimal("100.01"))
        self.assertEqual(sum(amount for _, amount in schedule), Decimal("100.01"))

    def test_the_due_date_is_the_last_installment(self):
        terms = self.terms_with(("50", 0), ("50", 60))
        self.assertEqual(terms.due_date(datetime.date(2026, 1, 1)), datetime.date(2026, 3, 2))

    def test_end_of_month_snapping(self):
        """'Net 30 EOM' is how the term is actually written."""
        terms = PaymentTerms.objects.create(code="EOM", name="Net 30 EOM", net_days=30)
        PaymentTermsLine.objects.create(
            terms=terms, percent=Decimal("100"), days=30, day_of_month=31
        )
        self.assertEqual(
            terms.schedule(datetime.date(2026, 1, 15), Decimal("100"))[0][0],
            datetime.date(2026, 2, 28),
        )

    def test_percentages_must_come_to_a_hundred(self):
        """Checked when the term is used, not as each line is saved — a
        50/50 is built one line at a time and the first would always fail."""
        terms = PaymentTerms.objects.create(code="BAD", name="Bad", net_days=30)
        PaymentTermsLine.objects.create(terms=terms, percent=Decimal("50"), days=0)
        PaymentTermsLine.objects.create(terms=terms, percent=Decimal("40"), days=30)

        with self.assertRaisesMessage(ValidationError, "come to 90"):
            terms.schedule(datetime.date(2026, 1, 1), Decimal("1000"))

    def test_a_half_built_term_can_be_saved(self):
        terms = PaymentTerms.objects.create(code="WIP", name="In progress", net_days=30)
        PaymentTermsLine.objects.create(terms=terms, percent=Decimal("50"), days=0)
        self.assertEqual(terms.lines.count(), 1)


class ApplicationTests(TestCase):
    def terms(self):
        terms = PaymentTerms.objects.create(code="5050", name="50/50", net_days=30)
        PaymentTermsLine.objects.create(terms=terms, sequence=1, percent=Decimal("50"), days=0)
        PaymentTermsLine.objects.create(terms=terms, sequence=2, percent=Decimal("50"), days=30)
        return terms

    def schedule(self, settled):
        return installment_schedule(
            terms=self.terms(), document_date=datetime.date(2026, 1, 1),
            total=Decimal("1000"), settled=Decimal(settled),
        )

    def test_money_lands_on_the_earliest_installment_first(self):
        """Someone paying half of a 50/50 order has paid the deposit, not
        the balance."""
        rows = self.schedule("500")

        self.assertEqual(rows[0]["outstanding"], Decimal("0"))
        self.assertEqual(rows[1]["outstanding"], Decimal("500.00"))

    def test_a_part_payment_spills_into_the_next(self):
        rows = self.schedule("700")
        self.assertEqual(rows[0]["outstanding"], Decimal("0"))
        self.assertEqual(rows[1]["outstanding"], Decimal("300.00"))

    def test_nothing_paid_leaves_both_outstanding(self):
        rows = self.schedule("0")
        self.assertEqual([row["outstanding"] for row in rows],
                         [Decimal("500.00"), Decimal("500.00")])

    def test_only_what_is_past_due_counts_as_overdue(self):
        rows = self.schedule("0")

        # The deposit was due on day one; the balance is not due yet.
        self.assertEqual(amount_overdue(rows, datetime.date(2026, 1, 15)), Decimal("500.00"))
        self.assertEqual(amount_overdue(rows, datetime.date(2026, 3, 1)), Decimal("1000.00"))

    def test_a_paid_deposit_leaves_nothing_overdue(self):
        rows = self.schedule("500")
        self.assertEqual(amount_overdue(rows, datetime.date(2026, 1, 15)), Decimal("0"))
