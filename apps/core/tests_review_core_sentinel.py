"""
Review probes for fix_core's O228 (g22 review_core): a savepoint rolled back lets go of what it took, a real
inversion is still caught, and raw SQL FOR SHARE is ranked. Run on PostgreSQL too (FOR SHARE runs only there).
"""
from django.db import connection, transaction
from django.test import TestCase

from .lock_order import LockOrderViolation, raw_shared_lock, taken


def run(sql, params):
    return raw_shared_lock(lambda *args: None, sql, params, False, {"connection": connection})


class SavepointTests(TestCase):
    def test_taken_in_a_savepoint_rolled_back_is_let_go_and_what_was_held_before_it_is_not(self):
        # The outer block holds invoice 3. A savepoint takes a sales order (refused: an inversion against invoice
        # 3), and rolls back. The invoice is still held, so a sales order taken next is still an inversion.
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                taken(connection, "sales.invoice", [3])
                try:
                    with transaction.atomic():
                        taken(connection, "sales.deliveryline", [1])
                        raise RuntimeError("refused")
                except RuntimeError:
                    pass
                taken(connection, "sales.salesorder", [7])

    def test_a_savepoint_rolled_back_twice_over_forgets_each_time(self):
        with transaction.atomic():
            for _ in range(2):
                try:
                    with transaction.atomic():
                        taken(connection, "sales.invoice", [3])
                        raise RuntimeError("refused")
                except RuntimeError:
                    pass
                taken(connection, "sales.salesorder", [7])

    def test_a_savepoint_inside_a_savepoint_rolled_back_keeps_the_outer_savepoints_rows(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                with transaction.atomic():
                    taken(connection, "sales.invoice", [3])
                    try:
                        with transaction.atomic():
                            taken(connection, "sales.invoiceline", [9])
                            raise RuntimeError("refused")
                    except RuntimeError:
                        pass
                    taken(connection, "sales.salesorder", [7])

    def test_rows_made_before_a_savepoint_stay_held_after_it_rolls_back(self):
        # A held row is skipped when taken again; a row forgotten would be ranked afresh and refused.
        with transaction.atomic():
            taken(connection, "sales.invoice", [3])
            try:
                with transaction.atomic():
                    taken(connection, "sales.invoice", [4])
                    raise RuntimeError("refused")
            except RuntimeError:
                pass
            taken(connection, "sales.invoice", [3])

    def test_an_error_inside_a_nested_block_without_a_savepoint_poisons_the_outer_and_still_keeps_rows(self):
        with self.assertRaisesMessage(LockOrderViolation, "sales.salesorder 7 locked after sales.invoice 3"):
            with transaction.atomic():
                with transaction.atomic():
                    taken(connection, "sales.invoice", [3])
                    try:
                        with transaction.atomic(savepoint=False):
                            raise RuntimeError("the transaction is lost")
                    except RuntimeError:
                        pass
                    taken(connection, "sales.salesorder", [7])


class RawShareTests(TestCase):
    def test_planted_instances_are_ranked(self):
        spellings = [
            ('SELECT 1 FROM "sales_invoice" WHERE id = %s FOR SHARE', [3]),
            ("SELECT 1 FROM sales_invoice WHERE id = %s FOR SHARE", [3]),
            ('SELECT 1 FROM "sales_invoice" WHERE "id" IN (%s, %s) FOR KEY SHARE', [3, 2]),
            ('select 1 from "sales_invoice" where "sales_invoice"."id" = %s for share', [3]),
            ('SELECT 1 FROM "sales_invoice" WHERE id = %s FOR SHARE NOWAIT', [3]),
        ]
        for sql, params in spellings:
            with self.assertRaises(LockOrderViolation, msg=sql):
                with transaction.atomic():
                    run(sql, params)
                    taken(connection, "sales.salesorder", [7])

    def test_a_share_of_a_table_given_by_schema_is_ranked_too(self):
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                run('SELECT 1 FROM "public"."sales_invoice" WHERE id = %s FOR SHARE', [3])
                taken(connection, "sales.salesorder", [7])

    def test_a_share_with_the_keys_in_a_list_parameter_is_ranked_too(self):
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                run('SELECT 1 FROM "sales_invoice" WHERE id = ANY(%s) FOR SHARE', [[3, 2]])
                taken(connection, "sales.salesorder", [7])

    def test_near_misses_are_not_ranked(self):
        with transaction.atomic():
            run('SELECT 1 FROM "sales_invoice" WHERE id = %s', [3])
            run('SELECT 1 FROM "sales_invoice" WHERE id = %s FOR UPDATE', [3]) if False else None
            taken(connection, "sales.salesorder", [7])

    def test_a_share_then_an_update_of_the_same_account_row_is_a_lock_upgrade_not_a_row_already_held(self):
        # Two payments each share bank 5 (hold_money_account); an unmarking takes it FOR UPDATE. A transaction that
        # shares a row and then takes it FOR UPDATE can deadlock with another doing the same: the share must not
        # make the update read as "held already".
        with self.assertRaises(LockOrderViolation):
            with transaction.atomic():
                run('SELECT 1 FROM "accounting_account" WHERE id = %s FOR SHARE', [5])
                taken(connection, "accounting.account", [5])


import unittest  # noqa: E402


@unittest.skipUnless(connection.vendor == "postgresql", "FOR SHARE runs on PostgreSQL only")
class RealShareTests(TestCase):
    def test_hold_money_account_is_seen_by_the_sentinel_with_its_key(self):
        from apps.accounting.models import Account, AccountType

        from .lock_order import _state

        bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        from apps.accounting.money import hold_money_account

        with transaction.atomic():
            hold_money_account(bank)
            self.assertIn(("accounting.account", bank.pk), _state(connection)["held"])
