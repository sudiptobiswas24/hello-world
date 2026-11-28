"""
Two things the office asked for, in the browser: a list leaves the
system as a CSV file of what the screen shows, and typing a record's
code into the palette lands on the record, not on a screen.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.inventory.models import Item

from .tests_browser import BrowserTestCase


class ListExportInTheBrowserTests(BrowserTestCase):
    def test_the_items_list_downloads_as_a_file_of_its_columns(self):
        page = self.sign_in(self.person("Stores Manager"), "/app/stores/items")
        expect(page.locator("tbody")).to_contain_text("WDG-1")
        with page.expect_download() as downloading:
            page.get_by_role("button", name="CSV").click()
        download = downloading.value
        self.assertTrue(download.suggested_filename.endswith(".csv"), download.suggested_filename)
        text = open(download.path(), encoding="utf-8-sig").read()
        header, *rows = text.splitlines()
        self.assertGreaterEqual(header.count(","), 2)
        self.assertEqual(len(rows), Item.objects.count())
        self.assertTrue(any("WDG-1" in row for row in rows), rows)
        self.assertEqual(self.problems, [])


class FindARecordInTheBrowserTests(BrowserTestCase):
    def test_a_code_typed_into_the_palette_opens_the_record(self):
        item = Item.objects.get(sku="WDG-1")
        page = self.sign_in(self.person("Stores Manager"), "/app/")
        page.get_by_role("button", name=re.compile("Go to")).click()
        page.get_by_label("Go to a screen").fill("WDG-1")
        page.get_by_role("option", name=re.compile("WDG-1")).click()
        page.wait_for_url(re.compile(rf"/stores/items/{item.pk}$"))
        expect(page.locator("main")).to_contain_text("WDG-1")
        self.assertEqual(self.problems, [])
