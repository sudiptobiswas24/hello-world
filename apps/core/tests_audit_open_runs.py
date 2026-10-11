"""
A run is asked whether it is open by something holding it, or the audit says so.

  A void asking the order with no lock: reported. Holding it first: not
  reported. A complaint asking itself: not a run, not asked.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(body):
    text = "def void(self):\n" + body
    return Command().unlocked_open_runs(
        ["manufacturing"], {"manufacturing": {Path("apps/manufacturing/example.py"): text}})


class ARunIsHeldBeforeItIsAskedTests(SimpleTestCase):
    def test_asked_unheld_is_reported(self):
        found = audit("    if not self.work_order.is_open():\n        raise ValidationError('x')\n")
        self.assertEqual([shape for shape, _ in found], ["open run read unheld"])
        self.assertIn("example.py:2 void() asks self.work_order", found[0][1])

    def test_held_first_is_not(self):
        self.assertEqual(audit("    lock_rows(self.work_order)\n"
                               "    if not self.work_order.is_open():\n        raise ValidationError('x')\n"), [])

    def test_something_else_open_is_not_a_run(self):
        self.assertEqual(audit("    if not self.is_open():\n        raise ValidationError('x')\n"), [])
