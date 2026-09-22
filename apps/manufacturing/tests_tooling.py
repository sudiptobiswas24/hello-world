"""
Cylinders, dies and the life left in them.

A cylinder is engraved for one customer's artwork, prints a finite
number of bags and then has to be re-chromed. A plant that loses track
finds out in the middle of a fifty-thousand-sack run.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import (
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)

from .tooling import (
    PrintDesign,
    Tool,
    ToolKind,
    ToolStatus,
    ToolUsage,
    tools_for,
    wearing_out,
)
from .tests_orders import TODAY, RunTestCase


class ToolingTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.pcs = UnitOfMeasure.objects.create(
            code="pcs", name="Pieces", category=UnitOfMeasureCategory.COUNT
        )
        self.customer = Party.objects.create(code="C-UCL", name="Ultratech")
        PartyRoleAssignment.objects.create(
            party=self.customer, role=PartyRole.CUSTOMER
        )
        self.design = PrintDesign.objects.create(
            code="UCL-50", name="Ultratech 50 kg, 2 colour",
            customer=self.customer, colours=2, approved_on=TODAY,
        )

    def bag_order(self, quantity="1000"):
        """
        A sack run, counted in pieces — which is what a cylinder's
        life is counted in.
        """
        from .bom import BillOfMaterials, BomComponent
        from .orders import WorkOrder

        if not hasattr(self, "bag"):
            self.bag = self.tape.__class__.objects.create(
                sku="BAG-60X100", name="Woven sack", uom=self.pcs
            )
            self.bag_bom = BillOfMaterials.objects.create(
                item=self.bag, name="Sack", quantity_produced=Decimal("1000"),
                uom=self.pcs,
            )
            BomComponent.objects.create(
                bom=self.bag_bom, item=self.tape, quantity=Decimal("110"),
                uom=self.kg, line_number=1,
            )
            self.stock(self.tape, "50000", "120")
        return WorkOrder.objects.create(
            item=self.bag, bom=self.bag_bom, quantity_ordered=Decimal(quantity),
            uom=self.pcs, warehouse=self.plant,
        )

    def cylinder(self, code="CYL-1", limit="500000", status=ToolStatus.AVAILABLE,
                 design=None):
        return Tool.objects.create(
            code=code, name=f"Cylinder {code}", kind=ToolKind.CYLINDER,
            design=design if design is not None else self.design,
            life_limit=Decimal(limit) if limit else None,
            life_uom=self.pcs if limit else None, status=status,
        )


class WhatCarriesArtworkTests(ToolingTestCase):
    def test_a_cylinder_with_no_design_is_refused(self):
        """
        A cylinder nobody can match to a customer's artwork is a
        cylinder nobody finds when that customer orders.
        """
        with self.assertRaisesMessage(ValidationError, "carries no design"):
            Tool.objects.create(
                code="CYL-X", name="Orphan", kind=ToolKind.CYLINDER,
                life_limit=Decimal("500000"), life_uom=self.pcs,
            )

    def test_only_a_cylinder_carries_artwork(self):
        with self.assertRaisesMessage(ValidationError, "Only a cylinder"):
            Tool.objects.create(
                code="DIE-X", name="Die", kind=ToolKind.DIE,
                design=self.design,
            )

    def test_a_die_needs_no_design(self):
        die = Tool.objects.create(
            code="DIE-1", name="Bottom cut", kind=ToolKind.DIE,
            life_limit=Decimal("2000000"), life_uom=self.pcs,
        )
        self.assertTrue(die.is_usable())

    def test_a_full_set_is_one_cylinder_a_colour(self):
        """
        A four-colour design with three usable cylinders cannot be
        printed at all, and a plant that finds that out at the press
        has already changed over.
        """
        self.cylinder("CYL-1")
        report = self.design.cylinder_set()
        self.assertFalse(report["complete"])
        self.assertEqual(report["short_by"], 1)
        self.cylinder("CYL-2")
        self.assertTrue(self.design.cylinder_set()["complete"])

    def test_a_cylinder_away_for_service_is_not_part_of_the_set(self):
        self.cylinder("CYL-1")
        self.cylinder("CYL-2", status=ToolStatus.SERVICE)
        self.assertEqual(self.design.cylinder_set()["short_by"], 1)
        self.assertEqual(len(tools_for(self.design)), 1)


class LifeIsDerivedTests(ToolingTestCase):
    def wear(self, tool, quantity, scrapped="0"):
        """A sack booking that used the tool."""
        order = self.bag_order(quantity)
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        entry = self.produce(order, quantity, scrapped=scrapped, uom=self.pcs)
        entry.post()
        return entry

    def test_a_fresh_cylinder_has_its_whole_life(self):
        tool = self.cylinder(limit="500000")
        self.assertEqual(tool.used(), Decimal("0"))
        self.assertEqual(tool.remaining(), Decimal("500000"))
        self.assertFalse(tool.is_worn())

    def test_output_wears_it(self):
        tool = self.cylinder(limit="500000")
        self.wear(tool, "1000")
        self.assertEqual(tool.used(), Decimal("1000"))
        self.assertEqual(tool.remaining(), Decimal("499000"))
        self.assertEqual(tool.used_percent(), Decimal("0.20"))

    def test_scrap_wore_it_too(self):
        """
        A cylinder printed the spoiled bags as well as the good ones.
        """
        tool = self.cylinder(limit="500000")
        order = self.bag_order("1000")
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "900", scrapped="100", uom=self.pcs).post()
        self.assertEqual(tool.used(), Decimal("1000"))

    def test_voiding_the_booking_takes_the_wear_back(self):
        """
        Derived, not counted down. A stored remaining figure would be
        wrong the moment a run was voided and nothing would say so.
        """
        tool = self.cylinder(limit="500000")
        entry = self.wear(tool, "1000")
        entry.void(TODAY)
        self.assertEqual(tool.used(), Decimal("0"))
        self.assertEqual(tool.remaining(), Decimal("500000"))

    def test_a_cylinder_nobody_has_rated_is_not_unlimited(self):
        """
        An unknown life is not an unlimited one. Answering no would
        let an unrated cylinder run for ever.
        """
        tool = self.cylinder(limit=None)
        self.assertIsNone(tool.remaining())
        self.assertIsNone(tool.is_worn())
        self.assertIsNone(tool.used_percent())
        self.assertTrue(tool.is_usable())


class RefusingAWornToolTests(ToolingTestCase):
    def test_a_worn_cylinder_cannot_go_on_a_run(self):
        tool = self.cylinder(limit="800")
        order = self.bag_order("1000")
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "1000", uom=self.pcs).post()
        self.assertTrue(tool.is_worn())

        next_order = self.bag_order("1000")
        next_order.tools.add(tool)
        with self.assertRaisesMessage(ValidationError, "worn out"):
            next_order.release(TODAY)

    def test_a_cylinder_away_for_service_cannot_go_on_a_run(self):
        tool = self.cylinder(status=ToolStatus.SERVICE)
        order = self.bag_order()
        order.tools.add(tool)
        with self.assertRaisesMessage(ValidationError, "away for service"):
            order.release(TODAY)

    def test_the_check_happens_where_a_swap_is_still_cheap(self):
        """
        At release, not at the first booking. By then the press is set
        up and the changeover costs a run.
        """
        tool = self.cylinder(status=ToolStatus.RETIRED)
        order = self.bag_order()
        order.tools.add(tool)
        with self.assertRaises(ValidationError):
            order.release(TODAY)
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")


class UnitsTests(ToolingTestCase):
    def test_a_cylinder_rated_in_bags_refuses_a_run_counted_in_kilos(self):
        """
        Wear read against the wrong unit gives a tool that looks fresh
        and is finished.
        """
        tool = self.cylinder(limit="500000")
        order = self.order()          # counted in kilogrammes
        order.tools.add(tool)
        with self.assertRaisesMessage(ValidationError, "not the same kind of thing"):
            order.release(TODAY)

    def test_a_tool_rated_in_the_runs_own_unit_is_fine(self):
        tool = Tool.objects.create(
            code="SCR-1", name="Extruder screen", kind=ToolKind.SCREEN,
            life_limit=Decimal("50000"), life_uom=self.kg,
        )
        order = self.order()
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "1000").post()
        self.assertEqual(tool.used(), Decimal("1000"))

    def test_a_convertible_unit_converts(self):
        tonne = UnitOfMeasure.objects.create(
            code="t", name="Tonne", category=UnitOfMeasureCategory.WEIGHT,
            base_unit=self.kg, conversion_factor=Decimal("1000"),
        )
        tool = Tool.objects.create(
            code="SCR-2", name="Screen", kind=ToolKind.SCREEN,
            life_limit=Decimal("50"), life_uom=tonne,
        )
        order = self.order()
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "1000").post()
        self.assertEqual(tool.used(), Decimal("1.0000"))


class WhatIsAboutToWearOutTests(ToolingTestCase):
    def test_it_lists_the_worst_first(self):
        nearly = self.cylinder("CYL-1", limit="1000")
        fresh = self.cylinder("CYL-2", limit="500000")
        order = self.bag_order("950")
        order.tools.add(nearly, fresh)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "950", uom=self.pcs).post()
        rows = wearing_out(threshold=Decimal("90"))
        self.assertEqual([tool.code for tool, _share, _left in rows], ["CYL-1"])
        self.assertEqual(rows[0][1], Decimal("95.00"))
        self.assertEqual(rows[0][2], Decimal("50"))

    def test_a_retired_tool_is_not_a_worry(self):
        tool = self.cylinder("CYL-1", limit="1000")
        order = self.bag_order("950")
        order.tools.add(tool)
        order.release(TODAY)
        self.full_issue(order)
        self.produce(order, "950", uom=self.pcs).post()
        self.assertEqual(len(wearing_out()), 1)
        tool.status = ToolStatus.RETIRED
        tool.save()
        self.assertEqual(wearing_out(), [])

    def test_an_unrated_tool_cannot_be_nearly_worn(self):
        self.cylinder("CYL-1", limit=None)
        self.assertEqual(wearing_out(threshold=Decimal("0")), [])
