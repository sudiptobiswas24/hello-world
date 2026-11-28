"""
A recipe in the browser, by the person who keeps them: regrind made a
thousand kilogrammes a batch from virgin polymer with 2% of the input
lost, so 1,000 / 0.98 = 1,020.4082 kg goes in. Each step is read back
from the database.
"""

import re
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.manufacturing.models import BillOfMaterials
from apps.manufacturing.tests_orders import RunTestCase

from .tests_browser import BrowserMixin


class MakingInTheBrowserTests(BrowserMixin, RunTestCase, StaticLiveServerTestCase):
    def test_the_engineer_writes_a_recipe(self):
        page = self.sign_in(self.person("Process Engineer"), "/app/making/boms/new")
        page.get_by_role("combobox", name="Makes").fill("REGRIND")
        page.get_by_role("option", name=re.compile("REGRIND")).click()
        page.get_by_label("A batch makes").fill("1000")
        page.get_by_label("Unit").select_option(label="kg")
        page.get_by_role("button", name="Create").click()
        page.wait_for_url(re.compile(r"/making/boms/\d+$"))
        bom = BillOfMaterials.objects.get(item=self.regrind)

        page.get_by_role("button", name="Add an input").click()
        form = page.get_by_role("form", name="Add an input")
        form.get_by_role("combobox", name="Item").fill("PP-RAFFIA")
        page.get_by_role("option", name=re.compile("PP-RAFFIA")).click()
        form.get_by_label("Per batch").fill("1000")
        form.get_by_label("Unit").select_option(label="kg")
        form.get_by_label("Waste %").fill("2")
        form.get_by_role("button", name="Add an input").click()
        inputs = page.locator("section.related", has_text="Inputs")
        expect(inputs).to_contain_text("PP homopolymer")
        expect(inputs).to_contain_text("1,020.4082")
        component = bom.components.get()
        self.assertEqual((component.item, component.quantity, component.gross_quantity()),
                         (self.virgin, Decimal("1000"), Decimal("1000") / Decimal("0.98")))
        self.assertEqual(self.problems, [])
