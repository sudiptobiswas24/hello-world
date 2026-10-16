"""
A stock movement names its item's own unit, or the audit says where it does not.

  Output in the entry's unit with a cost per stocking unit: reported. In
  the item's unit: not reported. An exemption whose movement has since
  moved to the item's unit: reported as stale.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(label, body):
    text = (
        "class ProductionEntry(AuditModel):\n"
        "    def post(self):\n"
        "        StockMovement.objects.create(item=order.item, " + body + ")\n"
    )
    return Command().movements_in_another_unit([label], {label: {Path(f"apps/{label}/example.py"): text}})


class AMovementIsInItsItemsUnitTests(SimpleTestCase):
    def test_one_in_the_documents_unit_is_reported(self):
        found = audit("manufacturing", "uom=self.uom, quantity=self.quantity_produced")
        self.assertEqual([shape for shape, _ in found], ["movement in another unit"])
        self.assertIn("example.py:3 writes order.item in self.uom", found[0][1])

    def test_one_in_the_items_unit_is_not(self):
        self.assertEqual(audit("manufacturing", "uom=order.item.uom, quantity=made"), [])

    def test_an_exemption_outlives_nothing(self):
        found = audit("purchasing", "uom=order.item.uom, quantity=made")
        self.assertEqual(sorted(shape for shape, _ in found), ["stale exemption"] * 3)
