"""
The stock shapes the stores audit found more than once, asked of the code.

A shelf read before it is held, a withdrawal priced at a rate multiplied
back up, and movements written around the checks their save() makes:
each was a defect in more than one place before it was a rule.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def asked(check, text, name="example.py"):
    return getattr(Command(), check)(["sales"], {"sales": {Path("apps/sales") / name: text}})


READ_THEN_LOCK = '''
def post(self):
    if item.on_hand_at(shelf) < wanted:
        raise ValidationError("short")
    lock_positions([(item, shelf)])
'''

LOCK_THEN_READ = '''
def post(self):
    lock_positions([(item, shelf)])
    if item.on_hand_at(shelf) < wanted:
        raise ValidationError("short")
'''


class ShelfReadBeforeItsLockTests(SimpleTestCase):
    def test_a_read_before_the_lock_is_reported(self):
        (finding,) = asked("shelf_read_before_lock", READ_THEN_LOCK)
        self.assertEqual(finding[0], "shelf read before its lock")
        self.assertIn("sales/example.py:post", finding[1])

    def test_the_lock_first_is_not(self):
        self.assertEqual(asked("shelf_read_before_lock", LOCK_THEN_READ), [])

    def test_a_read_with_no_lock_in_the_function_is_not_this_shape(self):
        self.assertEqual(asked("shelf_read_before_lock", "def f():\n    return item.on_hand_at(shelf)\n"), [])


class PerUnitWithdrawalRateTests(SimpleTestCase):
    def test_a_rate_asked_for_a_withdrawal_is_reported(self):
        (finding,) = asked("per_unit_withdrawal_rates",
                           "def f():\n    cost = item.removal_unit_cost(shelf, quantity)\n")
        self.assertEqual(finding[0], "per-unit withdrawal rate")
        self.assertIn("sales/example.py:2", finding[1])

    def test_the_total_is_not(self):
        self.assertEqual(asked("per_unit_withdrawal_rates",
                               "def f():\n    cost = item.cost_of_removing(shelf, quantity)\n"), [])


class MovementsWrittenAroundSaveTests(SimpleTestCase):
    def test_a_bulk_insert_is_reported(self):
        (finding,) = asked("movements_written_around_save",
                           "def f():\n    StockMovement.objects.bulk_create(rows)\n")
        self.assertIn("bulk_create", finding[1])

    def test_a_queryset_update_is_reported(self):
        (finding,) = asked("movements_written_around_save",
                           "def f():\n    StockMovement.objects.filter(pk=1).update(quantity=0)\n")
        self.assertIn("update", finding[1])

    def test_one_by_one_is_not(self):
        self.assertEqual(asked("movements_written_around_save",
                               "def f():\n    StockMovement.objects.create(item=item)\n"), [])
