"""
Rolls, and the conversion the unit-of-measure graph cannot do.

The arithmetic throughout: a tubular roll 600 mm lay-flat and 1,000 m
long has 0.6 x 1000 x 2 = 1,200 square metres of fabric in it, because
a tube laid flat is two thicknesses. At 87 GSM that is 104.4 kg, and
104.4 kg over 1,000 m is 9.578544 metres a kilo.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Lot, TrackingMode

from .rolls import (
    FabricRoll,
    metres_on_hand,
    rolls_at,
    unrolled_stock,
    weighed_gsm,
)
from .tests_orders import TODAY, RunTestCase


class RollTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.fabric = self.tape.__class__.objects.create(
            sku="FAB-60-87", name="Woven fabric, 60 cm, 87 GSM", uom=self.kg,
            tracking=TrackingMode.LOT,
        )

    def specification(self, target="87", tolerance="3", fabric_item=None,
                      code="F-60"):
        """A 10 x 10 mesh of 1,000-denier tape, 60 cm lay-flat tubular."""
        from .woven import FabricSpecification, TapeSpecification, Weave

        tape_item = self.tape.__class__.objects.create(
            sku=f"TAPE-FOR-{code}", name="Tape for the spec", uom=self.kg
        )
        tape = TapeSpecification.objects.create(
            code=f"T-{code}", tape_item=tape_item, denier=Decimal("1000"),
            tape_width_mm=Decimal("2.5"), virgin_granule=self.virgin,
            regrind_item=self.regrind, regrind_percent=Decimal("15"),
            filler_item=self.filler, filler_percent=Decimal("8"),
            masterbatch_item=self.colour, masterbatch_percent=Decimal("2"),
            extrusion_waste_percent=Decimal("3"),
            waste_recovered_percent=Decimal("80"),
        )
        return FabricSpecification.objects.create(
            code=code, fabric_item=fabric_item or self.fabric, warp_tape=tape,
            ends_per_inch=Decimal("10"), picks_per_inch=Decimal("10"),
            lay_flat_width_cm=Decimal("60"), weave=Weave.TUBULAR,
            target_gsm=Decimal(target),
            gsm_tolerance_percent=Decimal(tolerance),
            weaving_waste_percent=Decimal("2"),
        )

    def roll(self, code="R-1", length="1000", weight="104.400",
             width="600", tubular=True, spec=None, entry=None, stock=None):
        lot = Lot.objects.create(item=self.fabric, code=code)
        if stock is not None:
            self.stock(self.fabric, stock, "95", lot=lot)
        return FabricRoll.objects.create(
            lot=lot, width_mm=Decimal(width), length_m=Decimal(length),
            net_weight_kg=Decimal(weight), is_tubular=tubular,
            specification=spec, entry=entry,
        )


class WhatARollWeighsTests(RollTestCase):
    def test_a_tube_laid_flat_is_two_thicknesses(self):
        roll = self.roll()
        self.assertEqual(roll.layers(), Decimal("2"))
        # 0.6 m x 1000 m x 2 = 1,200 square metres.
        self.assertEqual(roll.area_sqm(), Decimal("1200.000"))

    def test_flat_cloth_is_one(self):
        roll = self.roll(tubular=False)
        self.assertEqual(roll.area_sqm(), Decimal("600.000"))

    def test_the_grammage_falls_out_of_the_scale_and_the_counter(self):
        """
        Measured rather than specified, and free: every roll is
        weighed and metered anyway, so this is a grammage reading on
        every roll the plant makes without a laboratory.
        """
        # 104.4 kg over 1,200 square metres is 87 GSM exactly.
        self.assertEqual(self.roll().implied_gsm(), Decimal("87.0000"))

    def test_getting_the_tube_wrong_doubles_the_grammage(self):
        """
        The mistake this flag exists to stop. Read as flat cloth, the
        same roll comes out at 174 GSM and every reading downstream is
        twice what it should be.
        """
        self.assertEqual(
            self.roll(tubular=False).implied_gsm(), Decimal("174.0000")
        )

    def test_a_heavy_roll_holds_fewer_metres(self):
        """
        The whole reason the conversion is the roll's own. The
        specification says 9.578544 metres a kilo; this roll came off
        five per cent heavy and gives 9.090909, and a cutting table
        handed the nominal figure runs short in the middle of a
        customer's order.
        """
        self.assertEqual(
            self.roll().metres_per_kg(), Decimal("9.578544")
        )
        self.assertEqual(
            self.roll(code="R-2", weight="110.000").metres_per_kg(),
            Decimal("9.090909"),
        )

    def test_converting_both_ways(self):
        roll = self.roll()
        self.assertEqual(roll.metres_for(Decimal("52.2")), Decimal("500.00"))
        self.assertEqual(roll.kilos_for(Decimal("500")), Decimal("52.200"))

    def test_a_core_is_not_fabric(self):
        roll = self.roll()
        roll.core_weight_kg = Decimal("2.500")
        roll.save()
        self.assertEqual(roll.gross_weight_kg(), Decimal("106.900"))
        # The grammage still reads off the net weight.
        self.assertEqual(roll.implied_gsm(), Decimal("87.0000"))

    def test_a_roll_with_no_length_is_refused(self):
        with self.assertRaises(Exception):
            self.roll(code="R-0", length="0")


class AgainstWhatItShouldHaveBeenTests(RollTestCase):
    def test_a_roll_on_target_is_within_tolerance(self):
        roll = self.roll(spec=self.specification())
        self.assertEqual(roll.gsm_deviation_percent(), Decimal("0"))
        self.assertTrue(roll.is_within_tolerance())

    def test_a_roll_five_per_cent_heavy_is_out(self):
        roll = self.roll(weight="110.000", spec=self.specification())
        # 91.6667 against 87 is 5.364% over, on a 3% tolerance.
        self.assertAlmostEqual(
            roll.gsm_deviation_percent(), Decimal("5.364"), places=3
        )
        self.assertFalse(roll.is_within_tolerance())

    def test_nothing_to_judge_it_against_is_not_a_pass(self):
        """
        The same rule the quality module follows: an uninspected batch
        is not a passed batch, and a roll with no specification has
        nothing to be heavy or light against.
        """
        roll = self.roll()
        self.assertIsNone(roll.gsm_deviation_percent())
        self.assertIsNone(roll.is_within_tolerance())

    def test_another_fabrics_specification_is_refused(self):
        """
        A roll read against the wrong specification is heavy or light
        against nothing.
        """
        other = self.tape.__class__.objects.create(
            sku="FAB-90-95", name="Wider fabric", uom=self.kg,
            tracking=TrackingMode.LOT,
        )
        spec = self.specification(fabric_item=other, code="F-90")
        lot = Lot.objects.create(item=self.fabric, code="R-X")
        with self.assertRaisesMessage(ValidationError, "heavy or light against nothing"):
            FabricRoll.objects.create(
                lot=lot, specification=spec, width_mm=Decimal("600"),
                length_m=Decimal("1000"), net_weight_kg=Decimal("104.400"),
            )


class WhatIsOnTheShelfInMetresTests(RollTestCase):
    def test_metres_are_summed_roll_by_roll_at_each_ones_own_rate(self):
        """
        The number the cutting table asks for. A nominal figure
        applied to the total gets it wrong the moment two rolls differ,
        which is always.
        """
        self.roll(code="R-1", weight="104.400", stock="104.400")
        self.roll(code="R-2", weight="110.000", stock="110.000")
        # 1,000 m on each, because each roll converts at its own rate.
        self.assertEqual(
            metres_on_hand(self.fabric, self.plant), Decimal("2000.00")
        )

    def test_a_part_used_roll_reports_what_is_left(self):
        """
        Read off the stock ledger, not off the roll. `length_m` is
        what came off the loom and does not change; what is left is a
        quantity of stock.
        """
        roll = self.roll(code="R-1", weight="104.400", stock="104.400")
        self.stock(self.fabric, "-52.200", "95", lot=roll.lot)
        self.assertEqual(
            metres_on_hand(self.fabric, self.plant), Decimal("500.00")
        )

    def test_fabric_with_no_roll_behind_it_is_reported_not_ignored(self):
        """
        It converts to no metres at all, so a cutting table reading
        the total is being told about less fabric than the plant owns
        — and silently, which is the worst way to be short.
        """
        self.roll(code="R-1", weight="104.400", stock="104.400")
        loose = Lot.objects.create(item=self.fabric, code="LOOSE")
        self.stock(self.fabric, "200", "95", lot=loose)
        self.assertEqual(
            metres_on_hand(self.fabric, self.plant), Decimal("1000.00")
        )
        self.assertEqual(
            unrolled_stock(self.fabric, self.plant), Decimal("200")
        )

    def test_an_empty_roll_is_not_listed(self):
        roll = self.roll(code="R-1", weight="104.400", stock="104.400")
        self.stock(self.fabric, "-104.400", "95", lot=roll.lot)
        self.assertEqual(rolls_at(self.fabric, self.plant), [])


class ARollBelongsToItsBookingTests(RollTestCase):
    def fabric_run(self, quantity="104.400", lot=None):
        """A short fabric run, booked to a batch because it is lot-tracked."""
        from .bom import BillOfMaterials, BomComponent
        from .orders import ProductionEntry, WorkOrder

        bom = BillOfMaterials.objects.create(
            item=self.fabric, name="Fabric", quantity_produced=Decimal("100"),
            uom=self.kg,
        )
        BomComponent.objects.create(
            bom=bom, item=self.tape, quantity=Decimal("100"), uom=self.kg,
            line_number=1,
        )
        self.stock(self.tape, "500", "120")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=bom, quantity_ordered=Decimal("104.400"),
            uom=self.kg, warehouse=self.plant,
        )
        order.release(TODAY)
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal(quantity), uom=self.kg, lot=lot,
        )
        entry.post()
        return entry

    def test_a_roll_may_be_booked_against_the_entry_that_wound_it(self):
        lot = Lot.objects.create(item=self.fabric, code="R-RUN")
        entry = self.fabric_run(lot=lot)
        roll = FabricRoll.objects.create(
            lot=lot, entry=entry, width_mm=Decimal("600"),
            length_m=Decimal("1000"), net_weight_kg=Decimal("104.400"),
        )
        self.assertEqual(roll.implied_gsm(), Decimal("87.0000"))

    def test_a_roll_naming_another_batch_is_refused(self):
        lot = Lot.objects.create(item=self.fabric, code="R-RUN")
        other = Lot.objects.create(item=self.fabric, code="R-OTHER")
        entry = self.fabric_run(lot=lot)
        with self.assertRaisesMessage(ValidationError, "points at the wrong run"):
            FabricRoll.objects.create(
                lot=other, entry=entry, width_mm=Decimal("600"),
                length_m=Decimal("1000"), net_weight_kg=Decimal("104.400"),
            )

    def test_a_roll_that_does_not_weigh_what_was_booked_is_refused(self):
        """
        The shelf and the scale have to agree at the moment the roll
        is made; they will not get closer later.
        """
        lot = Lot.objects.create(item=self.fabric, code="R-RUN")
        entry = self.fabric_run(lot=lot)
        with self.assertRaisesMessage(ValidationError, "have to agree"):
            FabricRoll.objects.create(
                lot=lot, entry=entry, width_mm=Decimal("600"),
                length_m=Decimal("1000"), net_weight_kg=Decimal("99.000"),
            )

    def test_a_roll_with_no_booking_is_left_alone(self):
        roll = self.roll()
        self.assertIsNone(roll.entry)


class WeighingIsAMeasurementTests(RollTestCase):
    def test_a_run_reads_its_own_grammage_off_its_rolls(self):
        """
        The measurement that needs no inspector. Every roll is weighed
        and metered, so a run that ate more polymer than the
        specification said can be read against the grammage of its own
        output whether or not a sample ever reached a laboratory.
        """
        from .orders import ProductionEntry, WorkOrder

        spec = self.specification()
        # Run against the specification's own computed bill of
        # materials, which is how the plant actually works.
        self.stock(spec.warp_tape.tape_item, "500", "120")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=spec.bom,
            quantity_ordered=Decimal("104.400"), uom=self.kg,
            warehouse=self.plant,
        )
        order.release(TODAY)
        lot = Lot.objects.create(item=self.fabric, code="R-RUN")
        entry = ProductionEntry.objects.create(
            work_order=order, entry_date=TODAY, warehouse=self.plant,
            quantity_produced=Decimal("104.400"), uom=self.kg, lot=lot,
        )
        entry.post()
        FabricRoll.objects.create(
            lot=lot, entry=entry, specification=spec, width_mm=Decimal("600"),
            length_m=Decimal("1000"), net_weight_kg=Decimal("104.400"),
        )
        reading = weighed_gsm(order)
        self.assertEqual(reading["measured"], Decimal("87.0000"))
        self.assertEqual(reading["target"], Decimal("87"))
        self.assertEqual(reading["rolls"], 1)

    def test_it_weights_by_area_not_by_roll(self):
        """
        A four-metre remnant and a two-thousand-metre roll are not one
        reading each.
        """
        from .orders import ProductionEntry, WorkOrder

        spec = self.specification()
        self.stock(spec.warp_tape.tape_item, "5000", "120")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=spec.bom, quantity_ordered=Decimal("1000"),
            uom=self.kg, warehouse=self.plant,
        )
        order.release(TODAY)
        for code, length, weight in (
            ("BIG", "2000", "208.800"), ("REMNANT", "4", "0.480")
        ):
            lot = Lot.objects.create(item=self.fabric, code=code)
            entry = ProductionEntry.objects.create(
                work_order=order, entry_date=TODAY, warehouse=self.plant,
                quantity_produced=Decimal(weight), uom=self.kg, lot=lot,
            )
            entry.post()
            FabricRoll.objects.create(
                lot=lot, entry=entry, specification=spec,
                width_mm=Decimal("600"), length_m=Decimal(length),
                net_weight_kg=Decimal(weight),
            )
        # The big roll is on target at 87 and the remnant is 100. By
        # area that is (208.8 + 0.48) x 1000 / (2400 + 4.8) = 87.0259
        # — barely moved, because the remnant is a five-hundredth of
        # the area. Averaged per roll it would read 93.5, which is a
        # figure about nothing.
        reading = weighed_gsm(order)
        self.assertEqual(reading["rolls"], 2)
        self.assertEqual(reading["measured"], Decimal("87.0259"))

    def test_a_run_with_no_rolls_says_nothing(self):
        from .orders import ProductionEntry, WorkOrder

        spec = self.specification()
        self.stock(spec.warp_tape.tape_item, "500", "120")
        order = WorkOrder.objects.create(
            item=self.fabric, bom=spec.bom,
            quantity_ordered=Decimal("104.400"), uom=self.kg,
            warehouse=self.plant,
        )
        order.release(TODAY)
        self.assertIsNone(weighed_gsm(order))
