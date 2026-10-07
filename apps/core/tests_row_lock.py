"""
A row locked by itself.

A model sorted through a relation joins that relation's table into every
query that keeps the sort. PostgreSQL will not lock the nullable side of
an outer join, so closing a complaint, whose actions sort by their alert,
failed there; on an inner join it locks the related row too. SQLite takes
no row locks, so the suite never saw it. Asked here of every model's lock
as SQL, which SQLite can answer.
"""

from django.apps import apps
from django.test import SimpleTestCase

from .models import row_lock_query


class EveryRowLocksByItselfTests(SimpleTestCase):
    def test_no_lock_joins_another_table(self):
        joined = [model._meta.label for model in apps.get_models() if " JOIN " in str(row_lock_query(model, 1).query)]
        self.assertEqual(joined, [])
