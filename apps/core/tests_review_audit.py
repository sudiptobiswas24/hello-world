"""Review probes for the two-layer edit log (29e2e8b): each asserts what SHOULD be true."""

import datetime
import unittest
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from .history import RecordEvent, SECRET, edit_logged
from .models import Currency, ExchangeRate, Party, PartyBankAccount


def events_of(model, pk):
    return list(RecordEvent.objects.filter(object_id=pk, content_type__model=model._meta.model_name,
                                           content_type__app_label=model._meta.app_label).order_by("pk"))


class ApplicationLayerProbes(TestCase):
    def test_p01_a_migrations_historical_model_write_leaves_an_event(self):
        from django.db.migrations.executor import MigrationExecutor
        state = MigrationExecutor(connection).loader.project_state([("core", "0043_edit_log")]).apps
        hist = state.get_model("core", "Currency")
        hist.objects.create(code="ZZZ", name="Zed")
        hist.objects.filter(code="ZZZ").update(name="Zed2")
        hist.objects.get(code="ZZZ").delete()
        self.assertGreaterEqual(RecordEvent.objects.filter(content_type__model="currency").count(), 3,
                                "a RunPython write through apps.get_model leaves no application event")

    def test_p02_a_set_null_cascade_on_user_delete_leaves_an_event_on_the_child(self):
        maker = User.objects.create_user("maker")
        usd = Currency.objects.create(code="USD", name="US Dollar")
        rate = ExchangeRate.objects.create(currency=usd, rate=Decimal("83.25"), valid_from=datetime.date(2026, 10, 1),
                                           created_by=maker)
        before = len(events_of(ExchangeRate, rate.pk))
        maker.delete()
        rate.refresh_from_db()
        self.assertIsNone(rate.created_by_id)
        after = events_of(ExchangeRate, rate.pk)
        self.assertGreater(len(after), before, "created_by set to NULL by a cascade changed the row with no event")

    def test_p03_the_edit_log_itself_cannot_be_rewritten_or_deleted_through_the_orm(self):
        usd = Currency.objects.create(code="USD", name="US Dollar")
        line = events_of(Currency, usd.pk)[0]
        with self.assertRaises(Exception, msg="update of a log line was allowed"):
            RecordEvent.objects.filter(pk=line.pk).update(changes={}, summary="nothing happened")
        with self.assertRaises(Exception, msg="delete of a log line was allowed"):
            RecordEvent.objects.filter(pk=line.pk).delete()
        self.assertTrue(RecordEvent.objects.filter(pk=line.pk).exclude(summary="nothing happened").exists())

    def test_p04_a_deleted_bank_account_does_not_put_its_whole_number_in_the_summary(self):
        party = Party.objects.create(code="V1", name="Vendor")
        field_names = {f.name for f in PartyBankAccount._meta.concrete_fields}
        kwargs = {"party": party, "bank_name": "HDFC", "account_number": "123456789012"}
        account = PartyBankAccount(**{k: v for k, v in kwargs.items() if k in field_names})
        account.save()
        pk = account.pk
        account.delete()
        gone = [e for e in events_of(PartyBankAccount, pk) if e.kind == "deleted"][0]
        from .history import as_row
        row = as_row(gone)
        self.assertNotIn("123456789012", row["summary"], "the delete's summary is str(instance): the whole number")
        self.assertNotIn("123456789012", str(row["changes"]))

    def test_p06_no_field_that_looks_like_a_secret_escapes_the_name_rule(self):
        import re
        looks = re.compile(r"token|hash|(^|_)pin(_|$)|otp|salt|cipher|encrypt|credential|private|bearer|"
                           r"signature|api_?key|passcode|cvv|totp|seed", re.I)
        found = []
        for model in django_apps.get_models():
            if not edit_logged(model):
                continue
            for field in model._meta.concrete_fields:
                if looks.search(field.name) and not SECRET.search(field.name):
                    found.append(f"{model._meta.label}.{field.name}")
        self.assertEqual(found, [])

    def test_p07_deleting_a_role_logs_the_logins_lost_membership(self):
        group = Group.objects.create(name="Probe role")
        user = User.objects.create_user("member")
        user.groups.add(group)
        before = len([e for e in events_of(User, user.pk) if "groups" in e.changes])
        group.delete()
        after = len([e for e in events_of(User, user.pk) if "groups" in e.changes])
        self.assertGreater(after, before, "a login lost its role through the role's deletion: no line on the login")

    def test_p08_a_command_run_inside_a_request_keeps_the_requests_person(self):
        from .history import acting_as, acting
        user = User.objects.create_user("poster")
        with acting_as(user=user, through="api"):
            from django.core.management.base import BaseCommand

            class Probe(BaseCommand):
                def handle(self, *a, **o):
                    self.seen = acting()
                    return ""
            cmd = Probe()
            cmd.execute(skip_checks=True, no_color=True, force_color=False, stdout=None, stderr=None)
        self.assertEqual(cmd.seen[0], user, "who is lost when a command is called inside an acting_as block")


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL")
class DatabaseLayerProbes(TransactionTestCase):
    def setUp(self):
        from .ledger_log import create
        create(connection)

    def test_p10_health_sees_a_trigger_set_to_replica_only(self):
        from .ledger_log import tables_without_their_trigger
        with connection.cursor() as c:
            c.execute("ALTER TABLE accounting_journalentry ENABLE REPLICA TRIGGER erp_ledger_change")
        try:
            self.assertIn("accounting_journalentry", tables_without_their_trigger(connection),
                          "a REPLICA-only trigger never fires in normal mode and the health check calls it fine")
        finally:
            with connection.cursor() as c:
                c.execute("ALTER TABLE accounting_journalentry ENABLE TRIGGER erp_ledger_change")

    def test_p11_opening_balances_and_the_chart_are_changed_by_raw_sql_with_a_trace(self):
        from .ledger_log import LedgerChange
        from apps.accounting.models import Account, AccountType
        acct = Account.objects.create(code="9999", name="Probe", account_type=AccountType.ASSET)
        before = LedgerChange.objects.count()
        with connection.cursor() as c:
            c.execute("UPDATE accounting_account SET name = 'renamed by raw sql' WHERE id = %s", [acct.pk])
        self.assertGreater(LedgerChange.objects.count(), before, "raw SQL on the chart of accounts leaves no row")

    def test_p12_no_books_table_column_holds_a_secret_name_or_whole_account_number(self):
        from .ledger_log import BOOKS_TABLES
        with connection.cursor() as c:
            c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_name = ANY(%s) "
                      "AND column_name ~* 'password|digest|secret|sealed|token|hash|account_n|aadhaar|(^|_)pan($|_)|iban|(^|_)pin($|_)'",
                      [list(BOOKS_TABLES)])
            self.assertEqual(c.fetchall(), [])



from .tests_api_keys import KeyCase  # noqa: E402


class ApiKeyProbe(KeyCase):
    def test_p05_an_api_key_in_use_writes_no_event_per_request(self):
        integration = self.integration()
        issued = self.issue(self.hrc, integration)
        key = issued.json().get("key") or issued.json().get("secret")
        self.assertTrue(key, issued.json())
        client = self.keyed(key)
        before = RecordEvent.objects.filter(content_type__model="apikey").count()
        for _ in range(5):
            client.get("/api/core/me/")
        after = RecordEvent.objects.filter(content_type__model="apikey").count()
        self.assertLessEqual(after - before, 1, f"{after - before} edit-log lines for 5 requests with one key "
                                                "(last_used_at stamped and logged per request, never pruned)")


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL")
class AppRoleProbes(TransactionTestCase):
    """Connected as the application's role (as web/tests_books_log_roles.py makes it)."""

    def setUp(self):
        import os, secrets
        from .ledger_log import create, grant_app
        create(connection)
        self.role, self.password = f"erp_rev_{os.getpid()}", secrets.token_hex(8)
        grant_app(connection, self.role, self.password)
        self.owner = dict(connection.settings_dict)

    def tearDown(self):
        self.as_role(self.owner["USER"], self.owner["PASSWORD"])
        app, db = connection.ops.quote_name(self.role), connection.ops.quote_name(connection.settings_dict["NAME"])
        with connection.cursor() as c:
            for st in (f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {app}",
                       f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {app}",
                       f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app}",
                       f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {app}",
                       f"REVOKE ALL ON SCHEMA public FROM {app}", f"REVOKE ALL ON DATABASE {db} FROM {app}",
                       f"DROP ROLE IF EXISTS {app}"):
                c.execute(st)

    def as_role(self, user, password):
        connection.close()
        connection.settings_dict["USER"], connection.settings_dict["PASSWORD"] = user, password

    def refused(self, statement):
        from django.db import DatabaseError, transaction
        with self.assertRaises(DatabaseError, msg=statement), transaction.atomic():
            with connection.cursor() as c:
                c.execute(statement)

    def test_p13_the_application_layer_log_is_append_only_for_the_application_role(self):
        usd = Currency.objects.create(code="USD", name="US Dollar")
        self.as_role(self.role, self.password)
        self.refused("UPDATE core_recordevent SET changes = '{}'")
        self.refused("DELETE FROM core_recordevent")

    def test_p14_the_application_role_can_reset_a_sequence_as_loaddata_does(self):
        self.as_role(self.role, self.password)
        with connection.cursor() as c:
            c.execute("SELECT setval(pg_get_serial_sequence('core_currency', 'id'), 1000, false)")
