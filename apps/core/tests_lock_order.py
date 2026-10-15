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

    def test_each_transaction_starts_afresh_and_unranked_models_pass_beside_ranked_ones(self):
        with transaction.atomic():
            lock_rows(self.second)
        with transaction.atomic():
            taken(connection, "sales.salesorder", [1])
            taken(connection, "manufacturing.machine", [1])
            lock_rows(self.first)
            taken(connection, "manufacturing.machine", [2])

    def test_an_unranked_models_own_rows_are_taken_in_key_order(self):
        # O182: two landed costs onto the same two receipt lines, listed the other way round, took
        # their stock positions 2 then 1 and 1 then 2, and deadlocked: ranked against nothing, the
        # model was not held to key order either.
        with self.assertRaisesMessage(LockOrderViolation, "manufacturing.machine 1 locked after manufacturing.machine 2"):
            with transaction.atomic():
                taken(connection, "manufacturing.machine", [2])
                taken(connection, "manufacturing.machine", [1])


class TheSentinelSeesEveryLockAStatementTakesTests(TestCase):
    """O184: what review_trade3 found it could not see (S3, S4, S5), each planted."""

    def setUp(self):
        self.first, self.second = (Party.objects.create(code=f"P{n}", name=f"P{n}") for n in (1, 2))

    def test_a_values_list_without_the_pk_still_names_the_rows(self):
        with self.assertRaisesMessage(LockOrderViolation, f"core.party {self.first.pk} locked after core.party"):
            with transaction.atomic():
                list(Party.objects.select_for_update().filter(pk=self.second.pk).values_list("code", flat=True))
                list(Party.objects.select_for_update().filter(pk=self.first.pk).values_list("code", flat=True))

    def test_a_values_list_lock_is_checked_in_the_order_it_was_taken(self):
        # Review F8: descending key order read as codes passed, the keys read back sorted.
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                list(Party.objects.select_for_update().order_by("-pk").values_list("code", flat=True))

    def test_an_update_takes_its_rows(self):
        with self.assertRaisesMessage(LockOrderViolation, "(update)"):
            with transaction.atomic():
                lock_rows(self.second)
                Party.objects.filter(code="P1").update(name="x")

    def test_a_save_of_a_row_that_stood_takes_it(self):
        with self.assertRaisesMessage(LockOrderViolation, f"core.party {self.first.pk} locked after core.party"):
            with transaction.atomic():
                lock_rows(self.second)
                self.first.name = "y"
                self.first.save()

    def test_a_delete_takes_its_rows(self):
        with self.assertRaisesMessage(LockOrderViolation, "(delete)"):
            with transaction.atomic():
                lock_rows(self.second)
                Party.objects.filter(pk=self.first.pk).delete()

    def test_a_joined_table_is_taken_with_the_row(self):
        from apps.core.models import Address

        address = Address.objects.create(party=self.first, line1="Plot 4", city="Pune")
        with self.assertRaisesMessage(LockOrderViolation, "(joined)"):
            with transaction.atomic():
                lock_rows(self.second)
                # Filtered through the party, so the join is an inner one: PostgreSQL refuses FOR UPDATE on
                # the nullable side of the outer join a bare select_related("party") would make.
                list(Address.objects.select_related("party").select_for_update()
                     .filter(pk=address.pk, party__code=self.first.code))

    def test_a_row_made_or_held_is_not_taken_again(self):
        with transaction.atomic():
            lock_rows(self.second)
            third = Party.objects.create(code="P3", name="P3")
            Party.objects.filter(pk=self.second.pk).update(name="again")
            third.name = "made here"
            third.save()
            made = Party.objects.bulk_create([Party(code="P4", name="P4")])
            Party.objects.filter(pk__in=[row.pk for row in made]).update(name="made in bulk")

    def test_a_statement_outside_any_transaction_holds_nothing_after_it(self):
        self.first.name = "autocommit"
        self.first.save()
        with transaction.atomic():
            lock_rows(self.second)

    def test_the_order_is_the_written_one(self):
        self.assertEqual(LOCK_ORDER[:5], ("purchasing.purchaseorder", "sales.salesorder",
                                          "purchasing.purchaseorderline", "sales.salesorderline", "core.party"))
