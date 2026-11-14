"""
The database's own change log (apps/core/ledger_log.py) on every table of the product, not only the 19 of the
books: the chart of accounts, opening balances, pay terms, statements, deliveries, receipts, masters, logins and
roles. So raw SQL and a migration's own writes leave their rows wherever they land.

Left out, and only these: Django's infrastructure, which no person decides and every sign-in writes
(django_session, django_admin_log, django_content_type, auth_permission, django_migrations), and the two logs
themselves (core_ledgerchange, core_recordevent, each append-only by its own trigger).

A table a later migration adds gets its trigger at the end of every migrate (apps.core.apps, as the grants do),
and the health check names any product table without one. Reverse: the triggers on tables beyond 0001's list.
"""

import importlib

from django.db import migrations

BOOKS = importlib.import_module("apps.web.migrations.0001_books_change_log").TABLES


def forwards(apps, schema_editor):
    from apps.core.ledger_log import create

    create(schema_editor.connection)


def backwards(apps, schema_editor):
    from apps.core.ledger_log import TRIGGER, product_tables

    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in product_tables(connection):
            if table not in BOOKS:
                cursor.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {table}")


class Migration(migrations.Migration):
    dependencies = [("web", "0002_books_log_two_roles")]

    operations = [migrations.RunPython(forwards, backwards)]
