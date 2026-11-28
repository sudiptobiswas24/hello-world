"""
accounting 0033 (F2 cutover) run as it runs: through MigrationExecutor, against the historical models.
  0032: RC1 has no waiting account, RC2 names 1391; no account 1319 -> 0033 sets nothing, its summary names RC1 only
  back to 0032, 1319 made, forward again -> RC1 waits in 1319, RC2 still in 1391, the summary names RC1
"""

import contextlib
import io

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag

BEFORE = [("accounting", "0032_tax_reverse_charge_waiting_account")]
AFTER = [("accounting", "0033_reverse_charge_waiting_account_cutover")]


@tag("migration")
class WaitingAccountCutoverMigrationTests(TransactionTestCase):
    def migrate(self, target):
        executor = MigrationExecutor(connection)
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            executor.migrate(target)
        return executor.loader.project_state(target).apps, said.getvalue()

    def test_set_where_1319_exists_and_the_rest_named(self):
        apps, _ = self.migrate(BEFORE)
        account, tax = apps.get_model("accounting", "Account"), apps.get_model("accounting", "Tax")

        def ledger(code, kind):
            return account.objects.create(code=code, name=code, account_type=kind)

        paid, owed, kept = ledger("1390", "asset"), ledger("2291", "liability"), ledger("1391", "asset")
        for code, waiting in (("RC1", None), ("RC2", kept)):
            tax.objects.create(code=code, name=code, rate="5", gst_head="igst", scope="purchase", paid_account=paid,
                               reverse_charge=True, reverse_charge_account=owed,
                               reverse_charge_waiting_account=waiting)

        apps, said = self.migrate(AFTER)
        tax = apps.get_model("accounting", "Tax")
        self.assertEqual(dict(tax.objects.values_list("code", "reverse_charge_waiting_account__code")),
                         {"RC1": None, "RC2": "1391"})
        self.assertIn("no account 1319", said)
        self.assertIn("left without a credit waiting account: RC1.", said)
        self.assertNotIn("RC2", said)

        apps, _ = self.migrate(BEFORE)
        apps.get_model("accounting", "Account").objects.create(code="1319", name="Reverse-charge credit awaiting",
                                                               account_type="asset")
        apps, said = self.migrate(AFTER)
        self.assertEqual(dict(apps.get_model("accounting", "Tax").objects.values_list(
            "code", "reverse_charge_waiting_account__code")), {"RC1": "1319", "RC2": "1391"})
        self.assertIn("reverse-charge credit waits in 1319 for RC1.", said)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
