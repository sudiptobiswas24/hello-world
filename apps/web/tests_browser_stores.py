"""
A stock count in the browser, by the two people it takes: the store
writes down what the shelf holds, and the Stores Manager posts the
difference. 100 widgets on the books at 5.00, 96 counted: 20.00 to
shrinkage.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.inventory.models import StockCount
from apps.inventory.tests_adjustments import AdjustmentTestCase

from .tests_browser import BrowserMixin


class StoresInTheBrowserTests(BrowserMixin, AdjustmentTestCase, StaticLiveServerTestCase):
    def url(self, path):
        return f"{self.live_server_url}/app{path}"

    def test_the_store_counts_and_the_manager_posts_the_difference(self):
        self.stock("100", "5")
        store = self.sign_in(self.person("Warehouse Staff"), "/app/stores/counts/new")
        store.get_by_label("Counted on").fill("2026-03-01")
        store.get_by_label("Warehouse").select_option(label="Main")
        store.get_by_label("Differences booked as").select_option(label="Count variance")
        store.get_by_role("button", name="Create").click()
        store.wait_for_url(re.compile(r"/stores/counts/\d+$"))
        count = StockCount.objects.get()

        store.get_by_role("button", name="Add a count").click()
        form = store.get_by_role("form", name="Add a count")
        form.get_by_role("combobox", name="Item").fill("Widget")
        store.get_by_role("option", name=re.compile("Widget")).click()
        form.get_by_label("Counted").fill("96")
        form.get_by_role("button", name="Add a count").click()
        expect(store.locator("section.related", has_text="Counted")).to_contain_text("96")
        line = count.lines.get()
        self.assertEqual((line.system_quantity, line.counted_quantity), (Decimal("100"), Decimal("96")))
        expect(store.get_by_role("button", name="Post the differences")).to_have_count(0)

        manager = self.sign_in(self.person("Stores Manager"), f"/app/stores/counts/{count.pk}", page=self.new_page())
        manager.get_by_role("button", name="Post the differences").click()
        manager.get_by_role("form", name="Post the differences").get_by_role(
            "button", name="Post the differences").click()
        expect(manager.locator(".toast", has_text="Posted").first).to_be_visible()
        count.refresh_from_db()
        self.assertTrue(count.posted)
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("96"))
        self.assertEqual(self.balance(self.shrinkage), Decimal("20.00"))
        self.assertEqual(self.problems, [])
