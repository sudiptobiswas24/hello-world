"""review_yearend probes for the tax depreciation schedule. Figures: /home/user/work/calc_review_yearend/calc.py"""
from decimal import Decimal

from .models import FixedAsset
from .tax import TaxBlockLine, TaxDepreciationYear, schedule
from .tests_tax import D, YEAR, TaxDepreciationTestCase


class Probes(TaxDepreciationTestCase):
    def rows(self, year=YEAR):
        return {row.block.code: row for row in schedule(year) if row.has_figures()}

    def test_T01_a_sole_asset_scrapped_for_nothing_ceases_the_block(self):
        # Block opened at 0; one machine 100,000 bought 1 Jan 2025, scrapped 1 Feb 2026 for 0 (dispose's default).
        self.opening("PM-COMP", "0")
        asset = self.asset("PM-COMP", "100000", D(2025, 6, 1))
        asset.dispose(on_date=D(2026, 2, 1))
        row = self.rows()["PM-COMP"]
        # Expected: nothing left, so the block ceased: loss 100,000, closing 0. A block that carries a scrapped
        # machine's cost forward, depreciating it every year, is wrong.
        self.assertEqual((row.ceased, row.closing, row.capital_gain), (True, Decimal("0"), Decimal("-100000")))

    def test_T02_two_blocks_two_rates_are_independent(self):
        self.opening("PM-GEN", "100000")
        self.opening("PM-COMP", "100000")
        self.asset("PM-GEN", "100000", D(2025, 5, 1))
        self.asset("PM-COMP", "60000", D(2025, 5, 1))
        rows = self.rows()
        self.assertEqual(rows["PM-GEN"].depreciation, Decimal("30000.00"))
        self.assertEqual(rows["PM-COMP"].depreciation, Decimal("64000.00"))

    def test_T03_a_filed_year_foots_with_the_year_before_it(self):
        # Y2 (2025-26) is filed while 2024-25 has no figures; then an asset is put to use in 2024-25.
        self.opening("PM-GEN", "100000")  # opens 2025-26
        self.asset("PM-GEN", "50000", D(2025, 6, 1))
        self.confirm_all()
        TaxDepreciationYear.file(YEAR)
        before = TaxBlockLine.objects.get(year__year_start=YEAR, block__code="PM-GEN")
        late = FixedAsset.objects.create(name="late", category=self.category("PM-GEN"), acquisition_date=D(2024, 10, 1),
                                         cost=Decimal("77000"), life_months=120)
        late.place_in_service(on_date=D(2024, 10, 1))  # a day in an earlier, unfiled year
        # The filed year opens at what the year before it closes at, or the schedule does not foot.
        y1 = {r.block.code: r for r in schedule(D(2024, 4, 1))}["PM-GEN"]
        self.assertEqual(before.opening, y1.closing)

    def test_T04_file_while_a_disposal_is_made(self):
        # single-thread view of the same claim: a disposal made after filing is refused (the guard exists).
        self.the_year()
        self.confirm_all()
        TaxDepreciationYear.file(YEAR)
