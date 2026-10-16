"""
The order things are planned in, and the loop that has no order.

The cycle tests are the ones that matter. A bill of materials graph
with a loop in it has no valid planning sequence, and what this module
does about that — cut the link, keep the levels out of it, and say so
— is a decision the rest of the plan depends on.
"""

from decimal import Decimal

from apps.manufacturing.bom import BillOfMaterials, BomComponent

from .levels import level_of, low_level_codes
from .tests_base import PlantTestCase


class LowLevelCodeTests(PlantTestCase):
    def test_a_finished_good_is_at_the_top(self):
        self.assertEqual(level_of(low_level_codes(), self.fabric), 0)

    def test_each_stage_sits_below_the_one_that_eats_it(self):
        levels = low_level_codes()
        self.assertEqual(level_of(levels, self.tape), 1)
        self.assertEqual(level_of(levels, self.virgin), 2)
        self.assertEqual(level_of(levels, self.regrind), 2)

    def test_an_item_nothing_mentions_is_at_the_top(self):
        other = self.fabric.__class__.objects.create(
            sku="PALLET", name="Pallet", uom=self.kg
        )
        self.assertEqual(level_of(low_level_codes(), other), 0)

    def test_an_item_used_at_two_depths_takes_the_deeper_one(self):
        # Polymer straight into the fabric BOM as well as into tape:
        # it must not be netted until the tape run has asked too.
        BomComponent.objects.create(
            bom=self.fabric_bom, item=self.virgin, quantity=Decimal("1"),
            uom=self.kg, line_number=2,
        )
        self.assertEqual(level_of(low_level_codes(), self.virgin), 2)

    def test_a_straight_graph_cuts_nothing(self):
        self.assertEqual(low_level_codes().cuts, [])


class WhenTheGraphLoopsTests(PlantTestCase):
    def setUp(self):
        super().setUp()
        # Reprocessing closes the loop: regrind is made from fabric
        # offcuts, fabric is woven from tape, and tape eats regrind.
        self.reprocess = BillOfMaterials.objects.create(
            item=self.regrind, name="Reprocess", quantity_produced=Decimal("100"),
            uom=self.kg,
        )
        BomComponent.objects.create(
            bom=self.reprocess, item=self.fabric, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )

    def test_the_walk_comes_back(self):
        # Without the path guard this does not terminate at all.
        levels = low_level_codes()
        self.assertTrue(levels.code_of)

    def test_it_says_which_links_it_cut(self):
        cuts = {(cut.parent.sku, cut.item.sku) for cut in low_level_codes().cuts}
        self.assertIn(("REGRIND", "FAB-10X10"), cuts)
        self.assertIn(("TAPE-1000", "REGRIND"), cuts)

    def test_a_cut_link_does_not_push_the_item_below_its_own_consumer(self):
        """
        The guard that keeps the cut honest.

        A cut that still counted towards a level would send tape below
        regrind — so tape's demand for regrind would be raised after
        regrind had already been netted, which is the exact failure
        low-level coding exists to prevent.
        """
        levels = low_level_codes()
        self.assertLessEqual(
            level_of(levels, self.tape), level_of(levels, self.regrind)
        )
