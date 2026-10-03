"""
Bad-debt write-off.

The distinction being tested is that a write-off is not a credit note.
A credit note says the sale did not happen and reverses revenue; a
write-off says it did happen and the money never came. Both clear the
receivable; only one of them is true.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import Payment, PaymentDirection

from .models import (
    DunningLevel,
    InvoiceWriteOff,
    SettlementStatus,
    ar_aging,
    bad_debt_report,
    outstanding_balance,
    run_dunning,
)
from .tests_base import SalesTestCase


class WriteOffTests(SalesTestCase):
    def test_it_clears_the_receivable_to_bad_debt_expense(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.assertEqual(invoice.amount_due(), Decimal("1000"))

        invoice.write_off(on_date=datetime.date(2026, 9, 1), reason="Liquidated")

        self.assertEqual(invoice.amount_due(), Decimal("0"))
        self.assertEqual(self.balance(self.bad_debt), Decimal("1000"))
        self.assertEqual(self.balance(self.ar), Decimal("0"))

    def test_revenue_is_left_alone(self):
        """The sale happened. A credit note would deny that; this must not."""
        invoice = self.bill(self.make_order("10", "100"))
        before = self.balance(self.revenue)
        invoice.write_off()
        self.assertEqual(self.balance(self.revenue), before)

    def test_it_defaults_to_whatever_is_still_owed(self):
        invoice = self.bill(self.make_order("10", "100"))
        payment = self.receipt(Decimal("400"), on=datetime.date(2026, 4, 1))
        self.allocate(payment, invoice, Decimal("400"))

        invoice.write_off()

        self.assertEqual(invoice.write_offs.get().amount, Decimal("600"))
        self.assertEqual(invoice.amount_due(), Decimal("0"))

    def test_a_partial_write_off_leaves_the_rest_collectable(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(Decimal("600"), reason="Settled at 40c")
        self.assertEqual(invoice.amount_due(), Decimal("400"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.PARTIAL)

    def test_write_offs_accumulate_without_exceeding_the_balance(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(Decimal("600"))
        invoice.write_off(Decimal("400"))
        self.assertEqual(invoice.amount_written_off(), Decimal("1000"))
        with self.assertRaisesMessage(ValidationError, "nothing left to write off"):
            invoice.write_off(Decimal("1"))

    def test_it_cannot_exceed_what_is_outstanding(self):
        invoice = self.bill(self.make_order("10", "100"))
        with self.assertRaisesMessage(ValidationError, "Cannot write off"):
            invoice.write_off(Decimal("1500"))

    def test_a_draft_invoice_cannot_be_written_off(self):
        order = self.make_order("10", "100")
        invoice = order.create_invoice(self.ar, invoice_date=datetime.date(2026, 3, 1))
        with self.assertRaisesMessage(ValidationError, "Only a posted invoice"):
            invoice.write_off()

    def test_a_credit_note_cannot_be_written_off(self):
        invoice = self.bill(self.make_order("10", "100"))
        note = invoice.create_credit_note()
        with self.assertRaisesMessage(ValidationError, "credit note cannot be written off"):
            note.write_off()

    def test_it_needs_an_account_to_charge(self):
        from apps.core.models import Company

        company = Company.get()
        company.bad_debt_account = None
        company.save()
        invoice = self.bill(self.make_order("10", "100"))
        with self.assertRaisesMessage(ValidationError, "no bad debt account"):
            invoice.write_off()

    def test_the_status_says_written_off_not_paid(self):
        """A zero balance reached by giving up reads differently to one
        reached by being paid, and collections needs to tell them apart."""
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off()
        self.assertEqual(invoice.settlement_status(), SettlementStatus.WRITTEN_OFF)


class WriteOffVisibilityTests(SalesTestCase):
    def test_it_leaves_the_aging_report(self):
        invoice = self.bill(self.make_order("10", "100"))
        as_of = datetime.date(2026, 9, 1)
        self.assertEqual(ar_aging(as_of)["90+"]["total"], Decimal("1000"))

        invoice.write_off(on_date=as_of)

        self.assertEqual(ar_aging(as_of)["90+"]["total"], Decimal("0"))

    def test_it_stops_counting_against_the_credit_limit(self):
        invoice = self.bill(self.make_order("10", "100"))
        self.assertEqual(outstanding_balance(self.customer), Decimal("1000"))
        invoice.write_off()
        self.assertEqual(outstanding_balance(self.customer), Decimal("0"))

    def test_dunning_stops_chasing_it(self):
        DunningLevel.objects.create(name="First", days_overdue=7)
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off()

        run_dunning(as_of=datetime.date(2026, 9, 1), send=False)

        self.assertEqual(invoice.dunning_notices.count(), 0)


class WriteOffRecoveryTests(SalesTestCase):
    def test_a_recovery_reverses_the_original_entry(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(on_date=datetime.date(2026, 9, 1))
        write_off = invoice.write_offs.get()

        entry = invoice.recover_write_off(write_off, on_date=datetime.date(2026, 10, 1))

        self.assertEqual(entry.reverses_id, write_off.journal_entry_id)
        self.assertEqual(self.balance(self.bad_debt), Decimal("0"))
        self.assertEqual(self.balance(self.ar), Decimal("1000"))

    def test_the_debt_becomes_collectable_again(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off()
        invoice.recover_write_off(invoice.write_offs.get())

        self.assertEqual(invoice.amount_due(), Decimal("1000"))
        self.assertEqual(invoice.settlement_status(), SettlementStatus.UNPAID)

    def test_the_write_off_stays_on_record(self):
        """Netted to nothing, not deleted — the decision was made and the
        history of it is what an auditor asks about."""
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(reason="Gone quiet")
        invoice.recover_write_off(invoice.write_offs.get())

        write_off = invoice.write_offs.get()
        self.assertTrue(write_off.is_recovered())
        self.assertEqual(write_off.reason, "Gone quiet")

    def test_it_cannot_be_recovered_twice(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off()
        write_off = invoice.write_offs.get()
        invoice.recover_write_off(write_off)
        with self.assertRaisesMessage(ValidationError, "already been recovered"):
            invoice.recover_write_off(write_off)

    def test_it_must_belong_to_the_invoice(self):
        first = self.bill(self.make_order("10", "100"))
        second = self.bill(self.make_order("5", "100"))
        first.write_off()
        with self.assertRaisesMessage(ValidationError, "different invoice"):
            second.recover_write_off(first.write_offs.get())


class BadDebtReportTests(SalesTestCase):
    def test_it_groups_by_customer_and_nets_recoveries(self):
        first = self.bill(self.make_order("10", "100"))
        second = self.bill(self.make_order("5", "100"))
        first.write_off(on_date=datetime.date(2026, 9, 1))
        second.write_off(on_date=datetime.date(2026, 9, 2))
        second.recover_write_off(second.write_offs.get())

        rows = bad_debt_report()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer"], self.customer)
        self.assertEqual(rows[0]["written_off"], Decimal("1500"))
        self.assertEqual(rows[0]["recovered"], Decimal("500"))
        self.assertEqual(rows[0]["net"], Decimal("1000"))

    def test_it_respects_the_period(self):
        invoice = self.bill(self.make_order("10", "100"))
        invoice.write_off(on_date=datetime.date(2026, 9, 1))

        self.assertEqual(bad_debt_report(end=datetime.date(2026, 8, 31)), [])
        self.assertEqual(len(bad_debt_report(start=datetime.date(2026, 9, 1))), 1)
