"""
The audit trail cannot be disabled from the application (Companies (Accounts) Rules 2014, rule 3(1)): connected
as the application's role, which owns nothing (apps/core/ledger_log.grant_app), a books table's trigger cannot be
disabled and the change log cannot be updated, deleted from or truncated; an ordinary posting still writes the
log, through the owner's SECURITY DEFINER trigger. The lanes run as one role; this test makes the second.
"""

import os
import secrets
import unittest
from decimal import Decimal

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from apps.core.ledger_log import LedgerChange, create, grant_app


@unittest.skipUnless(connection.vendor == "postgresql", "the database's own log is PostgreSQL's")
class TheApplicationsRoleCannotDisableTheTrailTests(TransactionTestCase):
    def setUp(self):
        create(connection)  # made by migration web 0001; again for a database built without migrations
        self.role, self.password = f"erp_app_test_{os.getpid()}", secrets.token_hex(16)
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cursor.fetchone()[0]:
                self.skipTest("the test role cannot make a role: ALTER ROLE <it> CREATEROLE")
        grant_app(connection, self.role, self.password)
        self.owner = dict(connection.settings_dict)

    def tearDown(self):
        self.as_role(self.owner["USER"], self.owner["PASSWORD"])
        app, database = connection.ops.quote_name(self.role), connection.ops.quote_name(connection.settings_dict["NAME"])
        with connection.cursor() as cursor:
            for statement in (
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {app}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {app}",
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {app}",
                f"REVOKE ALL ON SCHEMA public FROM {app}",
                f"REVOKE ALL ON DATABASE {database} FROM {app}",
                f"DROP ROLE IF EXISTS {app}",
            ):
                cursor.execute(statement)

    def as_role(self, user, password):
        connection.close()
        connection.settings_dict["USER"], connection.settings_dict["PASSWORD"] = user, password

    def refused(self, statement):
        with self.assertRaises(DatabaseError, msg=statement), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement)

    def test_refused_the_trigger_and_the_log_while_a_posting_is_still_logged(self):
        from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine

        cash = Account.objects.create(code="1000", name="Cash", account_type=AccountType.ASSET, holds_money=True)
        revenue = Account.objects.create(code="4000", name="Revenue", account_type=AccountType.INCOME)
        self.as_role(self.role, self.password)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            self.assertEqual(cursor.fetchone()[0], self.role)
        entry = JournalEntry.objects.create(date="2026-01-01", memo="Sale")
        JournalLine.objects.create(entry=entry, account=cash, debit=Decimal("100"))
        JournalLine.objects.create(entry=entry, account=revenue, credit=Decimal("100"))
        entry.post()
        rows = LedgerChange.objects.filter(table_name="accounting_journalentry", row_id=str(entry.pk))
        self.assertEqual([row.operation for row in rows.order_by("id")][:1], ["INSERT"])
        self.assertTrue(rows.filter(operation="UPDATE", new_row__posted=True).exists())
        self.assertEqual(LedgerChange.objects.filter(table_name="accounting_journalline",
                                                     new_row__entry_id=entry.pk).count(), 2)
        for statement in ("ALTER TABLE accounting_journalentry DISABLE TRIGGER erp_ledger_change",
                          "ALTER TABLE accounting_journalentry DISABLE TRIGGER ALL",
                          "DROP TRIGGER erp_ledger_change ON accounting_journalentry",
                          "UPDATE core_ledgerchange SET through = 'api'",
                          "DELETE FROM core_ledgerchange",
                          "INSERT INTO core_ledgerchange (table_name, operation, through) VALUES ('x', 'INSERT', 'api')",
                          "TRUNCATE core_ledgerchange",
                          "TRUNCATE accounting_journalentry CASCADE",
                          "CREATE TABLE app_owned (id int)"):
            with self.subTest(statement):
                self.refused(statement)
        self.assertTrue(rows.exists())
