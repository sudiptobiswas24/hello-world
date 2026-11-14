"""review_cost probes: O3 write-down at what the shelf loses, O205 posted stock documents stay (through the API)."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from .adjustments import StockAdjustment, StockAdjustmentLine, StockCount, StockCountLine
from .tests_adjustments import AdjustmentTestCase

D = Decimal


class StockProbes(AdjustmentTestCase):
    def head(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("stores_head")
        user.groups.add(Group.objects.get(name="Stores Manager"))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_posted_adjustments_line_cannot_be_deleted_or_edited_over_the_api(self):
        self.stock("100", "5")
        posted = self.adjust("-4", reason=self.shrink)
        line = posted.lines.get()
        client = self.head()
        gone = client.delete(f"/api/inventory/stock-adjustment-lines/{line.pk}/")
        self.assertIn(gone.status_code, (400, 403), gone.content[:200])
        self.assertTrue(StockAdjustmentLine.objects.filter(pk=line.pk).exists())
        edited = client.patch(f"/api/inventory/stock-adjustment-lines/{line.pk}/", {"quantity": "-90"}, format="json")
        self.assertIn(edited.status_code, (400, 403), edited.content[:200])
        line.refresh_from_db()
        self.assertEqual(line.quantity, D("-4"))

    def test_a_posted_counts_line_cannot_be_deleted_over_the_api(self):
        self.stock("100", "5")
        count = StockCount.objects.create(count_date=datetime.date(2026, 3, 1), warehouse=self.warehouse,
                                          reason=self.recount)
        count.add(self.item, "100")
        count.post()
        line = count.lines.get()
        gone = self.head().delete(f"/api/inventory/stock-count-lines/{line.pk}/")
        self.assertIn(gone.status_code, (400, 403), gone.content[:200])
        self.assertTrue(StockCountLine.objects.filter(pk=line.pk).exists())

    def test_a_write_down_at_the_shelf_rate_books_what_the_shelf_loses(self):
        """3,000 in at 3.3333 (10,000): 1,000 written off at 3.3333 books 3,333.33 on both books."""
        self.stock("3000", "3.33333333")
        before = self.balance(self.inventory)
        adjustment = self.adjust("-1000", reason=self.shrink, cost="3.3333")
        self.assertEqual(self.item.stock_value_at(self.warehouse), D("6666.67"))
        self.assertEqual(round(adjustment.lines.get().posted_value, 2), D("-3333.33"))
        self.assertEqual(self.balance(self.shrinkage), D("3333.33"))
        del before

    def test_a_write_down_of_cases_with_a_cost_per_stocking_unit(self):
        """10 cases of 12 each at 5 -> 120 each, 600. Write off 2 cases (24 each, 120.00); cost given per each (5)."""
        self.stock("120", "5")
        adjustment = self.adjust("-2", reason=self.shrink, uom=self.case, cost="5")
        self.assertEqual((round(adjustment.lines.get().posted_value, 2), self.balance(self.shrinkage)), (D("-120.00"), D("120.00")))
