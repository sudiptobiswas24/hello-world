"""
Fixed assets.

Buying a machine is not buying stock and not incurring an expense. It is
exchanging cash for something useful for years, and the cost belongs on
the balance sheet until those years consume it. Without this a capital
purchase had two bad homes: inventory, where it would be relieved on a
sale that never comes, or an expense, which puts a decade of value into
one month's profit.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

from apps.accounting.models import Account, AccountType, JournalLine
from apps.core.models import Company, Currency, Party, PartyRole, PartyRoleAssignment

from .models import (
    AssetCategory,
    AssetStatus,
    DepreciationMethod,
    FixedAsset,
    asset_register,
)


class AssetTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        Company.objects.create(name="Test Co", base_currency=self.usd)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.plant = acc("1500", "Plant", AccountType.ASSET)
        self.accumulated = acc("1590", "Accumulated depreciation", AccountType.ASSET)
        self.depreciation = acc("6100", "Depreciation", AccountType.EXPENSE)
        self.disposal = acc("6200", "Disposal", AccountType.EXPENSE)
        self.category = AssetCategory.objects.create(
            code="PLANT", name="Plant and machinery",
            asset_account=self.plant, accumulated_account=self.accumulated,
            expense_account=self.depreciation, disposal_account=self.disposal,
            default_life_months=12,
        )

    def asset(self, cost="12000", salvage="0", life=12, in_service=True):
        asset = FixedAsset.objects.create(
            name="Lathe", category=self.category,
            acquisition_date=datetime.date(2026, 1, 1),
            cost=Decimal(cost), salvage_value=Decimal(salvage), life_months=life,
        )
        if in_service:
            asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        return asset

    def balance(self, account):
        rows = JournalLine.objects.filter(account=account, entry__posted=True).aggregate(
            debit=Sum("debit"), credit=Sum("credit")
        )
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))


class LifecycleTests(AssetTestCase):
    def test_placing_in_service_numbers_it(self):
        asset = self.asset()
        self.assertEqual(asset.number, "FA-2026-00001")
        self.assertEqual(asset.status, AssetStatus.IN_SERVICE)

    def test_a_draft_has_no_number(self):
        self.assertEqual(self.asset(in_service=False).number, "")

    def test_salvage_must_be_below_cost(self):
        asset = FixedAsset(
            name="X", category=self.category, acquisition_date=datetime.date(2026, 1, 1),
            cost=Decimal("100"), salvage_value=Decimal("100"), life_months=12,
        )
        with self.assertRaisesMessage(ValidationError, "below cost"):
            asset.clean()

    def test_it_cannot_be_in_service_before_it_was_acquired(self):
        asset = FixedAsset(
            name="X", category=self.category, acquisition_date=datetime.date(2026, 6, 1),
            in_service_date=datetime.date(2026, 1, 1),
            cost=Decimal("100"), life_months=12,
        )
        with self.assertRaisesMessage(ValidationError, "before it was acquired"):
            asset.clean()

    def test_a_draft_asset_is_not_depreciated(self):
        asset = self.asset(in_service=False)
        with self.assertRaisesMessage(ValidationError, "Only an asset in service"):
            asset.depreciate(through=datetime.date(2026, 6, 30))


class DepreciationTests(AssetTestCase):
    def test_it_charges_a_month_at_a_time(self):
        """Each month is a period somebody closed; a lump posted to the
        current one misstates every month it covers."""
        asset = self.asset("12000", life=12)

        entries = asset.depreciate(through=datetime.date(2026, 3, 31))

        self.assertEqual(len(entries), 3)
        self.assertEqual([entry.amount for entry in entries], [Decimal("1000.00")] * 3)
        self.assertEqual(
            [entry.period_end for entry in entries],
            [datetime.date(2026, 1, 31), datetime.date(2026, 2, 28),
             datetime.date(2026, 3, 31)],
        )

    def test_it_posts_expense_against_accumulated(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 3, 31))

        self.assertEqual(self.balance(self.depreciation), Decimal("3000"))
        self.assertEqual(self.balance(self.accumulated), Decimal("-3000"))

    def test_running_it_twice_charges_nothing_extra(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 3, 31))

        self.assertEqual(asset.depreciate(through=datetime.date(2026, 3, 31)), [])
        self.assertEqual(asset.accumulated(), Decimal("3000"))

    def test_it_catches_up_missed_months(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 1, 31))

        entries = asset.depreciate(through=datetime.date(2026, 5, 31))

        self.assertEqual(len(entries), 4)
        self.assertEqual(asset.accumulated(), Decimal("5000"))

    def test_it_never_depreciates_past_salvage(self):
        """The last month takes whatever is left rather than the full
        charge."""
        asset = self.asset("10000", salvage="1000", life=12)

        asset.depreciate(through=datetime.date(2027, 12, 31))

        self.assertEqual(asset.accumulated(), Decimal("9000"))
        self.assertEqual(asset.net_book_value(), Decimal("1000"))

    def test_a_finished_asset_charges_nothing_more(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2027, 12, 31))
        self.assertEqual(asset.depreciate(through=datetime.date(2028, 12, 31)), [])

    def test_a_non_depreciating_category_charges_nothing(self):
        land = AssetCategory.objects.create(
            code="LAND", name="Land", asset_account=self.plant,
            accumulated_account=self.accumulated, expense_account=self.depreciation,
            method=DepreciationMethod.NONE,
        )
        asset = FixedAsset.objects.create(
            name="Yard", category=land, acquisition_date=datetime.date(2026, 1, 1),
            cost=Decimal("50000"), life_months=0,
        )
        asset.place_in_service()

        self.assertEqual(asset.depreciate(through=datetime.date(2027, 1, 1)), [])
        self.assertEqual(asset.net_book_value(), Decimal("50000"))

    def test_depreciation_starts_from_service_not_acquisition(self):
        """A machine in a crate is not being consumed."""
        asset = FixedAsset.objects.create(
            name="Press", category=self.category,
            acquisition_date=datetime.date(2026, 1, 1),
            cost=Decimal("12000"), life_months=12,
        )
        asset.place_in_service(on_date=datetime.date(2026, 4, 1))

        entries = asset.depreciate(through=datetime.date(2026, 6, 30))

        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].period_end, datetime.date(2026, 4, 30))


class DisposalTests(AssetTestCase):
    def test_selling_at_book_value_makes_neither_gain_nor_loss(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 6, 30))
        plant_before = self.balance(self.plant)

        asset.dispose(on_date=datetime.date(2026, 7, 1), proceeds=Decimal("6000"))

        # Creating an asset posts nothing — it cannot know what paid for
        # it — so only capitalisation debits the asset account. Disposal
        # takes the cost back off, whatever put it there.
        self.assertEqual(self.balance(self.plant) - plant_before, Decimal("-12000"))
        self.assertEqual(self.balance(self.accumulated), Decimal("0"))
        self.assertEqual(self.balance(self.disposal), Decimal("6000"))

    def test_selling_above_book_value_is_a_gain(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 6, 30))

        asset.dispose(on_date=datetime.date(2026, 7, 1), proceeds=Decimal("7000"))

        # Proceeds 7000 debited, gain 1000 credited: 6000 net, the book value.
        self.assertEqual(self.balance(self.disposal), Decimal("6000"))
        entry = asset.disposal_entry
        self.assertEqual(sum(l.debit for l in entry.lines.all()),
                         sum(l.credit for l in entry.lines.all()))

    def test_scrapping_writes_off_what_is_left(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 6, 30))

        asset.dispose(on_date=datetime.date(2026, 7, 1))

        self.assertEqual(self.balance(self.disposal), Decimal("6000"))
        self.assertEqual(asset.status, AssetStatus.DISPOSED)

    def test_the_entry_always_balances(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 4, 30))
        asset.dispose(on_date=datetime.date(2026, 5, 1), proceeds=Decimal("2000"))

        entry = asset.disposal_entry
        self.assertEqual(sum(l.debit for l in entry.lines.all()),
                         sum(l.credit for l in entry.lines.all()))

    def test_it_cannot_be_disposed_twice(self):
        asset = self.asset("12000", life=12)
        asset.dispose(on_date=datetime.date(2026, 5, 1))
        with self.assertRaisesMessage(ValidationError, "already been disposed"):
            asset.dispose(on_date=datetime.date(2026, 6, 1))

    def test_a_category_with_no_disposal_account_is_refused(self):
        """A gain or loss with nowhere to go would hide in depreciation."""
        self.category.disposal_account = None
        self.category.save()
        asset = self.asset("12000", life=12)

        with self.assertRaisesMessage(ValidationError, "no disposal account"):
            asset.dispose()


class RegisterTests(AssetTestCase):
    def test_it_reports_cost_depreciation_and_book_value(self):
        asset = self.asset("12000", life=12)
        asset.depreciate(through=datetime.date(2026, 3, 31))

        row = asset_register()[0]

        self.assertEqual(row["cost"], Decimal("12000"))
        self.assertEqual(row["accumulated"], Decimal("3000"))
        self.assertEqual(row["net_book_value"], Decimal("9000"))

    def test_drafts_are_left_out(self):
        self.asset(in_service=False)
        self.assertEqual(asset_register(), [])

    def test_a_disposed_asset_leaves_the_register(self):
        asset = self.asset("12000", life=12)
        asset.dispose(on_date=datetime.date(2026, 5, 1))
        self.assertEqual(asset_register(as_of=datetime.date(2026, 6, 1)), [])


class CapitalisationTests(AssetTestCase):
    def setUp(self):
        super().setUp()
        from apps.core.models import UnitOfMeasure
        from apps.inventory.models import Item

        self.uom = UnitOfMeasure.objects.create(code="ea", name="Each")
        self.item = Item.objects.create(sku="MACH", name="Machine", uom=self.uom)
        self.payable = Account.objects.create(
            code="2000", name="AP", account_type=AccountType.LIABILITY
        )
        self.expense = Account.objects.create(
            code="5000", name="Purchases", account_type=AccountType.EXPENSE
        )
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def bill_line(self, quantity="1", price="12000"):
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 1),
            payable_account=self.payable, currency=self.usd,
        )
        line = BillLine.objects.create(
            bill=bill, description="Machine", quantity=Decimal(quantity),
            unit_price=Decimal(price), expense_account=self.expense,
        )
        bill.post()
        return line

    def test_capitalising_moves_the_cost_onto_the_asset_account(self):
        line = self.bill_line("1", "12000")

        assets = line.capitalise_as_asset(self.category)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].cost, Decimal("12000"))
        self.assertEqual(self.balance(self.plant), Decimal("12000"))
        self.assertEqual(self.balance(self.expense), Decimal("0"))

    def test_each_unit_becomes_its_own_asset(self):
        """Assets are tracked, disposed of and depreciated individually."""
        line = self.bill_line("3", "4000")

        assets = line.capitalise_as_asset(self.category)

        self.assertEqual(len(assets), 3)
        self.assertEqual(sum(asset.cost for asset in assets), Decimal("12000"))

    def test_a_rounding_remainder_lands_on_the_last_one(self):
        line = self.bill_line("3", "3333.34")
        assets = line.capitalise_as_asset(self.category)
        self.assertEqual(sum(asset.cost for asset in assets), Decimal("10000.02"))

    def test_the_asset_remembers_the_purchase(self):
        line = self.bill_line("1", "12000")
        asset = line.capitalise_as_asset(self.category)[0]

        self.assertEqual(asset.bill_line, line)
        self.assertEqual(asset.vendor, self.vendor)
        self.assertEqual(asset.acquisition_date, datetime.date(2026, 1, 1))

    def test_it_cannot_be_capitalised_twice(self):
        line = self.bill_line("1", "12000")
        line.capitalise_as_asset(self.category)
        with self.assertRaisesMessage(ValidationError, "already been capitalised"):
            line.capitalise_as_asset(self.category)

    def test_a_fractional_quantity_is_refused(self):
        line = self.bill_line("1.5", "1000")
        with self.assertRaisesMessage(ValidationError, "whole units"):
            line.capitalise_as_asset(self.category)

    def test_the_category_default_life_is_used(self):
        line = self.bill_line("1", "12000")
        asset = line.capitalise_as_asset(self.category)[0]
        self.assertEqual(asset.life_months, 12)
