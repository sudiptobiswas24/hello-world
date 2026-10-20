"""audit2_core: adders no browser test had driven, by the people who use them."""
import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    expect = None

from apps.accounting.models import Account, AccountType, ChargeType, Tax
from apps.sales.price_variation import PriceIndex

from .tests_browser import BrowserTestCase


class UndrivenAddersTests(BrowserTestCase):
    def test_the_controller_gives_a_charge_type_a_tax(self):
        liability = Account.objects.create(code="2300", name="GST payable", account_type=AccountType.LIABILITY)
        tax = Tax.objects.create(code="GST18", name="GST 18%", rate=Decimal("18"), collected_account=liability,
                                 paid_account=liability)
        charge = ChargeType.objects.create(code="FRT", name="Freight", revenue_account=self.revenue,
                                           expense_account=self.revenue)
        page = self.sign_in(self.person("Controller"), f"/app/settings/charge-types/{charge.pk}")
        page.get_by_role("button", name="Add a tax").click()
        form = page.get_by_role("form", name="Add a tax")
        form.get_by_label("Tax").select_option(label="GST 18%")
        form.get_by_role("button", name="Add a tax").click()
        expect(page.locator("section.related", has_text="Taxes it carries")).to_contain_text("GST 18%")
        self.assertEqual(list(charge.taxes.all()), [tax])
        self.assertEqual(self.problems, [])

    def test_the_ar_manager_publishes_a_price_index_value(self):
        index = PriceIndex.objects.create(code="PP-HDPE", name="HDPE granule index")
        page = self.sign_in(self.person("AR Manager"), f"/app/sales/price-indices/{index.pk}")
        page.get_by_role("button", name="Publish a value").click()
        form = page.get_by_role("form", name="Publish a value")
        form.get_by_label("From").fill("2026-04-01")
        form.get_by_label("Value").fill("104.25")
        form.get_by_role("button", name="Publish a value").click()
        expect(page.locator("section.related", has_text="Values")).to_contain_text("104.25")
        self.assertEqual(index.values.get().value, Decimal("104.25"))
        self.assertEqual(self.problems, [])
