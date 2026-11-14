"""
Migration probe for the review of fix_sec3 (g22 review_sec): core 0036 and 0037
apply over a database with logins in it, leave them alone, and reverse. Run
with the real settings (migrations on), not fastsettings.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

BEFORE = ("core", "0035_" )
TABLES = {"core_guardedright", "core_secondfactor", "core_recoverycode", "core_integration", "core_apikey",
          "core_apikeyuse"}


def _leaf(name):
    executor = MigrationExecutor(connection)
    (match,) = [key for key in executor.loader.graph.nodes if key[0] == "core" and key[1].startswith(name)]
    return match


@tag("migration")
class MigrationProbes(TransactionTestCase):
    def tables(self):
        return set(connection.introspection.table_names())

    def test_m01_0036_and_0037_apply_over_existing_logins_and_reverse(self):
        before, last = _leaf("0035_"), _leaf("0037_")
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        self.assertFalse(TABLES & self.tables(), "control: the new tables are absent at 0035")
        executor = MigrationExecutor(connection)
        old = executor.loader.project_state([before]).apps
        old.get_model("auth", "User").objects.create(username="pre-existing", password="!")
        executor.migrate([last])
        self.assertTrue(TABLES <= self.tables(), TABLES - self.tables())
        executor = MigrationExecutor(connection)
        new = executor.loader.project_state([last]).apps
        self.assertEqual(new.get_model("auth", "User").objects.filter(username="pre-existing").count(), 1)
        self.assertEqual(new.get_model("core", "SecondFactor").objects.count(), 0)
        executor.migrate([before])
        self.assertFalse(TABLES & self.tables(), "reversing 0037 and 0036 leaves tables behind")
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertTrue(TABLES <= self.tables())
