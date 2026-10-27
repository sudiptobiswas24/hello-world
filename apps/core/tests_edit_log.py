"""
The edit log (apps/core/history.py, apps/core/ledger_log.py): every change of a record leaves its line with each
field's old and new value, who and through what, by every path: the API, the admin, the shell, a management
command, a bulk update, a delete. On PostgreSQL the books' tables are also logged by the database itself, a raw
SQL change included, into a log that refuses its own UPDATE, DELETE and TRUNCATE.
"""

import datetime
import unittest
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from .history import EventKind, RecordEvent, acting_as, edit_logged, installed
from .ledger_log import LedgerChange, create as create_ledger_log
from .models import AuditModel, Currency, ExchangeRate, Party
from .tests_second_factor import staff_session


def lines(record):
    return list(RecordEvent.objects.filter(object_id=record.pk, content_type__model=record._meta.model_name,
                                           content_type__app_label=record._meta.app_label).order_by("pk"))


class EveryPathLeavesOldAndNewTests(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="US Dollar")
        self.rate = ExchangeRate.objects.create(currency=self.usd, rate=Decimal("83.25"), valid_from=datetime.date(2026, 10, 1))

    def test_an_admin_change(self):
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        staff_session(self.client, root)
        response = self.client.post(f"/admin/core/exchangerate/{self.rate.pk}/change/", {
            "currency": self.usd.pk, "rate": "83.2575", "valid_from": "2026-10-01"})
        self.assertEqual(response.status_code, 302, response.content[:2000])
        line = lines(self.rate)[-1]
        self.assertEqual((line.kind, line.through, line.who, line.who_name), (EventKind.UPDATED, "admin", root, "root"))
        self.assertEqual(line.changes["rate"], {"from": "83.25000000", "to": "83.25750000"})
        self.assertNotIn("valid_from", line.changes)

    def test_a_shell_change(self):
        call_command("shell", command=f"from apps.core.models import Currency; c = Currency.objects.get(pk={self.usd.pk}); "
                                      "c.name = 'United States dollar'; c.save()")
        line = lines(self.usd)[-1]
        self.assertEqual((line.kind, line.through, line.who), (EventKind.UPDATED, "shell", None))
        self.assertEqual(line.changes, {"name": {"from": "US Dollar", "to": "United States dollar"}})

    def test_a_management_commands_change_by_its_name(self):
        call_command("setup_roles", verbosity=0)
        auditor = Group.objects.get(name="Auditor")
        line = [event for event in lines(auditor) if "permissions" in event.changes][-1]
        self.assertEqual(line.through, "command:setup_roles")
        self.assertEqual(line.changes["permissions"]["from"], [])
        self.assertIn("Core | record event | Can view record event", line.changes["permissions"]["to"])

    def test_a_bulk_update(self):
        ExchangeRate.objects.filter(currency=self.usd).update(rate=Decimal("84.1"))
        line = lines(self.rate)[-1]
        self.assertEqual((line.kind, line.through), (EventKind.UPDATED, "command:test"))
        self.assertEqual(line.changes, {"rate": {"from": "83.25000000", "to": "84.10000000"}})

    def test_a_bulk_update_naming_no_row_writes_no_line(self):
        before = RecordEvent.objects.count()
        self.assertEqual(ExchangeRate.objects.filter(currency__code="EUR").update(rate=Decimal("90")), 0)
        self.assertEqual(RecordEvent.objects.count(), before)

    def test_a_delete_keeps_every_value_it_had(self):
        pk = self.rate.pk
        ExchangeRate.objects.filter(pk=pk).delete()
        line = RecordEvent.objects.filter(object_id=pk, content_type__model="exchangerate").latest("pk")
        self.assertEqual(line.kind, EventKind.DELETED)
        self.assertEqual(line.changes["rate"], {"from": "83.25000000", "to": None})
        self.assertEqual(line.changes["currency"], {"from": {"id": self.usd.pk, "label": str(self.usd)}, "to": None})
        self.assertEqual(line.changes["valid_from"], {"from": "2026-10-01", "to": None})

    def test_a_key_is_its_id_and_label(self):
        made = lines(self.rate)[0]
        self.assertEqual(made.kind, EventKind.CREATED)
        self.assertEqual(made.changes["currency"], {"from": None, "to": {"id": self.usd.pk, "label": str(self.usd)}})
        self.assertEqual(made.changes["rate"], {"from": None, "to": "83.25000000"})

    def test_a_password_is_only_changed(self):
        user = User.objects.create_user("asha", password="first one")
        user.set_password("second one")
        user.save()
        line = lines(user)[-1]
        self.assertEqual(line.changes, {"password": {"changed": True}})
        self.assertNotIn("first one", str(lines(user)[0].changes))
        self.assertEqual(lines(user)[0].changes["password"], {"changed": True})

    def test_a_bank_account_number_is_kept_whole_and_shown_by_its_last_four(self):
        from django.contrib.contenttypes.models import ContentType

        from .history import changes_for

        event = RecordEvent(content_type=ContentType.objects.get(app_label="hr", model="employeepaydetails"), object_id=1,
                            kind=EventKind.UPDATED,
                            changes={"account_number": {"from": "300100000001", "to": "555500001111"}})
        self.assertEqual(changes_for(event), [{"field": "account_number", "label": "account number",
                                               "from": "••••••••0001", "to": "••••••••1111"}])

    def test_the_api_names_its_person_and_the_record_page_shows_both_values(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("asha"))
        party = client.post("/api/core/parties/", {"code": "C-9", "name": "Shree Cement"}, format="json").json()
        self.assertEqual(client.patch(f"/api/core/parties/{party['id']}/", {"name": "Shree Cement Ltd"},
                                      format="json").status_code, 200)
        rows = client.get("/api/core/history/", {"model": "core.party", "id": party["id"]}).json()
        self.assertEqual((rows[0]["kind"], rows[0]["who"], rows[0]["through"]), ("updated", "asha", "api"))
        self.assertEqual(rows[0]["changes"], [{"field": "name", "label": "name", "from": "Shree Cement",
                                               "to": "Shree Cement Ltd"}])

    def test_a_script_acting_for_someone_says_so(self):
        user = User.objects.create_user("ravi")
        with acting_as(user=user, through="script"):
            self.usd.symbol = "$"
            self.usd.save()
        line = lines(self.usd)[-1]
        self.assertEqual((line.who, line.through, line.changes), (user, "script", {"symbol": {"from": "", "to": "$"}}))


class TheEditLogIsKeptAsWrittenTests(TestCase):
    def test_a_line_cannot_be_changed_or_removed_by_any_orm_path(self):
        from .history import KeptAsWritten

        line = lines(Currency.objects.create(code="USD", name="US Dollar"))[0]
        line.summary = "nothing happened"
        for attempt in (line.save, line.delete, lambda: RecordEvent.objects.filter(pk=line.pk).update(summary="x"),
                        lambda: RecordEvent.objects.filter(pk=line.pk).delete(),
                        lambda: RecordEvent._base_manager.filter(pk=line.pk).delete(),
                        lambda: RecordEvent.objects.bulk_update([line], ["summary"])):
            with self.subTest(attempt), self.assertRaisesMessage(KeptAsWritten, "kept as written"):
                attempt()
        self.assertEqual(RecordEvent.objects.get(pk=line.pk).summary, "")

    @unittest.skipUnless(connection.vendor == "postgresql", "the database's own guard is PostgreSQL's")
    def test_the_database_refuses_its_update_and_delete(self):
        create_ledger_log(connection)
        Currency.objects.create(code="USD", name="US Dollar")
        for statement in ("UPDATE core_recordevent SET summary = 'x'", "DELETE FROM core_recordevent"):
            with self.subTest(statement), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)


class WhoActedIsKeptTests(TestCase):
    def test_a_login_never_acted_goes_one_that_acted_stays_admin_included(self):
        from .history import HasActed

        User.objects.create_user("never").delete()
        self.assertFalse(User.objects.filter(username="never").exists())
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        doer = User.objects.create_user("doer")
        with acting_as(user=doer):
            Currency.objects.create(code="INR", name="Rupee")
        staff_session(self.client, root)
        response = self.client.post(f"/admin/auth/user/{doer.pk}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        # The admin's bulk delete: refused too (it lists what protects the login, and deletes nothing).
        self.client.post("/admin/auth/user/", {"action": "delete_selected", "_selected_action": [doer.pk], "post": "yes"})
        self.assertTrue(User.objects.filter(pk=doer.pk).exists())
        self.assertTrue(User.objects.filter(pk=doer.pk).exists())
        with self.assertRaisesMessage(HasActed, "has lines in the edit log as the one who acted"):
            doer.delete()


class WhatIsLoggedTests(TestCase):
    def test_every_audit_model_is_logged_and_the_bulk_writes_are_wrapped(self):
        from django.apps import apps

        self.assertTrue(installed())
        unlogged = [model._meta.label for model in apps.get_models()
                    if issubclass(model, AuditModel) and not edit_logged(model)]
        self.assertEqual(unlogged, [])
        self.assertFalse(edit_logged(RecordEvent))
        self.assertTrue(edit_logged(User))

    def test_the_audit_reports_a_planted_raw_write_and_an_unlogged_audit_model(self):
        from pathlib import Path

        from apps.core.management.commands.audit_invariants import Command

        planted = {Path("apps/core/planted.py"): 'def f(cursor):\n    cursor.execute("UPDATE sales_invoice SET invoice_date = %s", [d])\n'}

        class Planted(Currency):
            not_edit_logged = "planted"

            class Meta:
                proxy = True
                app_label = "core"

        found = Command().writes_without_an_event(planted, models=[Planted])
        self.assertTrue(edit_logged(Planted))  # a proxy's saves are its table's, logged whatever it says
        self.assertEqual(len(found), 2, found)
        self.assertIn("apps/core/planted.py:2", found[0][1])
        self.assertIn("Planted", found[1][1])
        self.assertEqual(Command().writes_without_an_event({}, models=[Currency]), [])


@unittest.skipUnless(connection.vendor == "postgresql", "the database's own log is PostgreSQL's")
class TheDatabaseKeepsTheBooksLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        create_ledger_log(connection)  # made by migration web 0001; again here for a database built without it
        from apps.accounting.models import Account, AccountType

        cls.account = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET)
        cls.user = User.objects.create_user("meera")

    def entry(self):
        from apps.accounting.models import JournalEntry

        return JournalEntry.objects.create(date=datetime.date(2026, 10, 1), memo="Opening")

    def test_a_raw_sql_update_is_logged_with_old_and_new_rows(self):
        entry = self.entry()
        with connection.cursor() as cursor:
            cursor.cursor.execute("SELECT set_config('erp.actor', '', true)")  # as psql would: nobody said
            cursor.cursor.execute("UPDATE accounting_journalentry SET memo = 'Changed by hand' WHERE id = %s", [entry.pk])
        change = LedgerChange.objects.filter(table_name="accounting_journalentry", row_id=str(entry.pk)).first()
        self.assertEqual((change.operation, change.through, change.acting_user_id), ("UPDATE", "unattributed", None))
        self.assertEqual((change.old_row["memo"], change.new_row["memo"]), ("Opening", "Changed by hand"))

    def test_the_application_tells_the_database_who_is_acting(self):
        with acting_as(user=self.user, through="api"):
            entry = self.entry()
            with connection.cursor() as cursor:
                cursor.execute("UPDATE accounting_journalentry SET memo = %s WHERE id = %s", ["Raw, from the app", entry.pk])
        rows = list(LedgerChange.objects.filter(table_name="accounting_journalentry", row_id=str(entry.pk)).order_by("id"))
        self.assertEqual([(row.operation, row.acting_user_id, row.through) for row in rows],
                         [("INSERT", self.user.pk, "api"), ("UPDATE", self.user.pk, "api")])

    def test_a_delete_keeps_the_row(self):
        from apps.accounting.models import JournalEntry

        entry = self.entry()
        JournalEntry.objects.filter(pk=entry.pk).delete()
        change = LedgerChange.objects.filter(table_name="accounting_journalentry", row_id=str(entry.pk)).first()
        self.assertEqual((change.operation, change.old_row["memo"], change.new_row), ("DELETE", "Opening", None))

    def test_the_log_refuses_its_update_delete_and_truncate(self):
        self.entry()
        for statement in (f"UPDATE core_ledgerchange SET through = 'api'", "DELETE FROM core_ledgerchange",
                          "TRUNCATE core_ledgerchange"):
            with self.subTest(statement), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
        self.assertTrue(LedgerChange.objects.exists())

    def test_the_health_check_names_unattributed_rows_and_a_table_without_its_trigger(self):
        from apps.web.health import _books_change_log

        self.assertTrue(_books_change_log(None)["ok"])
        entry = self.entry()
        with connection.cursor() as cursor:
            cursor.cursor.execute("SELECT set_config('erp.actor', '', true)")
            cursor.cursor.execute("UPDATE accounting_journalentry SET memo = 'x' WHERE id = %s", [entry.pk])
            cursor.execute("ALTER TABLE accounting_payment DISABLE TRIGGER erp_ledger_change")
        finding = _books_change_log(None)
        self.assertFalse(finding["ok"])
        self.assertIn("accounting_payment", finding["detail"])
        self.assertIn("1 change", finding["detail"])
