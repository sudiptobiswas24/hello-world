"""The lock-order sentinel (apps/core/lock_order.py): a planted reversal fails, the order passes."""

from django.conf import settings
from django.db import connection, transaction
from django.test import TestCase

from .lock_order import LOCK_ORDER, LockedOutsideATransaction, LockOrderViolation, taken
from .models import Party, lock_rows


class TheSentinelHoldsTestsToTheLockOrderTests(TestCase):
    def setUp(self):
        self.first, self.second = (Party.objects.create(code=f"P{n}", name=f"P{n}") for n in (1, 2))

    def test_it_is_on_under_tests(self):
        self.assertTrue(settings.LOCK_ORDER_SENTINEL)

    def test_a_later_rank_then_an_earlier_one_is_refused(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                taken(connection, "sales.invoice", [3])
                taken(connection, "sales.salesorder", [7])

    def test_rows_of_one_model_are_taken_in_key_order(self):
        with self.assertRaisesMessage(LockOrderViolation, f"core.party {self.first.pk} locked after core.party"):
            with transaction.atomic():
                lock_rows(self.second)
                lock_rows(self.first)

    def test_any_select_for_update_counts(self):
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                list(Party.objects.select_for_update().filter(code="P2"))
                lock_rows(self.first)

    def test_a_lock_outside_any_transaction_the_code_opened_is_refused(self):
        # On SQLite the lock is ignored and on PostgreSQL it is a 500; a
        # TestCase's own block is not one the code opened.
        with self.assertRaisesMessage(LockedOutsideATransaction, "core.party locked outside any transaction"):
            list(Party.objects.select_for_update().filter(code="P1"))

    def test_the_written_order_and_a_row_already_held_pass(self):
        with transaction.atomic():
            taken(connection, "purchasing.purchaseorder", [9])
            taken(connection, "sales.salesorder", [1])
            lock_rows(self.first)
            lock_rows(self.second)
            lock_rows(self.first)  # held already: taken again, it waits for nothing
            taken(connection, "sales.salesorder", [1])
            taken(connection, "sales.delivery", [1])
            taken(connection, "sales.invoice", [1])

    def test_each_transaction_starts_afresh_and_unranked_models_pass(self):
        with transaction.atomic():
            lock_rows(self.second)
        with transaction.atomic():
            taken(connection, "sales.salesorder", [1])
            taken(connection, "manufacturing.machine", [1])
            lock_rows(self.first)
            taken(connection, "manufacturing.machine", [0])

    def test_the_order_is_the_written_one(self):
        self.assertEqual(LOCK_ORDER[:5], ("purchasing.purchaseorder", "sales.salesorder",
                                          "purchasing.purchaseorderline", "sales.salesorderline", "core.party"))
