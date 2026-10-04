"""
What an audit of the assets module found before it got an API.

A front door on these would have exposed each of them rather than fixed
them: a register that ignored the date it was asked for, rules that held
only when somebody called `clean()`, a disposal that hid missed months
in the loss, a foreign machine capitalised at its foreign price, and no
way back from capitalising a bill line.

The fixture's lathe costs 12,000 over twelve months, 1,000 a month,
in service from 1 January 2026.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import Currency, ExchangeRate

from .models import AssetStatus, FixedAsset, asset_register
from .tests import AssetTestCase, CapitalisationFixture

MAR_31 = datetime.date(2026, 3, 31)
JUN_30 = datetime.date(2026, 6, 30)


class TheRegisterAsItStoodTests(AssetTestCase):
    def test_depreciation_is_counted_through_the_date_asked(self):
        """Six months charged; as at March, three of them had been."""
        asset = self.asset()
        asset.depreciate(through=JUN_30)
        (row,) = asset_register(as_of=MAR_31)
        self.assertEqual(row["accumulated"], Decimal("3000"))
        self.assertEqual(row["net_book_value"], Decimal("9000"))
        (today,) = asset_register(as_of=JUN_30)
        self.assertEqual(today["accumulated"], Decimal("6000"))

    def test_an_asset_bought_after_the_date_is_not_on_it(self):
        self.asset()
        later = FixedAsset.objects.create(
            name="Press", category=self.category,
            acquisition_date=datetime.date(2026, 5, 1),
            cost=Decimal("6000"), life_months=12,
        )
        later.place_in_service()
        self.assertEqual(len(asset_register(as_of=MAR_31)), 1)
        self.assertEqual(len(asset_register(as_of=JUN_30)), 2)


class TheRulesHoldWhereverAnAssetIsMadeTests(AssetTestCase):
    def test_salvage_at_cost_is_refused_on_save(self):
        with self.assertRaisesMessage(ValidationError, "below cost"):
            self.asset(cost="1000", salvage="1000", in_service=False)

    def test_and_by_the_table_where_save_is_skipped(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FixedAsset.objects.bulk_create([FixedAsset(
                name="Bad", category=self.category,
                acquisition_date=datetime.date(2026, 1, 1),
                cost=Decimal("1000"), salvage_value=Decimal("1000"),
                life_months=12,
            )])

    def test_in_service_before_acquisition_is_refused_on_save(self):
        with self.assertRaisesMessage(ValidationError, "before it was acquired"):
            FixedAsset.objects.create(
                name="Bad", category=self.category,
                acquisition_date=datetime.date(2026, 3, 1),
                in_service_date=datetime.date(2026, 2, 1),
                cost=Decimal("1000"), life_months=12,
            )

    def test_nor_can_it_be_put_into_service_before_it_arrived(self):
        asset = self.asset(in_service=False)
        with self.assertRaisesMessage(ValidationError, "before it was acquired"):
            asset.place_in_service(on_date=datetime.date(2025, 12, 1))


class ADisposalChargesWhatWasDueTests(AssetTestCase):
    def test_months_not_yet_charged_are_charged_first(self):
        """
        Charged to March, sold on 15 June: April and May are charged
        before it goes, 5,000 in all, and the book value leaving is
        7,000 — not 9,000 with two months' depreciation hidden in it.
        """
        asset = self.asset()
        asset.depreciate(through=MAR_31)
        asset.dispose(on_date=datetime.date(2026, 6, 15), proceeds=Decimal("0"))
        self.assertEqual(self.balance(self.depreciation), Decimal("5000"))
        self.assertEqual(self.balance(self.disposal), Decimal("7000"))


class ADisposalTakesBackMonthsAfterItTests(AssetTestCase):
    """
    Depreciation run to December, then the lathe recorded as sold on 15
    June. The disposal took off all twelve months as accumulated, so the
    loss read nothing and the profit and loss carried seven months of
    depreciation on a machine that was gone. Those charges are reversed
    on their own dates; the disposal then sees five months and a loss of
    7,000.
    """

    def test_months_after_the_disposal_are_reversed(self):
        asset = self.asset()
        asset.depreciate(through=datetime.date(2026, 12, 31))
        asset.dispose(on_date=datetime.date(2026, 6, 15))
        self.assertEqual(
            (asset.accumulated(), self.balance(self.depreciation), self.balance(self.disposal),
             self.balance(self.accumulated),
             asset.depreciation_entries.filter(reversal__isnull=False).count()),
            (Decimal("5000.00"), Decimal("5000.00"), Decimal("7000.00"), Decimal("0"), 7),
        )

    def test_each_reversal_lands_in_the_month_it_undoes(self):
        asset = self.asset()
        asset.depreciate(through=datetime.date(2026, 8, 31))
        asset.dispose(on_date=datetime.date(2026, 6, 15))
        self.assertEqual(
            [(e.period_end, e.reversal.date) for e in
             asset.depreciation_entries.filter(reversal__isnull=False)],
            [(JUN_30, JUN_30), (datetime.date(2026, 7, 31), datetime.date(2026, 7, 31)),
             (datetime.date(2026, 8, 31), datetime.date(2026, 8, 31))],
        )

    def test_a_disposal_on_the_last_day_takes_back_that_month_too(self):
        """
        A disposal charges up to the month before it, never its own month,
        whatever day it falls on. A June charge already run is reversed on
        30 June just as it would be on the 15th.
        """
        asset = self.asset()
        asset.depreciate(through=JUN_30)
        asset.dispose(on_date=JUN_30)
        self.assertEqual((asset.accumulated(), self.balance(self.disposal)),
                         (Decimal("5000.00"), Decimal("7000.00")))

    def test_the_register_before_the_disposal_still_shows_what_was_charged(self):
        asset = self.asset()
        asset.depreciate(through=datetime.date(2026, 12, 31))
        asset.dispose(on_date=datetime.date(2026, 6, 15))
        self.assertEqual(asset.accumulated(as_of=MAR_31), Decimal("3000.00"))


class CapitalisingAndUndoingItTests(CapitalisationFixture):
    def test_a_foreign_machine_goes_on_at_base_currency(self):
        """€10,000 billed at 1.2 is 12,000 on the asset account."""
        eur = Currency.objects.create(code="EUR", name="Euro")
        ExchangeRate.objects.create(
            currency=eur, rate=Decimal("1.2"),
            valid_from=datetime.date(2025, 1, 1),
        )
        from apps.purchasing.models import Bill, BillLine

        bill = Bill.objects.create(
            vendor=self.vendor, bill_date=datetime.date(2026, 1, 1),
            payable_account=self.payable, currency=eur,
        )
        line = BillLine.objects.create(
            bill=bill, description="Machine", quantity=Decimal("1"),
            unit_price=Decimal("10000"), expense_account=self.expense,
        )
        bill.post()
        (asset,) = line.capitalise_as_asset(self.category)
        self.assertEqual(asset.cost, Decimal("12000.00"))
        self.assertEqual(self.balance(self.plant), Decimal("12000.00"))
        self.assertEqual(self.balance(self.expense), Decimal("0"))

    def test_a_draft_can_be_uncapitalised_and_the_bill_then_debited(self):
        line = self.bill_line("1", "12000")
        (asset,) = line.capitalise_as_asset(self.category)
        asset.uncapitalise(on_date=datetime.date(2026, 1, 5))
        self.assertEqual(asset.status, AssetStatus.CANCELLED)
        self.assertEqual(self.balance(self.plant), Decimal("0"))
        self.assertEqual(self.balance(self.expense), Decimal("12000"))
        line.bill.create_debit_note()
        self.assertEqual(self.balance(self.expense), Decimal("0"))
        self.assertEqual(asset_register(as_of=JUN_30), [])

    def test_a_capitalised_line_cannot_be_debited(self):
        """
        It would credit the expense account the capitalisation already
        emptied, and leave the machine on the books at full cost.
        """
        line = self.bill_line("1", "12000")
        line.capitalise_as_asset(self.category)
        with self.assertRaisesMessage(ValidationError, "Un-capitalise"):
            line.bill.create_debit_note()

    def test_an_asset_that_has_been_in_service_is_disposed_not_undone(self):
        line = self.bill_line("1", "12000")
        (asset,) = line.capitalise_as_asset(self.category)
        asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        with self.assertRaisesMessage(ValidationError, "disposal, not an undo"):
            asset.uncapitalise()

    def test_an_asset_not_from_a_bill_has_nothing_to_undo(self):
        asset = self.asset(in_service=False)
        with self.assertRaisesMessage(ValidationError, "delete the draft"):
            asset.uncapitalise()


class ADisposalAfterAMonthWasClosedTests(AssetTestCase):
    """
    Charged to September, September closed, then the lathe recorded as
    sold on 20 August. Reversing September's charge on 30 September was
    refused by the close, and with it the whole disposal: the asset had
    no way off the books. It is reversed on the disposal date instead;
    August's, still open, on its own month end.
    """

    def test_the_closed_month_is_reversed_on_the_disposal_date(self):
        from apps.accounting.models import AccountingPeriod

        asset = self.asset()
        asset.depreciate(through=datetime.date(2026, 9, 30))
        AccountingPeriod.objects.create(name="Sep 2026", start_date=datetime.date(2026, 9, 1),
                                        end_date=datetime.date(2026, 9, 30), closed=True)
        asset.dispose(on_date=datetime.date(2026, 8, 20))
        self.assertEqual(
            ([(e.period_end, e.reversal.date) for e in
              asset.depreciation_entries.filter(reversal__isnull=False)],
             asset.accumulated(), self.balance(self.disposal), self.balance(self.depreciation)),
            ([(datetime.date(2026, 8, 31), datetime.date(2026, 8, 31)),
              (datetime.date(2026, 9, 30), datetime.date(2026, 8, 20))],
             Decimal("7000.00"), Decimal("5000.00"), Decimal("7000.00")),
        )
