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

from apps.inventory.models import StockAdjustment, StockCount
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

    def test_the_manager_counts_in_something_never_stocked_at_the_cost_given(self):
        # The g22 month: 500 found with no cost on the books, at 30.00 each, 15,000.00 on the shelf.
        page = self.sign_in(self.person("Stores Manager"), "/app/stores/adjustments/new")
        page.get_by_label("Date").fill("2026-03-01")
        page.get_by_label("Warehouse").select_option(label="Main")
        page.get_by_label("Reason").select_option(label="Found on shelf")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/stores/adjustments/\d+$"))
        adjustment = StockAdjustment.objects.get()

        page.get_by_role("button", name="Add a line").click()
        form = page.get_by_role("form", name="Add a line")
        form.get_by_role("combobox", name="Item").fill("Widget")
        page.get_by_role("option", name=re.compile("Widget")).click()
        form.get_by_label("Quantity").fill("500")
        form.get_by_label("Cost each").fill("30.00")
        form.get_by_role("button", name="Add a line").click()
        expect(form).to_have_count(0)
        self.assertEqual(adjustment.lines.get().unit_cost, Decimal("30.0000"))

        page.get_by_role("button", name="Post", exact=True).click()
        expect(page.get_by_role("button", name="Post", exact=True)).to_have_count(0)
        adjustment.refresh_from_db()
        self.assertTrue(adjustment.posted)
        self.assertEqual(self.item.stock_value_at(self.warehouse), Decimal("15000.00"))
        self.assertEqual(self.balance(self.gain), Decimal("-15000.00"))
        self.assertEqual(self.problems, [])


from apps.manufacturing.tests_recall import RecallTestCase  # noqa: E402


class HeldBecauseInTheBrowserTests(BrowserMixin, RecallTestCase, StaticLiveServerTestCase):
    """
    O207: the Stores Manager's list of batches keeps showing what quality
    holds, each saying why in the gate's words, and nothing beside a batch
    that may go. B1 rejected after RB-A (its own pass before) was made from it.
    """

    def test_the_stores_manager_sees_why_each_batch_is_held(self):
        rejection = self.reject_b1()
        page = self.sign_in(self.person("Stores Manager"), "/app/stores/batches")
        expect(page.get_by_role("columnheader", name="Held because")).to_be_visible()
        expect(page.get_by_role("row", name=re.compile("RB-A"))).to_contain_text(
            f"made from {self.b1}, rejected on 1 June 2026 ({rejection.number})")
        expect(page.get_by_role("row", name=re.compile(self.b2.code))).not_to_contain_text("held")
        self.assertEqual(self.problems, [])
