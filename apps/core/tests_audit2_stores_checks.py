"""
Audit probe (audit2_stores): movements_without_their_money sees a movement written without its money
however it is spelled, and passes one that says it.
"""

from pathlib import Path

from django.test import SimpleTestCase

from apps.core.management.commands.audit_invariants import Command


def audit(call, header=""):
    text = (header + "class Write(AuditModel):\n    def post(self):\n        " + call + "\n")
    return Command().movements_without_their_money(["inventory"], {"inventory": {Path("apps/inventory/example.py"): text}})


class TheCheckSeesEverySpelling(SimpleTestCase):
    def test_a_qualified_class(self):
        self.assertTrue(audit("inventory_models.StockMovement.objects.create(item=i, quantity=-1)",
                              "import apps.inventory.models as inventory_models  # StockMovement\n"))

    def test_the_related_manager(self):
        self.assertTrue(audit("item.movements.create(warehouse=w, quantity=-1)", "# StockMovement\n"))

    def test_get_or_create(self):
        self.assertTrue(audit("StockMovement.objects.get_or_create(item=i, quantity=-1)"))

    def test_an_alias(self):
        self.assertTrue(audit("Movement.objects.create(item=i, quantity=-1)", "Movement = StockMovement\n"))

    def test_value_none_is_not_saying_it(self):
        self.assertTrue(audit("StockMovement.objects.create(item=i, quantity=-1, value=None)"))

    def test_a_movement_saved_by_hand(self):
        self.assertTrue(audit("m = StockMovement(item=i, quantity=-1)\n        m.save()"))

    def test_a_dict_of_fields_is_not_waved_through_without_value(self):
        self.assertTrue(audit("StockMovement.objects.create(item=i, **fields)"))

    def test_near_miss_value_adjustment_alone_is_not_value(self):
        self.assertTrue(audit("StockMovement.objects.create(item=i, quantity=0, value_adjustment=3)"))

    def test_near_miss_that_says_its_money_passes(self):
        self.assertEqual(audit("StockMovement.objects.create(item=i, quantity=-1, value=-3)"), [])
