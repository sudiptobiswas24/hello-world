"""
The supervisor reads the run board and the Gantt; the engineer raises a
change order from the bill of materials and applies it, and version 1
ends the day before version 2 begins.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.utils import timezone

from apps.inventory.models import Item
from apps.manufacturing.models import BillOfMaterials, BomChangeOrder, BomComponent, WorkCentre, WorkOrder

from .tests_browser import BrowserTestCase


class ProductionBoardInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        granule = Item.objects.create(sku="PP-1", name="Granules", uom=self.uom)
        self.bom = BillOfMaterials.objects.create(item=self.item, quantity_produced=Decimal("10"), uom=self.uom)
        BomComponent.objects.create(bom=self.bom, item=granule, quantity=Decimal("11"), uom=self.uom)
        centre = WorkCentre.objects.create(code="EXT-1", name="Extrusion line 1")
        WorkOrder.objects.create(item=self.item, bom=self.bom, quantity_ordered=Decimal("100"), uom=self.uom,
                                 warehouse=self.warehouse, work_centre=centre, scheduled_end=timezone.localdate())

    def test_the_board_the_gantt_and_a_change_order(self):
        page = self.sign_in(self.person("Production Supervisor"), "/app/production/board")
        expect(page.get_by_role("heading", name="Run board")).to_be_visible()
        expect(page.get_by_role("region", name="Not released")).to_contain_text("WDG-1")
        page.goto(f"{self.live_server_url}/app/production/gantt")
        expect(page.get_by_role("heading", name="Machine Gantt")).to_be_visible()

        engineer = self.sign_in(self.person("Process Engineer"), f"/app/making/boms/{self.bom.pk}", page=self.new_page())
        engineer.get_by_role("button", name="Raise a change order").click()
        form = engineer.get_by_role("form", name="Raise a change order")
        form.get_by_label("From").fill("2026-11-01")
        form.get_by_label("Why").fill("Thinner tape")
        form.get_by_role("button", name="Raise a change order").click()
        engineer.wait_for_url(re.compile(r"/making/change-orders/\d+$"))
        change = BomChangeOrder.objects.get()
        self.assertEqual((change.supersedes, change.draft.version, change.effective_from),
                         (self.bom, 2, datetime.date(2026, 11, 1)))

        engineer.get_by_role("button", name="Apply", exact=True).click()
        engineer.get_by_role("form", name="Apply").get_by_role("button", name="Apply", exact=True).click()
        self.toast(engineer, "Applied")
        change.refresh_from_db()
        self.bom.refresh_from_db()
        self.assertEqual((change.status, self.bom.valid_to, change.draft.is_default),
                         ("applied", datetime.date(2026, 10, 31), True))
        self.assertEqual(self.problems, [])
