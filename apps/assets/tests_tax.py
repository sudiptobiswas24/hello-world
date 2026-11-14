"""
Tax depreciation by block of assets (assets.tax, g24): a schedule beside the books, never posted.

Figures from /home/user/work/calc_fix_yearend/calc.py, worked for the tax year 2025-26 here (a year that
has ended, so it can be filed): P&M at 15% opening at 10,00,000, two machines put to use with 179 and
181 days left in the year, an old one sold for 50,000; computers sold out of their block; furniture sold
for more than its block.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, BooksOpening, JournalLine
from apps.core.models import Company, Currency

from .management.commands.load_tax_depreciation_rates import load
from .models import AssetCategory, FixedAsset
from .tax import TaxBlock, TaxBlockOpening, TaxBlockRate, TaxDepreciationYear, days_used, schedule

D = datetime.date
YEAR = D(2025, 4, 1)


class TaxDepreciationTestCase(TestCase):
    def setUp(self):
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Plant", base_currency=inr)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.accounts = dict(asset_account=acc("1500", "Plant", AccountType.ASSET),
                             accumulated_account=acc("1590", "Accumulated", AccountType.ASSET),
                             expense_account=acc("6100", "Depreciation", AccountType.EXPENSE),
                             disposal_account=acc("6200", "Disposal", AccountType.EXPENSE))
        BooksOpening.objects.create(opened_on=D(2025, 12, 31), opening_account=acc("3900", "Opening", AccountType.EQUITY))
        load()
        self.blocks = {block.code: block for block in TaxBlock.objects.all()}
        self.categories = {}

    def category(self, code):
        if code not in self.categories:
            self.categories[code] = AssetCategory.objects.create(
                code=code, name=code, default_life_months=120, tax_block=self.blocks[code], **self.accounts)
        return self.categories[code]

    def asset(self, code, cost, put_to_use):
        asset = FixedAsset.objects.create(name=f"{code} {cost}", category=self.category(code), acquisition_date=put_to_use,
                                          cost=Decimal(cost), life_months=120)
        asset.place_in_service(on_date=put_to_use)
        asset.refresh_from_db()
        return asset

    def opening(self, code, wdv):
        TaxBlockOpening.objects.create(block=self.blocks[code], year_start=YEAR, written_down_value=Decimal(wdv))

    def the_year(self):
        self.opening("PM-GEN", "1000000")
        old = self.asset("PM-GEN", "60000", D(2025, 1, 1))
        self.asset("PM-GEN", "200000", D(2025, 10, 4))  # 179 days to 31 March 2026
        self.asset("PM-GEN", "300000", D(2025, 10, 2))  # 181 days
        old.dispose(on_date=D(2026, 1, 15), proceeds=Decimal("50000"))
        self.opening("PM-COMP", "100000")
        self.asset("PM-COMP", "90000", D(2025, 1, 1)).dispose(on_date=D(2026, 2, 1), proceeds=Decimal("70000"))
        self.opening("FURN", "40000")
        furniture = self.asset("FURN", "50000", D(2025, 1, 1))
        self.asset("FURN", "10000", D(2025, 5, 1))
        furniture.dispose(on_date=D(2026, 2, 1), proceeds=Decimal("65000"))
        return {row.block.code: row for row in schedule(YEAR) if row.has_figures()}

    def confirm_all(self):
        for rate in TaxBlockRate.objects.all():
            rate.confirm(None)


class TheScheduleTests(TaxDepreciationTestCase):
    def test_the_rates_are_loaded_unconfirmed_and_once(self):
        self.assertEqual(TaxBlockRate.objects.filter(confirmed_on__isnull=False).count(), 0)
        self.assertEqual(load(), 0)
        rate = TaxBlockRate.objects.get(section__code="PM-GEN", applies_from=D(2026, 4, 1))
        self.assertEqual(rate.rate_percent, Decimal("15"))
        self.assertIn("Income-tax Act 2025 s.33", rate.authority)

    def test_the_180_day_rule_counts_both_ends(self):
        self.assertEqual((days_used(D(2025, 10, 4), YEAR), days_used(D(2025, 10, 3), YEAR),
                          days_used(D(2025, 10, 2), YEAR)), (179, 180, 181))

    def test_additions_at_half_and_full_rate_and_a_sale(self):
        row = self.the_year()["PM-GEN"]
        self.assertEqual((row.opening, row.additions_full, row.additions_half, row.sales),
                         (Decimal("1000000"), Decimal("300000"), Decimal("200000"), Decimal("50000")))
        self.assertEqual((row.depreciation, row.closing, row.capital_gain),
                         (Decimal("202500.00"), Decimal("1247500.00"), Decimal("0")))

    def test_a_block_emptied_by_its_sales_ceases_with_a_loss(self):
        row = self.the_year()["PM-COMP"]
        self.assertEqual((row.ceased, row.depreciation, row.closing, row.capital_gain),
                         (True, Decimal("0"), Decimal("0"), Decimal("-30000")))

    def test_a_sale_for_more_than_the_block_is_a_gain(self):
        row = self.the_year()["FURN"]
        self.assertEqual((row.ceased, row.depreciation, row.closing, row.capital_gain),
                         (False, Decimal("0"), Decimal("0"), Decimal("15000")))

    def test_the_book_depreciation_beside_it_is_the_books_own(self):
        rows = self.the_year()
        for asset in FixedAsset.objects.filter(status="in_service"):
            asset.depreciate(through=D(2026, 3, 31))
        row = {r.block.code: r for r in schedule(YEAR)}["PM-GEN"]
        booked = sum((charge.amount for asset in FixedAsset.objects.filter(tax_block=self.blocks["PM-GEN"])
                      for charge in asset.depreciation_entries.filter(reversal__isnull=True,
                                                                       period_end__gte=YEAR,
                                                                       period_end__lte=D(2026, 3, 31))),
                     Decimal("0"))
        self.assertGreater(booked, 0)
        self.assertEqual(row.book_depreciation, booked)
        self.assertEqual(rows["PM-GEN"].depreciation, row.depreciation)


class FilingTests(TaxDepreciationTestCase):
    def test_refused_at_an_unconfirmed_rate(self):
        self.the_year()
        with self.assertRaisesMessage(ValidationError, "is not confirmed by the accountant"):
            TaxDepreciationYear.file(YEAR)

    def test_a_filed_year_is_kept_and_holds_its_assets(self):
        self.the_year()
        self.confirm_all()
        year = TaxDepreciationYear.file(YEAR)
        line = year.lines.get(block__code="PM-GEN")
        self.assertEqual((line.depreciation, line.closing, line.rate_percent),
                         (Decimal("202500.00"), Decimal("1247500.00"), Decimal("15")))
        with self.assertRaisesMessage(ValidationError, "is already filed"):
            TaxDepreciationYear.file(YEAR)
        lathe = FixedAsset.objects.get(name="PM-GEN 300000")
        with self.assertRaisesMessage(ValidationError, "is filed and rests on this year"):
            lathe.dispose(on_date=D(2026, 3, 1), proceeds=Decimal("1"))
        sold = FixedAsset.objects.get(name="PM-COMP 90000")
        with self.assertRaisesMessage(ValidationError, "is filed and rests on this year"):
            sold.reinstate()
        lathe.tax_block = self.blocks["PM-40"]
        with self.assertRaisesMessage(ValidationError, "withdraw it before moving it"):
            lathe.save()
        rate = TaxBlockRate.objects.get(section__code="PM-GEN", applies_from=D(2017, 4, 1))
        rate.rate_percent = Decimal("20")
        with self.assertRaisesMessage(ValidationError, "filed at this rate"):
            rate.save()
        # The next year opens at what was filed, whatever is done after the year.
        nxt = {r.block.code: r for r in schedule(D(2026, 4, 1))}
        self.assertEqual(nxt["PM-GEN"].opening, Decimal("1247500.00"))
        year.withdraw()
        lathe.refresh_from_db()
        lathe.dispose(on_date=D(2026, 3, 1), proceeds=Decimal("1"))

    def test_a_year_not_ended(self):
        self.the_year()
        self.confirm_all()
        with self.assertRaisesMessage(ValidationError, "has not ended"):
            TaxDepreciationYear.file(D(2026, 4, 1))


class ThroughTheApiTests(TaxDepreciationTestCase):
    def as_(self, role):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_reads_and_files_it(self):
        self.the_year()
        client = self.as_("Controller")
        read = client.get("/api/assets/tax-years/schedule/", {"year": "2025-04-01"})
        self.assertEqual(read.status_code, 200, read.content)
        pm = next(row for row in read.json()["blocks"] if row["block_code"] == "PM-GEN")
        self.assertEqual((pm["depreciation"], pm["closing"], pm["rate_confirmed"]), ("202500.00", "1247500.00", False))
        for rate in client.get("/api/assets/tax-block-rates/").json():
            confirmed = client.post(f"/api/assets/tax-block-rates/{rate['id']}/confirm/",
                                    {"section": rate["section"], "applies_from": rate["applies_from"],
                                     "rate_percent": rate["rate_percent"]}, format="json")
            self.assertEqual(confirmed.status_code, 200, confirmed.content)
        filed = client.post("/api/assets/tax-years/file/", {"year": "2025-04-01"}, format="json")
        self.assertEqual(filed.status_code, 201, filed.content)

    def test_refused_to_a_bookkeeper(self):
        self.the_year()
        client = self.as_("Bookkeeper")
        self.assertEqual(client.get("/api/assets/tax-years/schedule/", {"year": "2025-04-01"}).status_code, 403)
        self.assertEqual(client.post("/api/assets/tax-years/file/", {"year": "2025-04-01"},
                                     format="json").status_code, 403)
