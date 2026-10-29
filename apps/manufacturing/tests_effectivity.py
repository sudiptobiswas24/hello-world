"""
Which recipe, on which day — and a recipe nobody stocks.

Effectivity: a sack specification changes from the first of November.
Runs whose output is due before then are made the old way, runs due
after the new way, and an explosion on either side of the change must
say which.

Phantoms: an intermediate that exists for a few metres between two
machines on one run. The fixture's is a coloured blend — 10 kg made of
8 kg of virgin and 2 kg of masterbatch — going into a product at 5 kg
a 10 kg batch alongside 5 kg of virgin directly. A hundred kilos of the
product therefore draws, hand-checked: 50 kg of blend, which is 40 of
virgin and 10 of colour, plus the 50 of virgin taken directly. Ninety
of virgin on one frozen row, not fifty and forty on two.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.inventory.models import Item

from .bom import (
    BillOfMaterials,
    BomByproduct,
    BomComponent,
    ByproductValuation,
    default_bom_for,
    default_boms_for,
    drawn,
    explode,
    planned_cost,
)
from .orders import WorkOrder
from .tests_orders import TODAY, RunTestCase

OCT_31 = datetime.date(2026, 10, 31)
NOV_1 = datetime.date(2026, 11, 1)


class EffectivityTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.product = Item.objects.create(sku="PROD", name="Product", uom=self.kg)

    def recipe(self, version, valid_from=None, valid_to=None, item=None,
               **kwargs):
        return BillOfMaterials.objects.create(
            item=item or self.product, version=version,
            quantity_produced=Decimal("10"), uom=self.kg,
            valid_from=valid_from, valid_to=valid_to, **kwargs,
        )

    def line(self, bom, item, quantity, number=1):
        return BomComponent.objects.create(
            bom=bom, item=item, quantity=Decimal(quantity), uom=self.kg,
            line_number=number,
        )


class WhichRecipeOnWhichDayTests(EffectivityTestCase):
    def setUp(self):
        super().setUp()
        self.old = self.recipe(1, valid_to=OCT_31)
        self.new = self.recipe(2, valid_from=NOV_1)

    def test_the_old_recipe_answers_up_to_its_last_day(self):
        self.assertEqual(default_bom_for(self.product, OCT_31), self.old)

    def test_the_new_one_from_its_first(self):
        self.assertEqual(default_bom_for(self.product, NOV_1), self.new)

    def test_a_recipe_that_has_ended_answers_nothing_after_it(self):
        """
        Not covered by the pair above, where the newer recipe also
        matches and sorts first, so a lookup that forgot the end date
        still returned the right one by the luck of the ordering. With
        nothing after it, an ended recipe must leave the item unmade.
        """
        self.new.delete()
        self.assertIsNone(default_bom_for(self.product, NOV_1))

    def test_every_recipe_answers_a_question_about_structure(self):
        self.assertEqual(default_boms_for(self.product), [self.old, self.new])

    def test_a_window_that_overlaps_by_a_day_is_refused(self):
        self.old.valid_to = NOV_1
        with self.assertRaisesMessage(ValidationError, "already the recipe"):
            self.old.save()

    def test_a_backwards_window_is_refused_with_a_reason(self):
        with self.assertRaisesMessage(ValidationError, "backwards"):
            self.recipe(3, valid_from=NOV_1, valid_to=OCT_31)

    def test_and_by_the_database_where_no_reason_was_asked(self):
        """
        A recipe that is not the default skips the overlap question, so
        only the table stands between it and a window that ends before
        it starts.
        """
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.recipe(3, valid_from=NOV_1, valid_to=OCT_31, is_default=False)

    def test_a_retired_recipe_takes_no_part(self):
        self.old.is_active = False
        self.old.save()
        self.assertIsNone(default_bom_for(self.product, OCT_31))

    def test_the_database_refuses_two_from_the_beginning(self):
        """
        The second backstop: both running from the beginning, one
        bounded and one not, so the open-ended rule alone would pass
        them.
        """
        product = Item.objects.create(sku="P2", name="Other", uom=self.kg)
        self.recipe(1, valid_to=OCT_31, item=product)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BillOfMaterials.objects.bulk_create([BillOfMaterials(
                item=product, version=2, quantity_produced=Decimal("10"),
                uom=self.kg, valid_to=datetime.date(2026, 12, 31),
            )])


class AnExplosionReadsTheDateAllTheWayDownTests(EffectivityTestCase):
    def test_a_sub_assembly_changing_recipe_changes_the_leaves(self):
        top = Item.objects.create(sku="TOP", name="Top", uom=self.kg)
        top_bom = self.recipe(1, item=top)
        self.line(top_bom, self.product, "10")
        self.line(self.recipe(1, valid_to=OCT_31), self.virgin, "10")
        self.line(self.recipe(2, valid_from=NOV_1), self.filler, "10")
        october = {r.item.sku for r in explode(top_bom, 10, on_date=OCT_31)}
        november = {r.item.sku for r in explode(top_bom, 10, on_date=NOV_1)}
        self.assertIn("PP-RAFFIA", october)
        self.assertNotIn("CACO3", october)
        self.assertIn("CACO3", november)
        self.assertNotIn("PP-RAFFIA", november)


class ReleaseAgainstTheRecipeInForceTests(EffectivityTestCase):
    def setUp(self):
        super().setUp()
        self.old = self.recipe(1, valid_to=OCT_31)
        self.line(self.old, self.virgin, "10")
        self.new = self.recipe(2, valid_from=NOV_1)
        self.line(self.new, self.filler, "10")

    def run_due(self, bom, due):
        return WorkOrder.objects.create(
            item=self.product, bom=bom, quantity_ordered=Decimal("10"),
            uom=self.kg, warehouse=self.plant, scheduled_end=due,
        )

    def test_a_run_due_in_november_on_the_old_recipe_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "in force then"):
            self.run_due(self.old, NOV_1).release(TODAY)

    def test_a_run_due_in_october_on_it_is_released(self):
        order = self.run_due(self.old, OCT_31)
        order.release(TODAY)
        self.assertEqual(order.components.get().item, self.virgin)

    def test_without_a_due_date_the_release_date_decides(self):
        order = self.run_due(self.new, None)
        with self.assertRaisesMessage(ValidationError, "in force then"):
            order.release(OCT_31)
        order.release(NOV_1)
        self.assertEqual(order.components.get().item, self.filler)


class ASpecificationDatesItsRecipeTests(EffectivityTestCase):
    """
    The plant's bills are computed from specifications and refuse to be
    edited, so the dates have to come in through the specification or
    effectivity would be useless for every product it makes.
    """

    def spec(self, code, **dates):
        from .tests_rolls import RollTestCase

        helper = RollTestCase.specification
        return helper(self, code=code, fabric_item=self.product, **dates)

    def test_two_specifications_for_one_fabric_each_build_a_dated_recipe(self):
        old = self.spec("F-OLD", valid_to=OCT_31)
        new = self.spec("F-NEW", valid_from=NOV_1)
        self.assertEqual(old.bom.valid_to, OCT_31)
        self.assertEqual(new.bom.valid_from, NOV_1)
        self.assertEqual(default_bom_for(self.product, OCT_31), old.bom)
        self.assertEqual(default_bom_for(self.product, NOV_1), new.bom)

    def test_and_each_brings_its_own_limits_in_turn(self):
        """
        The layer below the recipe. Specifications build inspection
        plans too, and quality allowed one per item — so the second
        specification collided there once the recipe no longer did.
        """
        from apps.quality.release import plan_for

        old = self.spec("F-OLD", valid_to=OCT_31)
        new = self.spec("F-NEW", valid_from=NOV_1)
        self.assertEqual(plan_for(self.product, OCT_31), old.inspection_plan)
        self.assertEqual(plan_for(self.product, NOV_1), new.inspection_plan)

    def test_an_overlapping_specification_is_refused_when_saved(self):
        self.spec("F-OLD", valid_to=OCT_31)
        with self.assertRaisesMessage(ValidationError, "already the recipe"):
            self.spec("F-NEW", valid_from=OCT_31)


class PhantomTestCase(EffectivityTestCase):
    def setUp(self):
        super().setUp()
        self.blend = Item.objects.create(sku="BLEND", name="Blend", uom=self.kg)
        self.blend_bom = self.recipe(1, item=self.blend, is_phantom=True)
        self.line(self.blend_bom, self.virgin, "8", 1)
        self.line(self.blend_bom, self.colour, "2", 2)
        self.product_bom = self.recipe(1)
        self.line(self.product_bom, self.blend, "5", 1)
        self.line(self.product_bom, self.virgin, "5", 2)


class APhantomIsBlownThroughTests(PhantomTestCase):
    def test_what_a_run_draws_skips_the_phantom(self):
        rows = [(line.item.sku, qty) for line, qty in drawn(
            self.product_bom, Decimal("100"), self.kg
        )]
        self.assertEqual(rows, [
            ("PP-RAFFIA", Decimal("40")), ("MB-WHITE", Decimal("10")),
            ("PP-RAFFIA", Decimal("50")),
        ])

    def test_release_freezes_one_row_per_item(self):
        order = WorkOrder.objects.create(
            item=self.product, bom=self.product_bom,
            quantity_ordered=Decimal("100"), uom=self.kg, warehouse=self.plant,
        )
        order.release(TODAY)
        frozen = {row.item.sku: row.quantity_required
                  for row in order.components.all()}
        self.assertEqual(frozen, {
            "PP-RAFFIA": Decimal("90"), "MB-WHITE": Decimal("10"),
        })

    def test_the_plan_prices_it_through_its_materials(self):
        """
        The blend has no shelf and no standard, so pricing it as itself
        would refuse. Through its materials: 90 kg of virgin at the
        fixture's 100 and 10 of colour at 200 is 11,000.
        """
        plan = planned_cost(self.product_bom, Decimal("100"), self.plant, self.kg)
        self.assertEqual(plan.materials, Decimal("11000"))

    def test_a_stocked_sub_assembly_is_still_drawn_as_itself(self):
        self.blend_bom.is_phantom = False
        self.blend_bom.save()
        skus = [line.item.sku for line, _qty in drawn(
            self.product_bom, Decimal("100"), self.kg
        )]
        self.assertEqual(skus, ["BLEND", "PP-RAFFIA"])

    def test_a_loop_through_a_phantom_is_cut_not_followed(self):
        """
        The blend made partly of the product that consumes it: the walk
        must not go round for ever, and the product comes back as a
        row drawn from a shelf.
        """
        self.line(self.blend_bom, self.product, "1", 3)
        skus = [line.item.sku for line, _qty in drawn(
            self.product_bom, Decimal("100"), self.kg
        )]
        self.assertIn("PROD", skus)


class APhantomLoopEndsTests(PhantomTestCase):
    def test_two_phantoms_made_of_each_other_do_not_recurse_for_ever(self):
        """
        The case the guard is for. A phantom running into a stocked item
        stops there anyway — only phantoms are blown through — so the
        loop that matters is one made entirely of phantoms, which would
        otherwise recurse until the interpreter gave up.
        """
        other = Item.objects.create(sku="BLEND2", name="Blend 2", uom=self.kg)
        other_bom = self.recipe(1, item=other, is_phantom=True)
        self.line(other_bom, self.blend, "1", 1)
        self.line(self.blend_bom, other, "1", 3)
        skus = [line.item.sku for line, _qty in drawn(
            self.product_bom, Decimal("100"), self.kg
        )]
        # Round once, then the blend comes back as a row of its own.
        self.assertIn("BLEND", skus)


class APhantomIsNeverARunTests(PhantomTestCase):
    def test_it_is_never_released(self):
        order = WorkOrder.objects.create(
            item=self.blend, bom=self.blend_bom, quantity_ordered=Decimal("10"),
            uom=self.kg, warehouse=self.plant,
        )
        with self.assertRaisesMessage(ValidationError, "phantom"):
            order.release(TODAY)

    def test_it_has_no_machines_of_its_own(self):
        from .routing import Routing

        self.blend_bom.routing = Routing.objects.create(code="R-B", name="B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.blend_bom.save()

    def test_nor_is_it_a_rework(self):
        self.blend_bom.is_rework = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.blend_bom.save()

    def test_its_trim_belongs_to_the_run_that_made_it(self):
        with self.assertRaisesMessage(ValidationError, "phantom"):
            BomByproduct.objects.create(
                bom=self.blend_bom, item=self.regrind, quantity=Decimal("1"),
                uom=self.kg, valuation=ByproductValuation.STANDARD,
            )

    def test_a_recipe_with_trim_cannot_become_one(self):
        self.blend_bom.is_phantom = False
        self.blend_bom.save()
        BomByproduct.objects.create(
            bom=self.blend_bom, item=self.regrind, quantity=Decimal("1"),
            uom=self.kg, valuation=ByproductValuation.STANDARD,
        )
        self.blend_bom.is_phantom = True
        with self.assertRaisesMessage(ValidationError, "by-products"):
            self.blend_bom.save()
