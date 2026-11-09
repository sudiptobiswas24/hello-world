"""
A lathe written off by mistake, put back in the browser by the controller, who alone may
write one off; the bookkeeper reads the asset and is offered neither.
"""

import datetime
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from apps.assets.models import AssetStatus
from apps.assets.tests import AssetTestCase

from .tests_browser import BrowserMixin


class ReinstatingInTheBrowserTests(BrowserMixin, AssetTestCase, StaticLiveServerTestCase):
    def test_the_controller_puts_back_a_lathe_written_off_in_error(self):
        asset = self.asset()
        asset.dispose(on_date=datetime.date(2026, 3, 10))
        keeper = self.sign_in(self.person("Bookkeeper"), f"/app/accounts/assets/{asset.pk}")
        expect(keeper.locator("main")).to_contain_text(asset.number)
        expect(keeper.get_by_role("button", name="Reinstate it")).to_have_count(0)

        controller = self.sign_in(self.person("Controller"), f"/app/accounts/assets/{asset.pk}",
                                  page=self.new_page())
        controller.get_by_role("button", name="Reinstate it").click()
        form = controller.get_by_role("form", name="Reinstate it")
        form.get_by_label("Why").fill("Wrong lathe written off")
        form.get_by_role("button", name="Reinstate it").click()
        expect(controller.locator(".toast, [role=status]").first).to_contain_text("Reinstated")
        asset.refresh_from_db()
        self.assertEqual((asset.status, asset.disposed_on, self.balance(self.disposal)),
                         (AssetStatus.IN_SERVICE, None, Decimal("0")))
        self.assertEqual(self.problems, [])


class ATypoCorrectedOnTheDraftInTheBrowserTests(BrowserMixin, AssetTestCase, StaticLiveServerTestCase):
    """
    O266: the controller corrects what the old system had depreciated on a hand draft (3,000 typed
    for the 2,250 nine months of 250 come to); once it is in service the box is not offered.
    """

    def test_the_controller_corrects_the_draft_and_it_is_fixed_once_posted(self):
        from apps.assets.models import FixedAsset

        loom = FixedAsset.objects.create(name="Loom 7", category=self.category, cost=Decimal("12000"),
                                         life_months=48, acquisition_date=datetime.date(2025, 4, 1),
                                         depreciated_before=datetime.date(2025, 12, 31),
                                         opening_depreciation=Decimal("3000"))
        page = self.sign_in(self.person("Controller"), f"/app/accounts/assets/{loom.pk}")
        box = page.get_by_label("Depreciated elsewhere", exact=True)
        expect(box).to_be_enabled()
        box.fill("2250")
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("Saved")).to_be_visible()
        loom.refresh_from_db()
        self.assertEqual(loom.opening_depreciation, Decimal("2250.00"))

        loom.place_in_service(on_date=datetime.date(2025, 4, 1))
        page.reload()
        expect(page.locator("main")).to_contain_text(loom.number)
        expect(page.get_by_role("textbox", name="Depreciated elsewhere", exact=True)).to_have_count(0)
        self.assertEqual(self.problems, [])
