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

    def test_lock_rows_takes_rows_by_the_lock_orders_rank_not_by_name(self):
        """
        "accounting.account" sorts before "core.party" by name and after it by rank: lock_rows sorted by name
        took the account first and the party after it, against the order it exists to keep.
        """
        from apps.accounting.models import Account, AccountType

        account = Account.objects.create(code="1300", name="Employee advances", account_type=AccountType.ASSET)
        with transaction.atomic():
            lock_rows(account, self.first)

    def test_lock_rows_takes_a_payment_before_the_account_it_moves_money_through(self):
        """
        O371: "accounting.account" sorts before "accounting.payment" by name and after it by rank (O258). Sorted
        by name, lock_rows(payment, account) took the account first, and every caller handing it both had to
        split the call, as the statutory remittance did. One call takes the payment first, whichever is named
        first.
        """
        import datetime
        from decimal import Decimal

        from apps.accounting.models import Account, AccountType, Payment, PaymentDirection

        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        payable = Account.objects.create(code="2010", name="Payable", account_type=AccountType.LIABILITY)
        payment = Payment.objects.create(party=self.first, direction=PaymentDirection.DISBURSEMENT,
                                         payment_date=datetime.date(2026, 10, 1), amount=Decimal("100.00"),
                                         bank_account=bank, counterpart_account=payable)
        with transaction.atomic():
            lock_rows(payment, payable)
            taken(connection, "sales.invoice", [1])
        with transaction.atomic():
            lock_rows(payable, payment)

    def test_an_account_is_ranked_after_the_payment_and_before_the_invoice(self):
        """O258: a payment, then the account it moves money through, then a sales invoice; never the account first."""
        with transaction.atomic():
            taken(connection, "accounting.payment", [4])
            taken(connection, "accounting.account", [9], how="for_share")
            taken(connection, "sales.invoice", [1])
        with self.assertRaisesMessage(LockOrderViolation, "accounting.payment 4 locked after accounting.account 9"):
            with transaction.atomic():
                taken(connection, "accounting.account", [9])
                taken(connection, "accounting.payment", [4])

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

    def test_a_migrations_own_models_are_not_held_to_the_order(self):
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader

        historical = MigrationLoader(connection).project_state().apps.get_model("core", "Party")
        with transaction.atomic():
            lock_rows(self.second)
            historical.objects.filter(pk=self.first.pk).update(name="in a data migration")
            list(historical.objects.select_for_update().filter(pk=self.first.pk))
        with self.assertRaisesMessage(LockOrderViolation, "(update)"):
            with transaction.atomic():
                lock_rows(self.second)
                Party.objects.filter(pk=self.first.pk).update(name="in the application")

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


class TheSentinelForgetsASavepointRolledBackAndSeesForShareTests(TestCase):
    """O228: rows taken inside a savepoint that rolled back are let go, as the database lets go of them;
    raw SQL FOR SHARE (hold_money_account) is ranked."""

    def test_a_row_taken_in_a_savepoint_rolled_back_is_not_held_after_it(self):
        # Before: the invoice stayed held, and the sales order taken next was refused as after it.
        with transaction.atomic():
            try:
                with transaction.atomic():
                    taken(connection, "sales.invoice", [3])
                    raise RuntimeError("refused; the savepoint rolls back")
            except RuntimeError:
                pass
            taken(connection, "sales.salesorder", [7])

    def test_a_savepoint_released_keeps_what_it_took(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                with transaction.atomic():
                    taken(connection, "sales.invoice", [3])
                taken(connection, "sales.salesorder", [7])

    def test_a_block_without_a_savepoint_keeps_what_it_took(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                try:
                    with transaction.atomic(savepoint=False):
                        taken(connection, "sales.invoice", [3])
                        raise RuntimeError("the whole transaction is lost, not a savepoint")
                except RuntimeError:
                    pass
                taken(connection, "sales.salesorder", [7])

    def test_raw_for_share_is_ranked_with_its_keys(self):
        from .lock_order import raw_shared_lock

        def run(sql, params):
            return raw_shared_lock(lambda *args: None, sql, params, False, {"connection": connection})

        self.assertIn(raw_shared_lock, connection.execute_wrappers)
        # Two shares wait for no one: a transfer from bank 5 to bank 3 holds them in its own order.
        with transaction.atomic():
            run('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [5])
            run('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [3])
        # A shared row is ranked against the others: an invoice shared, then its order taken, is refused.
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                run('SELECT 1 FROM "sales_invoice" WHERE "id" IN (%s, %s) FOR KEY SHARE', [3, 2])
                taken(connection, "sales.salesorder", [7])


class TheSentinelSeesAShareUpgradedAndASchemaNamedTableTests(TestCase):
    """O228 review F5 (a) and (b): a share then an update of one row is an upgrade; "public"."t" is t."""

    @staticmethod
    def share(sql, params):
        from .lock_order import raw_shared_lock

        return raw_shared_lock(lambda *args: None, sql, params, False, {"connection": connection})

    def test_a_share_then_an_update_of_the_same_row_is_refused_and_of_another_row_is_not(self):
        with self.assertRaisesMessage(LockOrderViolation, "accounting.account 5 taken (select_for_update) after "
                                                          "this transaction shared it"):
            with transaction.atomic():
                self.share('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [5])
                taken(connection, "accounting.account", [5])
        with transaction.atomic():
            self.share('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [5])
            self.share('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [5])
            taken(connection, "accounting.account", [6])

    def test_a_table_named_with_its_schema_is_ranked(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                self.share('SELECT 1 FROM "public"."sales_invoice" WHERE id = %s FOR SHARE', [3])
                taken(connection, "sales.salesorder", [7])


class NumberingIsHeldBehindTheCompanysOneRowTests(TestCase):
    """
    O257: a document numbering a second type inside its own transaction took that type's row after its own,
    and two transactions numbering the two types the other way round deadlocked. Every number is taken behind
    the company's one numbering row now (DocumentSequence.next_value), so the order between types decides
    nothing; the sentinel holds the shape: sequence rows out of key order without that row, or the row
    taken after a sequence, are refused.
    """

    def setUp(self):
        from .models import DocumentSequence

        self.first = DocumentSequence.objects.create(code="test.first", name="First", prefix="F-")
        self.second = DocumentSequence.objects.create(code="test.second", name="Second", prefix="S-")
        self.assertLess(self.first.pk, self.second.pk)

    def test_two_types_numbered_against_key_order_in_one_transaction_pass(self):
        from .models import DocumentSequence

        with transaction.atomic():
            later = DocumentSequence.next_for("test.second", "2026-10-01")
            earlier = DocumentSequence.next_for("test.first", "2026-10-01")
            made = DocumentSequence.next_for("test.third", "2026-10-01", name="Third", prefix="T-")
        self.assertEqual((later, earlier, made), ("S-2026-00001", "F-2026-00001", "T-2026-00001"))

    def test_sequences_taken_against_key_order_without_the_numbering_row_are_refused(self):
        # Put core.documentsequence back on UNSEEN and this passes unseen: the shape O257 deadlocked on.
        with self.assertRaisesMessage(LockOrderViolation,
                                      f"core.documentsequence {self.first.pk} locked after core.documentsequence "
                                      f"{self.second.pk}"):
            with transaction.atomic():
                lock_rows(self.second, refresh=False)
                lock_rows(self.first, refresh=False)

    def test_the_numbering_row_taken_after_a_sequence_is_refused(self):
        from .models import DocumentNumbering

        with self.assertRaisesMessage(LockOrderViolation,
                                      "core.documentnumbering 1 locked after core.documentsequence"):
            with transaction.atomic():
                lock_rows(self.first, refresh=False)
                DocumentNumbering.hold()

    def test_a_year_counter_is_behind_the_row_too(self):
        from .models import DocumentSequenceYear

        early = DocumentSequenceYear.objects.create(sequence=self.first, year=2026, next_number=4)
        late = DocumentSequenceYear.objects.create(sequence=self.second, year=2026, next_number=9)
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                lock_rows(late, refresh=False)
                lock_rows(early, refresh=False)
        from .models import DocumentSequence

        with transaction.atomic():
            self.assertEqual(DocumentSequence.next_for("test.second", "2026-10-01"), "S-2026-00009")
            self.assertEqual(DocumentSequence.next_for("test.first", "2026-10-01"), "F-2026-00004")

    def test_a_number_is_not_taken_outside_a_transaction_the_numbering_opened(self):
        from .models import DocumentNumbering

        with self.assertRaisesMessage(RuntimeError, "outside a transaction"):
            DocumentNumbering.hold()
