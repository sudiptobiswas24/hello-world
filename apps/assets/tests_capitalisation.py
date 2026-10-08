"""
Capitalising a bill line, undoing it, and what each leaves the other.

The undo was a dead end: an un-capitalised asset still counted as the
line's, so the line could never be capitalised again, into the right
category or at all. And a debit note on a line whose machine had gone
into service was refused with "once in service, dispose of them — then
the bill can be debited", which stayed refused after the disposal.

The lathe: 12,000 from a bill dated 1 January 2026, into plant (1500)
out of purchases (5000).
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import Account, AccountType, JournalLine

from .models import AssetCategory, AssetStatus, asset_register
from .tests import CapitalisationFixture


class WithVehicles(CapitalisationFixture):
    def setUp(self):
        super().setUp()
        self.vehicles_account = Account.objects.create(code="1520", name="Vehicles", account_type=AccountType.ASSET)
        self.vehicles = AssetCategory.objects.create(
            code="VEH", name="Vehicles", asset_account=self.vehicles_account, accumulated_account=self.accumulated,
            expense_account=self.depreciation, disposal_account=self.disposal, default_life_months=12)


class CapitalisedAgainAfterTheUndoTests(WithVehicles):
    def undone(self):
        line = self.bill_line("1", "12000")
        (first,) = line.capitalise_as_asset(self.category)
        first.uncapitalise(on_date=datetime.date(2026, 1, 5))
        return line, first

    def test_a_line_whose_capitalisation_was_undone_is_capitalised_again(self):
        line, first = self.undone()
        (second,) = line.capitalise_as_asset(self.vehicles, name="Forklift")
        first.refresh_from_db()
        self.assertEqual(
            (first.status, second.status, second.cost, self.balance(self.plant), self.balance(self.vehicles_account),
             self.balance(self.expense), line.amount_in_its_account()),
            (AssetStatus.CANCELLED, AssetStatus.DRAFT, Decimal("12000.00"), Decimal("0"), Decimal("12000.00"),
             Decimal("0"), Decimal("0.00")))

    def test_once_capitalised_again_it_is_not_capitalised_a_third_time(self):
        line, _ = self.undone()
        line.capitalise_as_asset(self.vehicles)
        with self.assertRaisesMessage(ValidationError, "already been capitalised"):
            line.capitalise_as_asset(self.category)

    def test_the_one_capitalised_again_is_undone_in_its_turn_and_the_bill_then_debited(self):
        line, _ = self.undone()
        (second,) = line.capitalise_as_asset(self.vehicles, on_date=datetime.date(2026, 1, 7))
        second.uncapitalise(on_date=datetime.date(2026, 1, 9))
        self.assertEqual((self.balance(self.vehicles_account), self.balance(self.expense)),
                         (Decimal("0"), Decimal("12000.00")))
        line.bill.create_debit_note()
        self.assertEqual(self.balance(self.expense), Decimal("0"))

    def test_one_already_undone_is_told_so(self):
        _, first = self.undone()
        with self.assertRaisesMessage(ValidationError, "has already been un-capitalised"):
            first.uncapitalise(on_date=datetime.date(2026, 1, 6))


class CapitalisedAgainOnTheDayItIsDoneTests(WithVehicles):
    """
    Undone on 31 March and capitalised again into vehicles. Dated on its bill's 1 January, the
    second stood beside the first until March: plant 12,000, vehicles 12,000, purchases -12,000.
    """

    def as_of(self, account, day):
        rows = JournalLine.objects.filter(account=account, entry__posted=True, entry__date__lte=day).aggregate(
            debit=Sum("debit"), credit=Sum("credit"))
        return (rows["debit"] or Decimal("0")) - (rows["credit"] or Decimal("0"))

    def undone_in_march(self):
        line = self.bill_line("1", "12000")
        (first,) = line.capitalise_as_asset(self.category)
        first.uncapitalise(on_date=datetime.date(2026, 3, 31))
        return line, first

    def test_capitalised_again_it_stands_from_the_day_it_was_done_not_its_bills(self):
        line, first = self.undone_in_march()
        (second,) = line.capitalise_as_asset(self.vehicles)
        today = timezone.localdate()
        self.assertEqual(second.capitalisation_entry.date, today)
        february = datetime.date(2026, 2, 15)
        self.assertEqual(
            (self.as_of(self.plant, february), self.as_of(self.vehicles_account, february),
             self.as_of(self.expense, february), [row["asset"] for row in asset_register(february)]),
            (Decimal("12000.00"), Decimal("0"), Decimal("0.00"), [first]))
        self.assertEqual(
            (self.as_of(self.plant, today), self.as_of(self.vehicles_account, today),
             self.as_of(self.expense, today), [row["asset"] for row in asset_register(today)]),
            (Decimal("0.00"), Decimal("12000.00"), Decimal("0.00"), [second]))

    def test_not_capitalised_again_before_the_undo(self):
        line, _ = self.undone_in_march()
        with self.assertRaisesMessage(ValidationError, "is not capitalised again on 2026-03-30: its capitalisation "
                                                       "was undone on 2026-03-31"):
            line.capitalise_as_asset(self.vehicles, on_date=datetime.date(2026, 3, 30))
        self.assertEqual((line.assets.count(), self.balance(self.vehicles_account), self.balance(self.expense)),
                         (1, Decimal("0"), Decimal("12000.00")))

    def test_capitalised_again_it_goes_into_service_no_earlier_than_it_was_capitalised(self):
        line, _ = self.undone_in_march()
        (second,) = line.capitalise_as_asset(self.vehicles, on_date=datetime.date(2026, 4, 1))
        with self.assertRaisesMessage(ValidationError, "it was capitalised on 2026-04-01"):
            second.place_in_service(datetime.date(2026, 2, 1))
        second.refresh_from_db()
        self.assertEqual(second.status, AssetStatus.DRAFT)
        second.place_in_service(datetime.date(2026, 4, 1))
        self.assertEqual(second.status, AssetStatus.IN_SERVICE)

    def test_a_first_capitalisation_is_not_dated_away_from_its_bill(self):
        line = self.bill_line("1", "12000")
        with self.assertRaisesMessage(ValidationError, "is capitalised on 2026-01-01"):
            line.capitalise_as_asset(self.category, on_date=datetime.date(2026, 2, 1))
        self.assertEqual(line.assets.count(), 0)


class ADebitNoteOnALineCapitalisedTests(CapitalisationFixture):
    def test_the_refusal_after_a_disposal_says_the_bill_cannot_be_debited_for_it(self):
        line = self.bill_line("1", "12000")
        (asset,) = line.capitalise_as_asset(self.category)
        asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        asset.dispose(on_date=datetime.date(2026, 2, 10))
        refused = self.as_role("AP Manager").post(f"/api/purchasing/bills/{line.bill.pk}/debit_note/", {},
                                                  format="json")
        said = str(refused.json())
        self.assertEqual((refused.status_code, "cannot be debited for it" in said, "dispose of them" in said,
                          self.balance(self.expense)),
                         (400, True, False, Decimal("0")))
