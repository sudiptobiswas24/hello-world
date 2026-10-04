"""
Indexes that make `?search=` fast on PostgreSQL.

A search asks for text anywhere in a column (`icontains`), which an
ordinary index cannot answer: over five years of invoices, finding one by
part of its number read all 25,000 in 12.7 ms; a trigram index answered
in 0.13 ms. The index is on the expression Django's icontains compiles
to, UPPER(column::text), or the planner would not use it.

PostgreSQL only: SQLite has no such index, and on one laptop has no need
of one. pg_trgm ships with PostgreSQL and the database owner may enable
it; if it cannot be, the migration says so and carries on, since a slower
search is no reason to stop the plant's server from starting.
"""

import sys

from django.db import migrations


def _name(table, column):
    return f"{table}_{column}_trgm"[:63]


def trigram_indexes(*columns):
    """A migration operation adding a trigram index on each (table, column)."""

    def create(apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        with schema_editor.connection.cursor() as cursor:
            try:
                cursor.execute("SAVEPOINT trigram")
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                cursor.execute("RELEASE SAVEPOINT trigram")
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                cursor.execute("ROLLBACK TO SAVEPOINT trigram")
                sys.stderr.write(
                    f"Search indexes not created: pg_trgm could not be enabled ({error}). "
                    "Searches still work, more slowly. As a superuser run "
                    "CREATE EXTENSION pg_trgm; then migrate this app back and forward.\n"
                )
                return
            for table, column in columns:
                cursor.execute(
                    f'CREATE INDEX IF NOT EXISTS "{_name(table, column)}" ON "{table}" '
                    f'USING gin (UPPER("{column}"::text) gin_trgm_ops)'
                )

    def drop(apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        with schema_editor.connection.cursor() as cursor:
            for table, column in columns:
                cursor.execute(f'DROP INDEX IF EXISTS "{_name(table, column)}"')

    return migrations.RunPython(create, drop, elidable=False)
