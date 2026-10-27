"""
The year end in the browser, as the Controller: foreign balances restated at the closing rate and the
restatement voided; the Income-tax depreciation schedule read by block and filed. Neither is offered to a
Bookkeeper, and the API refuses both to one. Figures from /home/user/work/calc_fix_yearend/calc.py.
"""

import datetime
import re
from decimal import Decimal

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, BooksOpening
from apps.accounting.restatement import Restatement
from apps.assets.management.commands.load_tax_depreciation_rates import load
from apps.assets.models import AssetCategory, FixedAsset
from apps.assets.tax import TaxBlock, TaxBlockOpening, TaxBlockRate, TaxDepreciationYear
from apps.core.models import Company, Currency, ExchangeRate

from .tests_browser import BrowserTestCase

D = datetime.date


class YearEndInTheBrowserTests(BrowserTestCase):
    def setUp(self):
        super().setUp()
        inr = Currency.objects.filter(is_base=True).first() or Currency.objects.create(
            code="INR", name="Rupee", is_base=True)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        gain, loss = acc("4900", "FX gain", AccountType.INCOME), acc("5900", "FX loss", AccountType.EXPENSE)
        company = Company.objects.first()
        if company is None:
            Company.objects.create(name="Plant", base_currency=inr, fx_gain_account=gain, fx_loss_account=loss)
        else:
            company.fx_gain_account, company.fx_loss_account = gain, loss
            company.save()
        # The fixture may keep USD already, as its base: the foreign currency is whichever of the two is not.
        usd = Currency.objects.filter(code="USD").first()
        base = Company.objects.first().base_currency
        self.usd = (usd if usd is not None and usd != base
                    else Currency.objects.create(code="USD" if usd is None else "EUR", name="Foreign"))
        ExchangeRate.objects.create(currency=self.usd, rate=Decimal("83.3367"), valid_from=D(2026, 3, 5))
        ExchangeRate.objects.create(currency=self.usd, rate=Decimal("84.1"), valid_from=D(2026, 3, 31))

    def usd_invoice(self):
        from apps.sales.models import SalesOrder, SalesOrderLine

        order = SalesOrder.objects.create(customer=self.customer, order_date=D(2026, 3, 5), currency=self.usd)
        SalesOrderLine.objects.create(order=order, item=self.item, uom=self.uom, quantity=Decimal("1"),
                                      unit_price=Decimal("1000"), revenue_account=self.revenue)
        order.confirm()
        invoice = order.create_invoice(self.ar, invoice_date=D(2026, 3, 5))
        invoice.post()
        return invoice

    def test_the_controller_restates_the_year_end_and_voids_it(self):
        invoice = self.usd_invoice()
        page = self.sign_in(self.person("Controller"), "/app/accounts/restatements")
        page.get_by_label("As at").fill("2026-03-31")
        preview = page.get_by_role("table", name="Open in a foreign currency")
        expect(preview.locator("tbody")).to_contain_text(invoice.number)
        expect(preview.locator("tbody")).to_contain_text("83,336.70")
        page.get_by_role("button", name="Restate", exact=True).click()
        made = page.get_by_role("article")
        expect(made).to_contain_text("84,100.00")
        expect(made).to_contain_text("763.30")
        restatement = Restatement.objects.get()
        self.assertEqual(restatement.lines.get().difference, Decimal("763.30"))
        page.get_by_role("button", name=f"Void {restatement.number}").click()
        expect(made).to_contain_text("Void from 2026-03-31")
        restatement.refresh_from_db()
        self.assertEqual(restatement.voided_on, D(2026, 3, 31))
        self.assertEqual(self.problems, [])

    def test_the_controller_reads_the_tax_schedule_and_files_it(self):
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        accounts = dict(asset_account=acc("1500", "Plant", AccountType.ASSET),
                        accumulated_account=acc("1590", "Accumulated", AccountType.ASSET),
                        expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
                        disposal_account=acc("6200", "Disposal", AccountType.EXPENSE))
        BooksOpening.objects.create(opened_on=D(2025, 12, 31), opening_account=acc("3900", "Opening", AccountType.EQUITY))
        load()
        block = TaxBlock.objects.get(code="PM-GEN")
        category = AssetCategory.objects.create(code="PM", name="Plant", default_life_months=120, tax_block=block,
                                                **accounts)
        TaxBlockOpening.objects.create(block=block, year_start=D(2025, 4, 1), written_down_value=Decimal("1000000"))
        for cost, used_from in (("200000", D(2025, 10, 4)), ("300000", D(2025, 10, 2))):
            FixedAsset.objects.create(name=f"Press {cost}", category=category, acquisition_date=used_from,
                                      cost=Decimal(cost), life_months=120).place_in_service(on_date=used_from)
        page = self.sign_in(self.person("Controller"), "/app/accounts/tax-depreciation")
        # The accountant confirms the rate the year is filed at, from the screen.
        page.get_by_role("button", name="Confirm PM-GEN from 2017-04-01").click()
        expect(page.get_by_role("button", name="Confirm PM-GEN from 2017-04-01")).to_have_count(0)
        self.assertIsNotNone(TaxBlockRate.objects.get(section=block, applies_from=D(2017, 4, 1)).confirmed_on)
        page.get_by_label("Tax year from").fill("2025-04-01")
        row = page.get_by_role("table", name="Blocks").locator("tbody tr", has_text="PM-GEN")
        # 13,00,000 x 15% + 2,00,000 x 7.5% = 2,10,000; closing 15,00,000 - 2,10,000.
        expect(row).to_contain_text("2,10,000.00")
        expect(row).to_contain_text("12,90,000.00")
        page.get_by_role("button", name="File the year").click()
        expect(page.get_by_text(re.compile("Filed on"))).to_be_visible()
        self.assertEqual(TaxDepreciationYear.objects.get().lines.get().depreciation, Decimal("210000.00"))
        self.assertEqual(self.problems, [])

    def test_a_bookkeeper_is_offered_neither(self):
        self.usd_invoice()
        bookkeeper = self.person("Bookkeeper")
        page = self.sign_in(bookkeeper, "/app/accounts/trial-balance")
        expect(page.get_by_role("link", name="Trial balance").first).to_be_visible()
        expect(page.get_by_role("link", name="Restate foreign balances")).to_have_count(0)
        expect(page.get_by_role("link", name="Tax depreciation by block")).to_have_count(0)
        client = APIClient()
        client.force_authenticate(bookkeeper)
        self.assertEqual(client.post("/api/accounting/restatements/restate/", {"as_of": "2026-03-31"},
                                     format="json").status_code, 403)
        self.assertEqual(client.get("/api/assets/tax-years/schedule/", {"year": "2025-04-01"}).status_code, 403)
        self.assertFalse(Restatement.objects.exists())
