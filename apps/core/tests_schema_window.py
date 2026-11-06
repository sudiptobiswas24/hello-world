"""
For a migration test: today's models written while the schema stands at an older migration.

Several migration tests migrate back, make rows with today's models, then migrate forward. They worked
while nothing had added a column to those tables since: sales 0084 added taxes_taken_under (O446) to the
sales order and invoice, and five such tests stopped on "no column named taxes_taken_under_id". Each
column added since that the old schema lacks is added for the window (TodaysColumns()) and dropped by
close(), before the test goes on to the latest migrations, which add it for real.
"""

from django.apps import apps
from django.db import connection


def _columns(model):
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}


def _added_since():
    from apps.accounting.mixins import TaxesTakenUnder

    return [model._meta.get_field("taxes_taken_under") for model in apps.get_models()
            if issubclass(model, TaxesTakenUnder)]


class TodaysColumns:
    """
    Plain column DDL both ways, a bare nullable column: a schema editor rebuilds an SQLite table from
    today's model, every column it has since gained with it, which is not the old schema.
    """

    def __init__(self, fields=None):
        self.added = []
        with connection.cursor() as cursor:
            for field in fields or _added_since():
                if field.column not in _columns(field.model):
                    cursor.execute(f"ALTER TABLE {self._name(field.model._meta.db_table)} "
                                   f"ADD COLUMN {self._name(field.column)} bigint NULL")
                    self.added.append(field)

    @staticmethod
    def _name(name):
        return connection.ops.quote_name(name)

    def close(self):
        with connection.cursor() as cursor:
            for field in self.added:
                if field.column in _columns(field.model):
                    cursor.execute(f"ALTER TABLE {self._name(field.model._meta.db_table)} "
                                   f"DROP COLUMN {self._name(field.column)}")
        self.added = []
