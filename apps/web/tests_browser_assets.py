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
