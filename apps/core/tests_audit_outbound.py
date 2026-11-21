"""
Stock taken off the shelf is priced by what leaving costs, or the audit says where not.

  A void writing an outbound movement and never asking cost_of_removing():
  reported. One that asks: not. A receipt: not outbound, not asked. An
  exemption whose function now asks: stale.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(label, body, name="void"):
    text = (
        "class ProductionEntry(AuditModel):\n"
        f"    def {name}(self):\n" + body
    )
    return Command().outbound_at_a_posted_figure([label], {label: {Path(f"apps/{label}/example.py"): text}})


def unpriced(body):
    """What the rule finds in the example, its other exemptions aside."""
    return [row for row in audit("manufacturing", body) if row[0] != "stale exemption"]


OUT = ("        StockMovement.objects.create(item=self.item, movement_type=MovementType.ISSUE,\n"
       "                                     quantity=-moved.quantity, unit_cost=self.unit_cost)\n")


class LeavingIsPricedByTheShelfTests(SimpleTestCase):
    def test_a_void_at_the_posted_cost_is_reported(self):
        found = unpriced(OUT)
        self.assertEqual([shape for shape, _ in found], ["outbound at a posted figure"])
        self.assertIn("example.py:3 void() takes self.item off the shelf", found[0][1])

    def test_one_that_asks_is_not(self):
        self.assertEqual(unpriced("        going = cost_of_removing(self.item, w, q)\n" + OUT), [])

    def test_a_receipt_is_not_asked(self):
        self.assertEqual(unpriced(OUT.replace("ISSUE", "RECEIPT")), [])

    def test_an_exemption_outlives_nothing(self):
        found = audit("purchasing", "        going = cost_of_removing(self.item, w, q)\n" + OUT)
        self.assertEqual(sorted(shape for shape, _ in found), ["stale exemption"] * 2)
