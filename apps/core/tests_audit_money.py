"""
A stock movement says its money, or the audit names it (O269).

  Written without value=: reported, with its line. With it: not. A
  movement built from **fields: not read, and not reported.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(body):
    text = (
        "class Write(AuditModel):\n"
        "    def post(self):\n"
        "        StockMovement.objects.create(item=item, uom=item.uom, " + body + ")\n"
    )
    return Command().movements_without_their_money(["inventory"], {"inventory": {Path("apps/inventory/example.py"): text}})


class AMovementSaysItsMoneyTests(SimpleTestCase):
    def test_one_written_without_its_money_is_reported(self):
        found = audit("quantity=-quantity, unit_cost=rate")
        self.assertEqual([shape for shape, _ in found], ["movement without its money"])
        self.assertIn("inventory/example.py:3 writes a stock movement without value=", found[0][1])

    def test_one_that_says_what_it_booked_is_not(self):
        self.assertEqual(audit("quantity=-quantity, unit_cost=rate, value=-taking"), [])

    def test_the_application_writes_every_movement_with_its_money(self):
        from apps.core.management.commands.audit_invariants import OUR_APPS, app_sources

        self.assertEqual(Command().movements_without_their_money(list(OUR_APPS), app_sources()), [])
