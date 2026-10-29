"""The planner's view, which is the only thing that calls these."""

from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command

from .tests_orders import RunTestCase


class PlanARunTests(RunTestCase):
    def run_it(self, sku="TAPE-1000", quantity="1000", **options):
        out = StringIO()
        call_command("plan_run", sku, quantity, stdout=out, **options)
        return out.getvalue()

    def test_it_says_what_to_find_and_whether_it_is_there(self):
        report = self.run_it(warehouse="P")
        self.assertIn("PP-RAFFIA", report)
        # 773.1959 kg wanted against 2,000 on the shelf: nothing short.
        self.assertIn("773.1959", report)
        self.assertNotIn("SHORT", report)

    def test_it_says_when_the_yard_is_short(self):
        report = self.run_it(quantity="10000", warehouse="P")
        self.assertIn("SHORT", report)

    def test_it_puts_the_regrind_requirement_beside_the_recovery(self):
        report = self.run_it(warehouse="P")
        balance = report.split("gives back:")[1]
        self.assertIn("REGRIND", balance)
        # Needs 154.64 kg of reprocessed material and gives back 24.74.
        self.assertIn("154.6392", balance)
        self.assertIn("24.7423", balance)

    def test_an_item_nothing_knows_how_to_make(self):
        with self.assertRaises(CommandError):
            self.run_it(sku="PP-RAFFIA")

    def test_an_item_that_does_not_exist(self):
        with self.assertRaises(CommandError):
            self.run_it(sku="NOPE")
