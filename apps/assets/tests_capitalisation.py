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

from apps.accounting.models import Account, AccountType

from .models import AssetCategory, AssetStatus
from .tests import CapitalisationFixture


class CapitalisedAgainAfterTheUndoTests(CapitalisationFixture):
    def setUp(self):
        super().setUp()
        self.vehicles_account = Account.objects.create(code="1520", name="Vehicles", account_type=AccountType.ASSET)
        self.vehicles = AssetCategory.objects.create(
            code="VEH", name="Vehicles", asset_account=self.vehicles_account, accumulated_account=self.accumulated,
            expense_account=self.depreciation, disposal_account=self.disposal, default_life_months=12)

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
        (second,) = line.capitalise_as_asset(self.vehicles)
        second.uncapitalise(on_date=datetime.date(2026, 1, 9))
        self.assertEqual((self.balance(self.vehicles_account), self.balance(self.expense)),
                         (Decimal("0"), Decimal("12000.00")))
        line.bill.create_debit_note()
        self.assertEqual(self.balance(self.expense), Decimal("0"))

    def test_one_already_undone_is_told_so(self):
        _, first = self.undone()
        with self.assertRaisesMessage(ValidationError, "has already been un-capitalised"):
            first.uncapitalise(on_date=datetime.date(2026, 1, 6))


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
