"""
g22 review_purch: races on PostgreSQL (SQLite serialises writers and proves nothing). Probes of fix_purch.
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.db import connection, transaction
from django.test import tag

from apps.accounting.models import JournalEntry, TdsRate
from apps.e2e import tests_races as races
from apps.purchasing.models import Bill, BlanketOrder, BlanketOrderLine, PurchaseOrder, PurchaseOrderLine
from apps.purchasing import tests_lifecycle
from apps.purchasing.tests_returns_and_landed import ProbeCase
from apps.purchasing.tests_tds import TdsTestCase

D = datetime.date
Z = Decimal


@tag("race")
class ReviewPurchRaces(races.RaceCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL only")

    def test_01_two_bills_with_one_invoice_number_at_once(self):
        made = races.fixture(self, ProbeCase)

        def entering(reference):
            def go():
                Bill.objects.create(vendor=made.vendor, bill_date=D(2026, 6, 10), payable_account=made.payable,
                                    reference=reference)
            return go

        outcomes = races.race(Bill, entering("INV-77"), entering("inv 77"))
        self.once(outcomes)
        self.assertEqual(Bill.objects.filter(vendor=made.vendor).count(), 1, outcomes)
        self.assertTrue(any("already" in o or "unique" in o or "duplicate" in o for o in outcomes), outcomes)

    def test_02_a_rate_edited_as_a_bill_deducts_the_deduction_keeps_the_rate_its_row_holds(self):
        made = races.fixture(self, TdsTestCase)
        party = made.contractor(pan="AAAPL1234C")
        bill = made.bill("50000", day=D(2026, 6, 30), vendor=party)
        rate_pk = made.contract_rate.pk

        def deduct():
            Bill.objects.get(pk=bill.pk).deduct_tds()

        def edit():
            row = TdsRate.objects.get(pk=rate_pk)
            row.rate_percent = Z("5")
            row.save()

        outcomes = races.race((JournalEntry, TdsRate), deduct, edit)
        row = TdsRate.objects.get(pk=rate_pk)
        deductions = list(bill.tds_deductions.all())
        if deductions:
            kept = deductions[0]
            self.assertEqual((kept.rate_percent, kept.rate_id), (row.rate_percent, row.pk),
                             f"the deduction says {kept.rate_percent}% on a row that now says {row.rate_percent}% "
                             f"(confirmed {row.confirmed_on}); outcomes {outcomes}")

    def test_03_a_rate_confirmed_as_a_bill_deducts_nothing_is_deducted_at_an_unconfirmed_row(self):
        made = races.fixture(self, TdsTestCase)
        party = made.contractor(pan="AAAPL1234C")
        bill = made.bill("50000", day=D(2026, 9, 30), vendor=party)
        pending = TdsRate.objects.create(section=made.contract, applies_from=D(2026, 9, 1), rate_percent=Z("3"),
                                         no_pan_rate_percent=Z("25"))
        from django.contrib.auth.models import User
        user = User.objects.create_user("accountant")

        def deduct():
            Bill.objects.get(pk=bill.pk).deduct_tds()

        def confirm():
            TdsRate.objects.get(pk=pending.pk).confirm(user)

        outcomes = races.race((JournalEntry, TdsRate), deduct, confirm)
        for made_row in bill.tds_deductions.select_related("rate"):
            self.assertIsNotNone(made_row.rate.confirmed_on, outcomes)

    def test_04_a_credit_note_number_set_twice_at_once_is_set_once(self):
        made = races.fixture(self, ProbeCase)
        bill = made.hand_bill(None, "10", "100", link=False)
        bill.post()
        note = Bill.objects.get(pk=bill.pk).create_debit_note(quantities={bill.lines.get(): Z("4")})
        outcomes = races.race(
            Bill,
            lambda: Bill.objects.get(pk=note.pk).record_supplier_note("CN-1", D(2026, 1, 20)),
            lambda: Bill.objects.get(pk=note.pk).record_supplier_note("CN-2", D(2026, 1, 20)))
        self.once(outcomes)
        winner = "CN-1" if outcomes[0] == "done" else "CN-2"
        self.assertEqual(Bill.objects.get(pk=note.pk).supplier_note_number, winner, outcomes)

    def blanket_scenario(self):
        made = races.fixture(self, tests_lifecycle.PurchasingLifecycleTestCase)
        blanket = BlanketOrder.objects.create(vendor=made.vendor, start_date=D(2026, 1, 1), end_date=D(2026, 12, 31),
                                              currency=made.usd)
        agreed = BlanketOrderLine.objects.create(blanket=blanket, item=made.item, uom=made.uom,
                                                 quantity=Z("100"), unit_price=Z("5"))
        blanket.confirm()
        first = blanket.release({agreed: Z("40")}, order_date=D(2026, 2, 1))
        line_pk = first.lines.get().pk

        def raise_it():
            line = PurchaseOrderLine.objects.get(pk=line_pk)
            line.quantity = Z("70")
            line.save()

        def release_more():
            BlanketOrder.objects.get(pk=blanket.pk).release(
                {BlanketOrderLine.objects.get(pk=agreed.pk): Z("40")}, order_date=D(2026, 2, 2))

        return agreed, raise_it, release_more

    def test_05_o227_the_refutation_race_sees_nothing_deadlock_and_over_calls_nothing(self):
        agreed, raise_it, release_more = self.blanket_scenario()
        outcomes = races.race((PurchaseOrderLine, PurchaseOrder), raise_it, release_more)
        self.assertEqual(sum(o == "done" for o in outcomes), 1, outcomes)
        self.assertFalse(any("deadlock" in o for o in outcomes), outcomes)
        self.assertLessEqual(BlanketOrderLine.objects.get(pk=agreed.pk).quantity_released(), Z("100"))

    def test_06_o227_the_same_race_does_fail_when_the_agreements_lock_is_taken_away(self):
        """Soundness of the refutation's test: with the agreement never locked, both go through and 110 are called off."""
        from apps.purchasing import models

        real = models.lock_rows

        def without_the_agreement(*rows, **kwargs):
            rows = [row for row in rows if not isinstance(row, BlanketOrder)]
            return real(*rows, **kwargs) if rows else None

        agreed, raise_it, release_more = self.blanket_scenario()
        with mock.patch.object(models, "lock_rows", without_the_agreement):
            outcomes = races.race((PurchaseOrderLine, PurchaseOrder), raise_it, release_more)
        over = BlanketOrderLine.objects.get(pk=agreed.pk).quantity_released()
        self.assertTrue(over > Z("100") or sum(o == "done" for o in outcomes) == 2,
                        f"the race did not notice: outcomes {outcomes}, released {over}")
