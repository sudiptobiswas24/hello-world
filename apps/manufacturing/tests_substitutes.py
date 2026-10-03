"""
Something else that will do.

A plant keeps two white masterbatches because one vendor is cheaper
and the other is reliable, and a run stopped for want of the first
while the second sits in the next bay is a run stopped for nothing.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.inventory.models import Item

from .bom import BomComponent, BomSubstitute
from .orders import MaterialIssue, MaterialIssueLine, WorkOrderStatus
from .tests_orders import TODAY, RunTestCase


class SubstituteTestCase(RunTestCase):
    def setUp(self):
        super().setUp()
        self.other_white = Item.objects.create(
            sku="MB-WHITE-B", name="White masterbatch, second source",
            uom=self.kg, standard_cost=Decimal("210"),
        )
        self.colour_line = self.bom.components.get(item=self.colour)

    def allow(self, item=None, ratio="1", priority=1):
        return BomSubstitute.objects.create(
            component=self.colour_line, item=item or self.other_white,
            quantity_per=Decimal(ratio), priority=priority,
        )

    def released(self):
        order = self.order()
        order.release(TODAY)
        return order

    def issue_line(self, order, item, quantity):
        issue = MaterialIssue.objects.create(
            work_order=order, issue_date=TODAY, warehouse=self.plant,
        )
        MaterialIssueLine.objects.create(
            issue=issue, item=item, quantity=Decimal(quantity), uom=self.kg,
            line_number=1,
        )
        issue.post()
        return issue


class WhatMayStandInTests(SubstituteTestCase):
    def test_an_item_cannot_stand_in_for_itself(self):
        with self.assertRaisesMessage(ValidationError, "stand in for itself"):
            self.allow(item=self.colour)

    def test_something_already_in_the_recipe_cannot_also_be_a_stand_in(self):
        """
        Counted twice, one issue would cover two requirements and the
        run would read as fully issued on half the material.
        """
        with self.assertRaisesMessage(ValidationError, "already a component"):
            self.allow(item=self.filler)

    def test_a_proper_stand_in_is_allowed(self):
        row = self.allow()
        self.assertEqual(row.item, self.other_white)
        self.assertEqual(row.quantity_per, Decimal("1"))


class IssuingTheStandInTests(SubstituteTestCase):
    def test_a_run_given_the_stand_in_is_a_run_that_got_its_material(self):
        """
        Counting only the original would report the run short for
        ever, and the variance would say the blend ran light when in
        fact it ran on the other masterbatch.
        """
        self.allow()
        self.stock(self.other_white, "100", "210")
        order = self.released()
        component = order.components.get(item=self.colour)
        self.issue_line(order, self.other_white, component.quantity_required)
        # The requirement is carried at six places and an issue line
        # stores four, so what went out is the rounded figure.
        self.assertEqual(
            component.quantity_issued(),
            component.quantity_required.quantize(Decimal("0.0001")),
        )

    def test_the_ratio_is_not_assumed_to_be_one(self):
        """
        Two grades of filler do not load the same. A half-strength
        stand-in takes two kilos to meet one of requirement.
        """
        self.allow(ratio="2")
        self.stock(self.other_white, "200", "210")
        order = self.released()
        component = order.components.get(item=self.colour)
        self.issue_line(order, self.other_white, "40")
        self.assertEqual(component.quantity_issued(), Decimal("20"))

    def test_the_original_and_the_stand_in_add_up(self):
        self.allow()
        self.stock(self.other_white, "100", "210")
        order = self.released()
        component = order.components.get(item=self.colour)
        self.issue_line(order, self.colour, "10")
        self.issue_line(order, self.other_white, "5")
        self.assertEqual(component.quantity_issued(), Decimal("15"))

    def test_something_not_approved_covers_nothing(self):
        self.stock(self.other_white, "100", "210")
        order = self.released()
        component = order.components.get(item=self.colour)
        self.issue_line(order, self.other_white, "20")
        self.assertEqual(component.quantity_issued(), Decimal("0"))

    def test_approving_it_after_the_run_started_changes_nothing(self):
        """
        Frozen at release with everything else. A substitution
        approved later must not retrospectively make a run read as
        issued.
        """
        self.stock(self.other_white, "100", "210")
        order = self.released()
        self.allow()
        component = order.components.get(item=self.colour)
        self.issue_line(order, self.other_white, "20")
        self.assertEqual(component.quantity_issued(), Decimal("0"))

    def test_returning_the_stand_in_takes_it_back(self):
        from .orders import IssueDirection

        self.allow()
        self.stock(self.other_white, "100", "210")
        order = self.released()
        component = order.components.get(item=self.colour)
        issue = self.issue_line(order, self.other_white, "20")
        self.assertEqual(component.quantity_issued(), Decimal("20"))
        issue.void(TODAY)
        self.assertEqual(component.quantity_issued(), Decimal("0"))
