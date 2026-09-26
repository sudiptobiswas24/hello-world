"""
The arithmetic, checked against numbers worked out by hand.

Every figure asserted here was reconstructed from the specification
rather than read off the code's output, because this project has twice
adjusted an assertion to match a wrong answer and once adjusted correct
code to match wrong arithmetic. Where a number is not obvious the
working is in the comment above it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import UnitOfMeasure, UnitOfMeasureCategory
from apps.inventory.models import Item

from .bom import (
    BillOfMaterials,
    BomComponent,
    default_bom_for,
    explode,
    material_balance,
    net_requirements,
)
from .woven import BagSpecification, FabricSpecification, TapeSpecification, Weave


def close(value, expected, places="0.0001"):
    return Decimal(value).quantize(Decimal(places)) == Decimal(expected).quantize(
        Decimal(places)
    )


class WovenTestCase(TestCase):
    def setUp(self):
        self.kg = UnitOfMeasure.objects.create(
            code="kg", name="Kilogram", category=UnitOfMeasureCategory.WEIGHT
        )
        self.pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        weigh = lambda sku, name: Item.objects.create(
            sku=sku, name=name, uom=self.kg
        )
        self.virgin = weigh("PP-RAFFIA", "PP homopolymer, raffia grade")
        self.regrind = weigh("REGRIND", "Reprocessed plant waste")
        self.filler = weigh("CACO3", "Calcium carbonate masterbatch")
        self.colour = weigh("MB-WHITE", "White masterbatch")
        self.tape_item = weigh("TAPE-1000", "PP tape, 1000 denier")
        self.fabric_item = weigh("FAB-60-87", "Woven fabric, 60 cm, 87 GSM")
        self.thread = weigh("THREAD", "Sewing thread")
        self.bag_item = Item.objects.create(
            sku="BAG-60X100", name="Woven sack 60 x 100 cm", uom=self.pcs
        )

    def tape(self, **overrides):
        values = dict(
            code="T1000", tape_item=self.tape_item, denier=Decimal("1000"),
            tape_width_mm=Decimal("2.5"), virgin_granule=self.virgin,
            regrind_item=self.regrind, regrind_percent=Decimal("15"),
            filler_item=self.filler, filler_percent=Decimal("8"),
            masterbatch_item=self.colour, masterbatch_percent=Decimal("2"),
            extrusion_waste_percent=Decimal("3"),
            waste_recovered_percent=Decimal("80"),
        )
        values.update(overrides)
        return TapeSpecification.objects.create(**values)

    def fabric(self, tape=None, **overrides):
        values = dict(
            code="F87", fabric_item=self.fabric_item, warp_tape=tape or self.tape(),
            ends_per_inch=Decimal("10"), picks_per_inch=Decimal("10"),
            lay_flat_width_cm=Decimal("60"), weave=Weave.TUBULAR,
            target_gsm=Decimal("87.5"), gsm_tolerance_percent=Decimal("5"),
            weaving_waste_percent=Decimal("2"),
            waste_recovered_percent=Decimal("85"),
            loom_waste_item=self.regrind,
        )
        values.update(overrides)
        return FabricSpecification.objects.create(**values)

    def bag(self, fabric=None, **overrides):
        values = dict(
            code="B60X100", bag_item=self.bag_item, fabric=fabric or self.fabric(),
            bag_width_cm=Decimal("60"), bag_length_cm=Decimal("100"),
            bottom_hem_cm=Decimal("3"), top_hem_cm=Decimal("2"),
            thread_item=self.thread, thread_grams_per_bag=Decimal("1.2"),
            conversion_waste_percent=Decimal("2.5"),
            waste_recovered_percent=Decimal("70"),
            cutting_waste_item=self.regrind,
        )
        values.update(overrides)
        return BagSpecification.objects.create(**values)

    def component(self, bom, item):
        return bom.components.get(item=item)


class TheArithmeticTests(WovenTestCase):
    def test_denier_is_grammes_per_nine_thousand_metres(self):
        # 1,000 denier: 9,000 m weighs 1,000 g, so a kilo runs 9,000 m.
        tape = self.tape()
        self.assertTrue(close(tape.grams_per_metre(), "0.111111", "0.000001"))
        self.assertTrue(close(tape.metres_per_kg(), "9000"))

    def test_the_virgin_share_is_what_the_additives_leave(self):
        tape = self.tape()
        self.assertEqual(tape.additive_percent(), Decimal("25"))
        self.assertEqual(tape.virgin_percent(), Decimal("75"))

    def test_gsm_comes_from_the_mesh_and_the_denier(self):
        # (10 + 10) tapes per inch x 39.3701 inches per metre x 1000
        # denier / 9000 = 87.4891 g per square metre. A 10 x 10 loom
        # makes 87.5 GSM, not the round 80 a quotation tends to carry.
        fabric = self.fabric()
        self.assertTrue(close(fabric.gsm(), "87.48911"))
        self.assertTrue(close(fabric.warp_share(), "0.5"))

    def test_a_tube_laid_flat_is_two_thicknesses(self):
        fabric = self.fabric()
        self.assertEqual(fabric.layers(), Decimal("2"))
        # 87.48911 x 0.60 m x 2 = 104.98693 g per running metre.
        self.assertTrue(close(fabric.grams_per_metre(), "104.986932"))
        self.assertTrue(close(fabric.metres_per_kg(), "9.525"))

    def test_flat_fabric_is_one(self):
        fabric = self.fabric(code="F-FLAT", weave=Weave.FLAT)
        self.assertEqual(fabric.layers(), Decimal("1"))
        self.assertTrue(close(fabric.grams_per_metre(), "52.493466"))

    def test_the_hems_are_fabric_somebody_paid_for(self):
        bag = self.bag()
        self.assertEqual(bag.cut_length_cm(), Decimal("105"))
        # 2 layers x 0.60 m x 1.05 m = 1.26 square metres of fabric.
        self.assertTrue(close(bag.fabric_area_sqm(), "1.26"))
        # 1.26 x 87.48911 = 110.23628 g of fabric in one sack.
        self.assertTrue(close(bag.fabric_grams(), "110.236279"))
        self.assertTrue(close(bag.bag_grams(), "111.436279"))

    def test_dropping_the_hems_understates_the_sack_by_five_per_cent(self):
        # The comparison that makes the point: a costing built on the
        # finished length rather than the cut length is short by the
        # hems, which on this sack is 4.8% of the fabric.
        bag = self.bag()
        with_hems = bag.fabric_grams()
        bag.bottom_hem_cm = Decimal("0")
        bag.top_hem_cm = Decimal("0")
        bag.save()
        self.assertTrue(close(bag.fabric_grams(), "104.986932"))
        shortfall = (with_hems - bag.fabric_grams()) / with_hems * Decimal("100")
        self.assertTrue(close(shortfall, "4.7619", "0.001"))

    def test_lamination_covers_the_same_area_as_the_fabric(self):
        bag = self.bag(
            is_laminated=True, lamination_gsm=Decimal("15"),
            lamination_item=self.virgin,
        )
        # 1.26 square metres at 15 GSM.
        self.assertTrue(close(bag.lamination_grams(), "18.9"))
        self.assertTrue(close(bag.bag_grams(), "130.336279"))

    def test_ink_is_per_colour_per_printed_face(self):
        bag = self.bag(
            print_colours=2, printed_faces=2, ink_item=self.colour,
            ink_grams_per_sqm_per_colour=Decimal("3"),
        )
        # Printing covers the sack's face, not the hems: 2 x 0.60 x 1.00
        # = 1.2 square metres, x 3 g x 2 colours = 7.2 g.
        self.assertTrue(close(bag.printed_area_sqm(), "1.2"))
        self.assertTrue(close(bag.ink_grams(), "7.2"))


class WasteIsTakenOnTheInputTests(WovenTestCase):
    def test_gross_is_net_over_one_minus_waste(self):
        tape = self.tape()
        bom = tape.bom
        virgin = self.component(bom, self.virgin)
        # 75 kg of virgin in a 100 kg batch, 3% of the input lost:
        # 75 / 0.97 = 77.319588, and the whole batch grosses to 100/0.97.
        self.assertEqual(virgin.quantity, Decimal("75"))
        self.assertTrue(close(virgin.gross_quantity(), "77.319588"))
        total = sum(c.gross_quantity() for c in bom.components.all())
        self.assertTrue(close(total, "103.092784"))

    def test_the_other_reading_of_the_percentage_is_wrong_and_by_how_much(self):
        # net x (1 + w) is the reading a lot of systems take. Over the
        # four stages a printed laminated sack passes through, 3% read
        # each way differs by nearly half a per cent of the polymer bill.
        waste = Decimal("0.03")
        on_input = (Decimal("1") / (Decimal("1") - waste)) ** 4
        on_output = (Decimal("1") + waste) ** 4
        self.assertTrue(close(on_input, "1.129570", "0.000001"))
        self.assertTrue(close(on_output, "1.125509", "0.000001"))
        self.assertGreater(on_input - on_output, Decimal("0.004"))

    def test_only_the_collected_part_of_the_loss_is_a_by_product(self):
        tape = self.tape()
        # Losing 3% of the input to make 100 kg means losing
        # 100 x 0.03/0.97 = 3.092784 kg, of which 80% is swept up and
        # reground: 2.474227 kg. The rest is dust and burn-off, and
        # booking it as regrind grows a yard balance nobody can find.
        byproduct = tape.bom.byproducts.get(item=self.regrind)
        self.assertTrue(close(byproduct.quantity, "2.474227"))

    def test_a_stage_with_no_waste_grosses_to_itself(self):
        tape = self.tape(extrusion_waste_percent=Decimal("0"))
        virgin = self.component(tape.bom, self.virgin)
        self.assertEqual(virgin.gross_quantity(), Decimal("75"))
        self.assertFalse(tape.bom.byproducts.exists())


class TheSpecificationComputesTheBomTests(WovenTestCase):
    def test_saving_a_specification_builds_its_bom(self):
        tape = self.tape()
        self.assertIsNotNone(tape.bom)
        self.assertTrue(tape.bom.is_computed)
        self.assertEqual(tape.bom.item, self.tape_item)
        self.assertEqual(tape.bom.quantity_produced, Decimal("100"))
        self.assertEqual(
            {c.item_id for c in tape.bom.components.all()},
            {self.virgin.pk, self.regrind.pk, self.filler.pk, self.colour.pk},
        )

    def test_changing_the_specification_changes_the_bom(self):
        tape = self.tape()
        tape.filler_percent = Decimal("12")
        tape.save()
        self.assertEqual(
            self.component(tape.bom, self.filler).quantity, Decimal("12")
        )
        # The virgin share is derived, so it moved on its own.
        self.assertEqual(
            self.component(tape.bom, self.virgin).quantity, Decimal("71")
        )

    def test_a_material_the_product_stopped_using_disappears(self):
        # A rebuild that diffs instead of replacing is how a BOM ends up
        # carrying a masterbatch the sack stopped using two years ago.
        tape = self.tape()
        self.assertTrue(tape.bom.components.filter(item=self.colour).exists())
        tape.masterbatch_percent = Decimal("0")
        tape.save()
        self.assertFalse(tape.bom.components.filter(item=self.colour).exists())

    def test_a_computed_bom_refuses_to_be_edited_by_hand(self):
        tape = self.tape()
        bom = tape.bom
        bom.quantity_produced = Decimal("250")
        with self.assertRaises(ValidationError):
            bom.save()
        bom.refresh_from_db()
        self.assertEqual(bom.quantity_produced, Decimal("100"))

    def test_nor_may_a_component_of_one_be(self):
        tape = self.tape()
        virgin = self.component(tape.bom, self.virgin)
        virgin.quantity = Decimal("60")
        with self.assertRaises(ValidationError):
            virgin.save()
        with self.assertRaises(ValidationError):
            BomComponent.objects.create(
                bom=tape.bom, item=self.thread, quantity=Decimal("1"),
                uom=self.kg,
            )

    def test_a_bom_that_nothing_computes_is_editable(self):
        bom = BillOfMaterials.objects.create(
            item=self.bag_item, quantity_produced=Decimal("1000"), uom=self.pcs,
        )
        BomComponent.objects.create(
            bom=bom, item=self.fabric_item, quantity=Decimal("110"), uom=self.kg
        )
        bom.name = "Typed by hand"
        bom.save()
        self.assertEqual(bom.components.count(), 1)

    def test_the_bom_says_what_computes_it(self):
        tape = self.tape()
        self.assertEqual(tape.bom.computed_by(), tape)


class TheWholeChainExplodesTests(WovenTestCase):
    def setUp(self):
        super().setUp()
        self.tape_spec = self.tape()
        self.fabric_spec = self.fabric(tape=self.tape_spec)
        self.bag_spec = self.bag(fabric=self.fabric_spec)

    def test_ten_thousand_sacks_reach_the_polymer(self):
        rows = explode(self.bag_spec.bom, Decimal("10000"), self.pcs)
        by_item = {}
        for row in rows:
            if not row.is_byproduct:
                by_item[row.item.sku] = by_item.get(row.item.sku, Decimal("0")) + row.quantity

        # Fabric: 110.236279 g a sack, 2.5% lost in conversion, ten
        # thousand sacks. 110.236279 / 0.975 x 10 = 1130.6285 kg.
        self.assertTrue(close(by_item["FAB-60-87"], "1130.628502", "0.01"))
        # Tape: that fabric with 2% lost on the loom.
        self.assertTrue(close(by_item["TAPE-1000"], "1153.702553", "0.01"))
        # Virgin: 75% of the blend, 3% lost in extrusion.
        self.assertTrue(close(by_item["PP-RAFFIA"], "892.038065", "0.01"))
        self.assertTrue(close(by_item["CACO3"], "95.150727", "0.01"))
        self.assertTrue(close(by_item["MB-WHITE"], "23.787682", "0.01"))
        self.assertTrue(close(by_item["THREAD"], "12.307692", "0.01"))

    def test_the_shopping_list_holds_only_what_is_bought(self):
        # Fabric and tape are made here. Listing them beside the polymer
        # they are made from would count the polymer twice.
        bought = {item.sku for item, _quantity, _uom in
                  net_requirements(self.bag_spec.bom, Decimal("10000"), self.pcs)}
        self.assertEqual(
            bought, {"PP-RAFFIA", "REGRIND", "CACO3", "MB-WHITE", "THREAD"}
        )

    def test_the_sack_weighs_what_the_fabric_in_it_weighs(self):
        # The cross-check a weighbridge does: ten thousand sacks at
        # 111.436 g is 1114.36 kg, and it had better be the fabric and
        # thread that went in, less what was thrown away.
        made = self.bag_spec.bag_grams() * Decimal("10000") / Decimal("1000")
        self.assertTrue(close(made, "1114.362790", "0.01"))


class TheGraphIsCyclicTests(WovenTestCase):
    """
    Tape consumes regrind and produces regrind, and in a plant that
    reprocesses its own waste properly the regrind has a BOM of its own.
    A walk that does not remember where it has been does not come back.
    """

    def setUp(self):
        super().setUp()
        self.tape_spec = self.tape()
        self.fabric_spec = self.fabric(tape=self.tape_spec)
        self.bag_spec = self.bag(fabric=self.fabric_spec)

    def test_regrind_consumed_by_the_process_that_makes_it(self):
        rows = explode(self.bag_spec.bom, Decimal("1000"), self.pcs)
        regrind = [r for r in rows if r.item.pk == self.regrind.pk]
        self.assertTrue(any(r.is_byproduct for r in regrind))
        self.assertTrue(any(not r.is_byproduct for r in regrind))
        # Consumed regrind is a leaf: it comes off a shelf, it is not a
        # sub-assembly to be taken apart.
        self.assertTrue(all(r.is_leaf for r in regrind if not r.is_byproduct))

    def test_a_reprocessing_bom_does_not_send_the_walk_round_forever(self):
        # Give regrind a BOM that consumes the plant's own fabric waste,
        # which is made of tape, which is made of regrind.
        loop = BillOfMaterials.objects.create(
            item=self.regrind, quantity_produced=Decimal("100"), uom=self.kg,
        )
        BomComponent.objects.create(
            bom=loop, item=self.fabric_item, quantity=Decimal("100"), uom=self.kg
        )
        rows = explode(self.bag_spec.bom, Decimal("1000"), self.pcs)
        self.assertLess(len(rows), 60)
        self.assertTrue(any(r.item.pk == self.virgin.pk for r in rows))

    def test_the_balance_shows_the_regrind_a_plant_has_to_buy(self):
        # A blend calling for 15% reprocessed material against processes
        # that recover a few per cent of throughput is a plant that buys
        # scrap from somebody, and no conventional BOM says so.
        balance = {
            item.sku: (required, produced, net)
            for item, required, produced, net
            in material_balance(self.bag_spec.bom, Decimal("10000"), self.pcs)
        }
        required, produced, net = balance["REGRIND"]
        self.assertGreater(required, produced)
        self.assertGreater(net, Decimal("100"))
        self.assertTrue(close(required, "178.407613", "0.01"))
        # Extrusion, loom and cutting all give some back.
        self.assertTrue(close(produced, "67.944161", "0.01"))


class ASpecificationThatCannotBeMadeIsRefusedTests(WovenTestCase):
    def test_a_mesh_that_cannot_reach_the_quoted_gsm(self):
        with self.assertRaises(ValidationError) as caught:
            self.fabric(
                code="F-WRONG", ends_per_inch=Decimal("8"),
                picks_per_inch=Decimal("8"), target_gsm=Decimal("87.5"),
            )
        self.assertIn("70.0 GSM", str(caught.exception))

    def test_a_tube_woven_to_the_wrong_width(self):
        fabric = self.fabric(lay_flat_width_cm=Decimal("55"))
        with self.assertRaises(ValidationError) as caught:
            self.bag(fabric=fabric)
        self.assertIn("one of the two is wrong", str(caught.exception))

    def test_a_sack_cut_from_flat_fabric(self):
        fabric = self.fabric(code="F-FLAT", weave=Weave.FLAT)
        with self.assertRaises(ValidationError):
            self.bag(fabric=fabric)

    def test_a_blend_with_no_polymer_left_in_it(self):
        with self.assertRaises(ValidationError):
            self.tape(code="T-BAD", filler_percent=Decimal("90"),
                      regrind_percent=Decimal("15"))

    def test_a_percentage_with_no_material_named(self):
        with self.assertRaises(ValidationError) as caught:
            self.tape(code="T-NONAME", filler_item=None,
                      filler_percent=Decimal("8"))
        self.assertIn("which one", str(caught.exception))

    def test_a_laminated_sack_with_nothing_to_laminate_it_with(self):
        with self.assertRaises(ValidationError):
            self.bag(is_laminated=True, lamination_gsm=Decimal("15"))

    def test_a_refused_specification_leaves_no_half_built_bom(self):
        # The spec and the BOM it computes land together or not at all.
        before = BillOfMaterials.objects.count()
        with self.assertRaises(ValidationError):
            self.tape(code="T-BAD", filler_percent=Decimal("99"))
        self.assertEqual(BillOfMaterials.objects.count(), before)
        self.assertFalse(TapeSpecification.objects.filter(code="T-BAD").exists())


class OneDefaultBomPerItemTests(WovenTestCase):
    def test_a_second_default_is_refused(self):
        from django.db import IntegrityError, transaction

        BillOfMaterials.objects.create(
            item=self.bag_item, quantity_produced=Decimal("1000"), uom=self.pcs,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillOfMaterials.objects.create(
                    item=self.bag_item, version=2,
                    quantity_produced=Decimal("500"), uom=self.pcs,
                )

    def test_an_explosion_picks_the_default(self):
        bom = BillOfMaterials.objects.create(
            item=self.bag_item, quantity_produced=Decimal("1000"), uom=self.pcs,
        )
        self.assertEqual(default_bom_for(self.bag_item), bom)
        BillOfMaterials.objects.create(
            item=self.bag_item, version=2, is_default=False,
            quantity_produced=Decimal("500"), uom=self.pcs,
        )
        self.assertEqual(default_bom_for(self.bag_item), bom)


class TheUnitTheArithmeticIsWrittenInTests(WovenTestCase):
    """
    Grammes per square metre become kilogrammes per batch, and that step
    is true of the kilogramme and of nothing else. Every case here was
    found by probing rather than by a failing test, and every one of
    them produced a plausible-looking BOM that was wrong.
    """

    def test_fabric_stocked_in_metres(self):
        # "100 m of fabric needs 100 kg of tape" — a ratio between a
        # length and a weight, which is not a ratio.
        metre = UnitOfMeasure.objects.create(
            code="m", name="Metre", category=UnitOfMeasureCategory.LENGTH
        )
        cloth = Item.objects.create(sku="FAB-M", name="Fabric by the metre", uom=metre)
        with self.assertRaises(ValidationError) as caught:
            self.fabric(code="F-M", fabric_item=cloth)
        self.assertIn("cannot be written against a length", str(caught.exception))

    def test_tape_stocked_in_tonnes(self):
        # A weight against a weight, and wrong by a factor of a thousand:
        # "100 t of tape needs 75 kg of virgin polymer".
        tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        big = Item.objects.create(sku="TAPE-T", name="Tape by the tonne", uom=tonne)
        with self.assertRaises(ValidationError) as caught:
            self.tape(code="T-T", tape_item=big)
        self.assertIn("base weight unit", str(caught.exception))

    def test_a_blend_material_in_a_different_unit_from_the_tape(self):
        gram = UnitOfMeasure.objects.create(
            code="g", name="Gram", category=UnitOfMeasureCategory.WEIGHT
        )
        dust = Item.objects.create(sku="MB-G", name="Masterbatch by the gram", uom=gram)
        with self.assertRaises(ValidationError) as caught:
            self.tape(code="T-MIX", masterbatch_item=dust)
        self.assertIn("One chain, one unit", str(caught.exception))

    def test_sacks_stocked_by_weight(self):
        # A BOM written per thousand pieces against an item stocked by
        # weight reads as a thousand kilogrammes of sacks.
        heavy = Item.objects.create(sku="BAG-KG", name="Sacks by weight", uom=self.kg)
        with self.assertRaises(ValidationError) as caught:
            self.bag(code="B-KG", bag_item=heavy)
        self.assertIn("sacks are counted", str(caught.exception))

    def test_the_chain_in_one_consistent_unit_is_accepted(self):
        # The guard has to let the ordinary case through, or it is only
        # a refusal.
        bag = self.bag()
        self.assertEqual(bag.bom.components.get(item=self.fabric_item).uom, self.kg)


class ASpecificationCannotInventMaterialTests(WovenTestCase):
    def test_recovering_more_waste_than_was_lost(self):
        # Losing 3.09 kg and recovering 12.37 of it is regrind made out
        # of nothing, and the yard never sees it.
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.tape(
                    code="T-OVER", waste_recovered_percent=Decimal("400")
                )

    def test_a_target_gsm_of_zero_would_accept_any_mesh_at_all(self):
        # Nothing is within five per cent of zero, so the tolerance check
        # passes whatever the loom is set to.
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.fabric(code="F-ZERO", target_gsm=Decimal("0"))


class DeletingASpecificationReleasesItsBomTests(WovenTestCase):
    """
    The reverse path, written in the same sitting as the forward one.
    Deleting the specification and leaving its BOM marked computed
    leaves master data nobody can touch: nothing rebuilds it, because
    what rebuilt it is gone, and nothing may edit it, because it says it
    is computed.
    """

    def test_the_bom_becomes_an_ordinary_one(self):
        tape = self.tape()
        bom = tape.bom
        tape.delete()
        bom.refresh_from_db()
        self.assertFalse(bom.is_computed)
        self.assertIsNone(bom.computed_by())
        bom.name = "Kept, and now editable"
        bom.save()
        self.assertEqual(
            BillOfMaterials.objects.get(pk=bom.pk).name, "Kept, and now editable"
        )

    def test_its_components_survive_with_it(self):
        tape = self.tape()
        bom = tape.bom
        before = bom.components.count()
        tape.delete()
        self.assertEqual(bom.components.count(), before)


class TheSpecificationCarriesTheRoutingInTests(WovenTestCase):
    """
    A computed bill of materials refuses to be edited, so the routing
    cannot be typed onto it. It comes in the way the components do:
    named on the specification and worked out from there.
    """

    def setUp(self):
        super().setUp()
        from .orders import WorkCentre
        from .routing import Routing, RoutingOperation

        self.line = WorkCentre.objects.create(code="EXT-1", name="Extrusion 1")
        self.plan = Routing.objects.create(code="R-EXT", name="Extrude")
        RoutingOperation.objects.create(
            routing=self.plan, sequence=10, name="Extrude",
            work_centre=self.line, setup_minutes=Decimal("90"),
            units_per_hour=Decimal("180"), rate_uom=self.kg,
        )

    def test_it_lands_on_the_computed_bom(self):
        tape = self.tape(routing=self.plan)
        self.assertEqual(tape.bom.routing, self.plan)

    def test_and_moves_when_the_specification_does(self):
        from .routing import Routing

        tape = self.tape(routing=self.plan)
        faster = Routing.objects.create(code="R-EXT2", name="Extrude, new line")
        tape.routing = faster
        tape.save()
        self.assertEqual(tape.bom.routing, faster)

    def test_and_can_be_taken_off_again(self):
        tape = self.tape(routing=self.plan)
        tape.routing = None
        tape.save()
        self.assertIsNone(tape.bom.routing)

    def test_the_bom_still_refuses_a_hand_edit(self):
        tape = self.tape(routing=self.plan)
        bom = tape.bom
        bom.routing = None
        with self.assertRaises(ValidationError):
            bom.save()
