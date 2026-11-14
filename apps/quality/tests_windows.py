"""
Inspection plans in turn: the limits that apply depend on the day.

A plan for October and its replacement from November may both be
active. Two covering the same day may not.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import UnitOfMeasure
from apps.core.windows import covers, overlaps
from apps.inventory.models import Item

from .models import InspectionPlan
from .release import plan_for

OCT_31 = datetime.date(2026, 10, 31)
NOV_1 = datetime.date(2026, 11, 1)


class TheArithmeticTests(TestCase):
    def test_both_ends_are_inclusive(self):
        self.assertTrue(covers(NOV_1, None, NOV_1))
        self.assertTrue(covers(None, OCT_31, OCT_31))
        self.assertFalse(covers(NOV_1, None, OCT_31))
        self.assertFalse(covers(None, OCT_31, NOV_1))

    def test_windows_that_meet_do_not_overlap(self):
        self.assertFalse(overlaps(None, OCT_31, NOV_1, None))

    def test_windows_sharing_a_day_do(self):
        self.assertTrue(overlaps(None, NOV_1, NOV_1, None))
        self.assertTrue(overlaps(None, None, NOV_1, NOV_1))

    def test_a_window_inside_another_overlaps_it(self):
        self.assertTrue(overlaps(
            datetime.date(2026, 1, 1), datetime.date(2026, 12, 31),
            datetime.date(2026, 6, 1), datetime.date(2026, 6, 2),
        ))


class PlansInTurnTests(TestCase):
    def setUp(self):
        uom = UnitOfMeasure.objects.create(code="kg", name="Kilogram")
        self.item = Item.objects.create(sku="FAB", name="Fabric", uom=uom)

    def plan(self, valid_from=None, valid_to=None):
        return InspectionPlan.objects.create(
            item=self.item, is_mandatory=False,
            valid_from=valid_from, valid_to=valid_to,
        )

    def test_the_plan_in_force_is_the_one_asked_for(self):
        october = self.plan(valid_to=OCT_31)
        november = self.plan(valid_from=NOV_1)
        self.assertEqual(plan_for(self.item, OCT_31), october)
        self.assertEqual(plan_for(self.item, NOV_1), november)

    def test_a_staged_plan_is_not_in_force_before_it_starts(self):
        """
        Alone, so no earlier plan can answer in its place. A mandatory
        plan staged for November that counted in October would hold
        every batch made today against limits nobody has adopted yet.
        """
        self.plan(valid_from=NOV_1)
        self.assertIsNone(plan_for(self.item, OCT_31))

    def test_two_plans_on_the_same_day_are_refused(self):
        self.plan(valid_to=NOV_1)
        with self.assertRaisesMessage(ValidationError, "already sets the limits"):
            self.plan(valid_from=NOV_1)

    def test_a_retired_plan_is_not_in_the_way(self):
        old = self.plan()
        old.is_active = False
        old.save()
        self.plan()
        self.assertEqual(InspectionPlan.objects.filter(is_active=True).count(), 1)

    def test_a_backwards_window_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "backwards"):
            self.plan(valid_from=NOV_1, valid_to=OCT_31)

    def test_the_table_refuses_two_open_ended_plans_that_skip_save(self):
        self.plan(valid_from=NOV_1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InspectionPlan.objects.bulk_create([InspectionPlan(
                item=self.item, is_mandatory=False,
                valid_from=datetime.date(2027, 1, 1),
            )])
