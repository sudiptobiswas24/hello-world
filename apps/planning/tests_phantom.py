"""
Planning through a phantom, and by the day a recipe is in force.

The fixture's phantom is a blend: 100 kg of it from 75 of virgin and 25
of regrind, never made or stocked on its own. A mix sold by the kilo is
made from it one for one. No stock anywhere, so a thousand kilos of mix
wants, hand-checked, 750 of virgin and 250 of regrind — and no order
for the blend at all.
"""

from decimal import Decimal

from apps.inventory.models import Item
from apps.manufacturing.bom import BillOfMaterials, BomComponent

from .levels import level_of, low_level_codes
from .models import DemandSource, PlannedOrderKind
from .tests_mrp import PlanningTestCase


class PhantomTestCase(PlanningTestCase):
    def setUp(self):
        super().setUp()
        self.blend = Item.objects.create(sku="BLEND", name="Blend", uom=self.kg)
        blend_bom = BillOfMaterials.objects.create(
            item=self.blend, quantity_produced=Decimal("100"), uom=self.kg,
            is_phantom=True,
        )
        for number, (item, quantity) in enumerate(
            ((self.virgin, "75"), (self.regrind, "25")), start=1
        ):
            BomComponent.objects.create(
                bom=blend_bom, item=item, quantity=Decimal(quantity),
                uom=self.kg, line_number=number,
            )
        self.mix = Item.objects.create(sku="MIX", name="Mix", uom=self.kg)
        mix_bom = BillOfMaterials.objects.create(
            item=self.mix, quantity_produced=Decimal("100"), uom=self.kg,
        )
        BomComponent.objects.create(
            bom=mix_bom, item=self.blend, quantity=Decimal("100"),
            uom=self.kg, line_number=1,
        )

    def kinds(self, run, item):
        return [order.kind for order in run.orders.filter(item=item)]


class APlannedRunDrawsThroughItTests(PhantomTestCase):
    def test_no_order_is_raised_for_the_phantom(self):
        self.sell(self.mix, "1000", self.day(30))
        orders = self.orders()
        self.assertEqual(orders["MIX"].quantity, Decimal("1000"))
        self.assertNotIn("BLEND", orders)

    def test_its_materials_are_wanted_by_the_run_that_makes_it(self):
        self.sell(self.mix, "1000", self.day(30))
        orders = self.orders()
        self.assertEqual(orders["PP-RAFFIA"].quantity, Decimal("750"))
        self.assertEqual(orders["REGRIND"].quantity, Decimal("250"))
        demand = orders["PP-RAFFIA"].demands.get()
        self.assertEqual(demand.parent, orders["MIX"])


class SellingThePhantomItselfTests(PhantomTestCase):
    def test_its_shortfall_goes_straight_to_its_components(self):
        self.sell(self.blend, "100", self.day(30))
        run = self.plan()
        self.assertEqual(self.kinds(run, self.blend), [])
        virgin = run.orders.get(item=self.virgin)
        self.assertEqual(virgin.quantity, Decimal("75"))
        demand = virgin.demands.get()
        self.assertEqual(demand.source, DemandSource.PHANTOM)
        # Wanted the day the blend is: it is made inside whatever run
        # asked for it, so there is no lead time of its own.
        self.assertEqual(demand.needed_by, self.day(30))
        self.assertIn("phantom", demand.describe())

    def test_what_is_on_its_shelf_is_used_first(self):
        """
        Forty kilos of blend left over from before it became a phantom:
        sixty is short, and sixty is 45 of virgin and 15 of regrind.
        """
        self.stock(self.blend, "40")
        self.sell(self.blend, "100", self.day(30))
        orders = self.orders()
        self.assertEqual(orders["PP-RAFFIA"].quantity, Decimal("45"))
        self.assertEqual(orders["REGRIND"].quantity, Decimal("15"))


class MadeOrBoughtByTheDayTests(PlanningTestCase):
    """
    A coating bought until the plant's own line comes up on day 20 and
    made after. One item, two answers, and the planner must give both.
    """

    def test_a_shortfall_before_the_change_is_bought_and_after_it_made(self):
        coat = Item.objects.create(sku="COAT", name="Coating", uom=self.kg)
        bom = BillOfMaterials.objects.create(
            item=coat, quantity_produced=Decimal("100"), uom=self.kg,
            valid_from=self.day(20),
        )
        BomComponent.objects.create(
            bom=bom, item=self.virgin, quantity=Decimal("100"), uom=self.kg,
            line_number=1,
        )
        self.sell(coat, "100", self.day(10))
        self.sell(coat, "100", self.day(40))
        run = self.plan()
        by_date = {
            order.needed_by: order.kind
            for order in run.orders.filter(item=coat)
        }
        self.assertEqual(by_date[self.day(10)], PlannedOrderKind.BUY)
        self.assertEqual(by_date[self.day(40)], PlannedOrderKind.MAKE)


class StructureCountsEveryWindowTests(PlanningTestCase):
    def test_a_component_only_in_next_months_recipe_still_has_a_level(self):
        """
        Given no level, it would be netted before the run that will ask
        for it had asked, and its demand would be reported as missed.
        """
        future = Item.objects.create(sku="FUT", name="Future", uom=self.kg)
        newcomer = Item.objects.create(sku="NEW", name="Newcomer", uom=self.kg)
        today_bom = BillOfMaterials.objects.create(
            item=future, version=1, quantity_produced=Decimal("1"),
            uom=self.kg, valid_to=self.day(99),
        )
        BomComponent.objects.create(
            bom=today_bom, item=self.virgin, quantity=Decimal("1"),
            uom=self.kg, line_number=1,
        )
        next_bom = BillOfMaterials.objects.create(
            item=future, version=2, quantity_produced=Decimal("1"),
            uom=self.kg, valid_from=self.day(100),
        )
        BomComponent.objects.create(
            bom=next_bom, item=newcomer, quantity=Decimal("1"),
            uom=self.kg, line_number=1,
        )
        levels = low_level_codes()
        self.assertGreater(level_of(levels, newcomer), level_of(levels, future))

    def test_and_keeps_it_when_its_parent_sits_lower_down(self):
        """
        Three deep, because two deep proves nothing: the top-level pass
        starts from every recipe anyway, so a component of FUT's next
        recipe gets level one from that alone. What needs the walk is
        FUT itself sitting under TOP — then FUT is at one and its
        newcomer must be pushed to two by walking the recipe that is
        not in force yet.
        """
        top = Item.objects.create(sku="TOP", name="Top", uom=self.kg)
        future = Item.objects.create(sku="FUT", name="Future", uom=self.kg)
        newcomer = Item.objects.create(sku="NEW", name="Newcomer", uom=self.kg)
        top_bom = BillOfMaterials.objects.create(
            item=top, quantity_produced=Decimal("1"), uom=self.kg,
        )
        BomComponent.objects.create(
            bom=top_bom, item=future, quantity=Decimal("1"), uom=self.kg,
            line_number=1,
        )
        today_bom = BillOfMaterials.objects.create(
            item=future, version=1, quantity_produced=Decimal("1"),
            uom=self.kg, valid_to=self.day(99),
        )
        BomComponent.objects.create(
            bom=today_bom, item=self.virgin, quantity=Decimal("1"),
            uom=self.kg, line_number=1,
        )
        next_bom = BillOfMaterials.objects.create(
            item=future, version=2, quantity_produced=Decimal("1"),
            uom=self.kg, valid_from=self.day(100),
        )
        BomComponent.objects.create(
            bom=next_bom, item=newcomer, quantity=Decimal("1"),
            uom=self.kg, line_number=1,
        )
        levels = low_level_codes()
        self.assertEqual(level_of(levels, future), 1)
        self.assertEqual(level_of(levels, newcomer), 2)
