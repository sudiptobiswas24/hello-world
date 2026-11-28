"""
The books' change log under two roles (apps/core/ledger_log.py): its trigger function made again as SECURITY
DEFINER with a fixed search_path, so it writes the log as the owner, and the application's role (when settings
name one: DATABASE_URL beside DATABASE_OWNER_URL) given rows to read and write and nothing more. Idempotent;
apps.core runs the grants again at the end of every migrate. Reverse: the function as 0001 made it stays
replaced (harmless under one role); the grants are left (revoking them would stop the application).
"""

import importlib

from django.db import migrations

TABLES = importlib.import_module("apps.web.migrations.0001_books_change_log").TABLES


def forwards(apps, schema_editor):
    from django.conf import settings

    from apps.core.ledger_log import create, grant_app

    create(schema_editor.connection, TABLES)
    grant_app(schema_editor.connection, settings.DATABASE_APP_ROLE, settings.DATABASE_APP_PASSWORD)


class Migration(migrations.Migration):
    dependencies = [("web", "0001_books_change_log")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
