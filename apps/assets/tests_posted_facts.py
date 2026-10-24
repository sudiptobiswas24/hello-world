"""
What an asset posted is a fact it keeps, not something read again from
its category later.

A category says what the next asset gets. Read live, it moved assets
already on the books: a category given new accounts had its lathe's
disposal take 12,000 off an account that never held it, leaving the old
plant account at 12,000 and the old accumulated account at -3,000 for
good; a category switched to not depreciated stopped a lathe in service
with 9,000 still to charge.

The lathe: 12,000 from a bill dated 1 January 2026, twelve months at
1,000, in service from 1 January.
"""

import contextlib
import datetime
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, tag

from apps.accounting.models import Account, AccountType

from .models import AssetCategory, DepreciationMethod, FixedAsset
from .tests import CapitalisationFixture

MAR_31 = datetime.date(2026, 3, 31)
JUN_30 = datetime.date(2026, 6, 30)


class CategoryMovedTestCase(CapitalisationFixture):
    def setUp(self):
        super().setUp()
        self.new_plant = Account.objects.create(code="1510", name="Plant (new)", account_type=AccountType.ASSET)
        self.new_accumulated = Account.objects.create(code="1595", name="Accumulated (new)",
                                                      account_type=AccountType.ASSET)
        self.controller = self.as_role("Controller")

    def lathe(self):
        (asset,) = self.bill_line("1", "12000").capitalise_as_asset(self.category)
        asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        return asset

    def move_the_category(self, asset, **changes):
        """The category changed over the API; the asset as the next request reads it."""
        changes = changes or {"asset_account": self.new_plant.pk, "accumulated_account": self.new_accumulated.pk}
        response = self.controller.patch(f"/api/assets/categories/{self.category.pk}/", changes, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return FixedAsset.objects.get(pk=asset.pk)

    def books(self):
        return tuple(self.balance(account) for account in
                     (self.plant, self.accumulated, self.new_plant, self.new_accumulated))


class TheAccountsAnAssetStandsOnTests(CategoryMovedTestCase):
    def test_its_disposal_clears_the_accounts_it_was_put_on_not_the_categorys_new_ones(self):
        asset = self.lathe()
        asset.depreciate(through=MAR_31)
        self.move_the_category(asset)
        gone = self.controller.post(f"/api/assets/assets/{asset.pk}/dispose/", {"on_date": "2026-04-01"},
                                    format="json")
        self.assertEqual(gone.status_code, 200, gone.content)
        self.assertEqual(self.books() + (self.balance(self.disposal),),
                         (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("9000.00")))

    def test_months_charged_after_the_move_are_held_with_the_months_before(self):
        asset = self.lathe()
        asset.depreciate(through=MAR_31)
        asset = self.move_the_category(asset)
        asset.depreciate(through=JUN_30)
        self.assertEqual(self.books()[1:], (Decimal("-6000.00"), Decimal("0"), Decimal("0")))
        asset.dispose(on_date=datetime.date(2026, 7, 1))
        self.assertEqual(self.books() + (self.balance(self.depreciation), self.balance(self.disposal)),
                         (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("6000.00"),
                          Decimal("6000.00")))

    def test_one_reinstated_after_the_move_goes_back_where_it_stood(self):
        asset = self.lathe()
        asset.depreciate(through=MAR_31)
        asset.dispose(on_date=datetime.date(2026, 6, 15))
        asset = self.move_the_category(asset)
        asset.reinstate()
        asset.depreciate(through=JUN_30)
        self.assertEqual(self.books() + (self.balance(self.disposal),),
                         (Decimal("12000.00"), Decimal("-6000.00"), Decimal("0"), Decimal("0"), Decimal("0")))

    def test_one_put_in_service_after_the_move_stands_on_the_new_accounts(self):
        asset = FixedAsset.objects.create(name="Press", category=self.category, cost=Decimal("6000"),
                                          acquisition_date=datetime.date(2026, 1, 1), life_months=12)
        asset = self.move_the_category(asset)
        asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        asset.depreciate(through=datetime.date(2026, 1, 31))
        self.assertEqual((asset.asset_account, asset.accumulated_account, self.balance(self.new_accumulated)),
                         (self.new_plant, self.new_accumulated, Decimal("-500.00")))

    def test_what_it_stands_on_is_not_changed_once_in_service(self):
        asset = self.lathe()
        asset.accumulated_account = self.new_accumulated
        with self.assertRaisesMessage(ValidationError, "accumulated_account can no longer change"):
            asset.save()


class TheMethodAnAssetIsDepreciatedByTests(CategoryMovedTestCase):
    def test_a_category_switched_to_not_depreciated_does_not_stop_one_in_service(self):
        asset = self.lathe()
        asset.depreciate(through=MAR_31)
        asset = self.move_the_category(asset, method=DepreciationMethod.NONE)
        made = asset.depreciate(through=JUN_30)
        self.assertEqual(([row.amount for row in made], asset.accumulated()),
                         ([Decimal("1000.00")] * 3, Decimal("6000.00")))

    def test_a_draft_follows_its_category_until_it_goes_into_service(self):
        asset = FixedAsset.objects.create(name="Yard wall", category=self.category, cost=Decimal("6000"),
                                          acquisition_date=datetime.date(2026, 1, 1), life_months=12)
        asset = self.move_the_category(asset, method=DepreciationMethod.NONE)
        asset.place_in_service(on_date=datetime.date(2026, 1, 1))
        self.assertEqual((asset.method, asset.depreciate(through=JUN_30)), (DepreciationMethod.NONE, []))


class ACapitalisedDraftTests(CapitalisationFixture):
    """
    A draft from a bill has posted: its capitalisation put 12,000 on the
    plant account. Its status said draft, and the guard asked only that.
    """

    def setUp(self):
        super().setUp()
        self.controller = self.as_role("Controller")
        (self.lathe,) = self.bill_line("1", "12000").capitalise_as_asset(self.category)

    def patched(self, asset, **changes):
        return self.controller.patch(f"/api/assets/assets/{asset.pk}/", changes, format="json")

    def test_it_keeps_the_cost_its_capitalisation_posted(self):
        refused = self.patched(self.lathe, cost="15000.00")
        self.lathe.refresh_from_db()
        self.lathe.place_in_service(on_date=datetime.date(2026, 1, 1))
        self.lathe.dispose(on_date=datetime.date(2026, 1, 15))
        self.assertEqual((refused.status_code, self.lathe.cost, self.balance(self.plant)),
                         (400, Decimal("12000.00"), Decimal("0")))
        self.assertIn("settled by that entry", str(refused.json()))

    def test_nor_is_it_moved_to_another_category(self):
        vehicles = Account.objects.create(code="1520", name="Vehicles", account_type=AccountType.ASSET)
        category = AssetCategory.objects.create(
            code="VEH", name="Vehicles", asset_account=vehicles, accumulated_account=self.accumulated,
            expense_account=self.depreciation, disposal_account=self.disposal, default_life_months=12)
        refused = self.patched(self.lathe, category=category.pk)
        lathe = FixedAsset.objects.get(pk=self.lathe.pk)
        lathe.place_in_service(on_date=datetime.date(2026, 1, 1))
        lathe.dispose(on_date=datetime.date(2026, 1, 15))
        self.assertEqual((refused.status_code, self.balance(self.plant), self.balance(vehicles)),
                         (400, Decimal("0"), Decimal("0")))

    def test_nor_is_its_acquisition_date_moved_off_the_bills(self):
        self.assertEqual(self.patched(self.lathe, acquisition_date="2026-02-01").status_code, 400)

    def test_nor_is_it_given_depreciation_an_old_system_took(self):
        refused = self.patched(self.lathe, depreciated_before="2026-03-31", opening_depreciation="3000.00")
        self.assertEqual((refused.status_code, FixedAsset.objects.get(pk=self.lathe.pk).opening_depreciation),
                         (400, Decimal("0.00")))

    def test_what_its_capitalisation_did_not_settle_still_changes(self):
        changed = self.patched(self.lathe, name="Lathe No. 2", life_months=24, salvage_value="1000.00",
                               in_service_date="2026-02-01")
        self.assertEqual(changed.status_code, 200, changed.content)

    def test_a_draft_typed_in_by_hand_is_still_repriced(self):
        draft = FixedAsset.objects.create(name="Press", category=self.category, cost=Decimal("6000"),
                                          acquisition_date=datetime.date(2026, 1, 1), life_months=12)
        self.assertEqual(self.patched(draft, cost="6500.00").status_code, 200)

class TheAuditAsksItTests(SimpleTestCase):
    """
    `manage.py audit_invariants` reports an entry kept past a guard keyed to
    the status, and a setting the asset keeps read live from its category.
    """

    def findings(self, check, edit=lambda text: text):
        from apps.core.management.commands.audit_invariants import Command, app_sources

        sources = app_sources()
        sources["assets"] = {path: edit(text) if path.name == "models.py" else text
                             for path, text in sources["assets"].items()}
        return [detail for _, detail in getattr(Command(), check)(["assets"], sources)]

    def test_a_guard_that_forgets_the_capitalisation_is_reported(self):
        (said,) = self.findings("entries_kept_past_the_edit_guard",
                                lambda text: text.replace("previous.capitalisation_entry_id", "previous.pk"))
        self.assertIn("assets.FixedAsset.capitalisation_entry is set by BillLine.capitalise_as_asset", said)

    def test_a_disposal_reading_its_categorys_account_is_reported(self):
        (said,) = self.findings("kept_settings_read_live", lambda text: text.replace(
            "entry=entry, account=self.asset_account,", "entry=entry, account=self.category.asset_account,"))
        self.assertIn("keeps its own asset_account", said)

    def test_the_asset_as_it_stands_is_not(self):
        self.assertEqual(self.findings("entries_kept_past_the_edit_guard") + self.findings("kept_settings_read_live"),
                         [])

# Slow: it unwinds the later migrations and replays them.
@tag("migration")
class AssetsAlreadyOnTheBooksMigrationTests(TransactionTestCase):
    """
    Upgraded, an asset already on the books keeps what it stands on: the
    asset account its capitalisation debited, and the accumulated account
    its charges credited, even where its category has moved since; straight
    line where it has charges, though its category now says not depreciated;
    otherwise its category's, as every entry of it read until now. A draft
    from a bill keeps its asset account; one typed in keeps nothing until it
    goes into service. What the books cannot tell is printed for the keeper.
    """

    before = [("assets", "0006_disposal_in_error_reinstated")]
    after = [("assets", "0007_what_each_asset_stands_on")]

    def test_each_keeps_what_it_stands_on(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        # Only assets is taken back; the accounts and entries are made as they stand now, by id.
        now = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        account = now.get_model("accounting", "Account")
        old_plant, new_plant, accumulated, expense, purchases = (
            account.objects.create(code=code, name=code, account_type=kind) for code, kind in (
                ("1500", "asset"), ("1510", "asset"), ("1590", "asset"), ("6100", "expense"),
                ("5000", "expense")))
        category = apps.get_model("assets", "AssetCategory").objects.create(
            code="PLANT", name="Plant", asset_account_id=new_plant.pk, accumulated_account_id=accumulated.pk,
            expense_account_id=expense.pk, method="straight_line")
        entry = now.get_model("accounting", "JournalEntry").objects.create(date=datetime.date(2026, 1, 1))
        line = now.get_model("accounting", "JournalLine")
        line.objects.create(entry=entry, account=old_plant, debit=Decimal("12000"))
        line.objects.create(entry=entry, account=purchases, credit=Decimal("12000"))
        draft_entry = now.get_model("accounting", "JournalEntry").objects.create(date=datetime.date(2026, 1, 1))
        line.objects.create(entry=draft_entry, account=old_plant, debit=Decimal("500"))
        line.objects.create(entry=draft_entry, account=purchases, credit=Decimal("500"))
        asset = apps.get_model("assets", "FixedAsset")

        def made(name, status, capitalisation=None):
            return asset.objects.create(name=name, category=category, status=status, cost=Decimal("12000"),
                                        acquisition_date=datetime.date(2026, 1, 1), life_months=12,
                                        capitalisation_entry_id=capitalisation)

        made("From a bill", "in_service", entry.pk)
        made("Typed in", "in_service")
        made("Drafted from a bill", "draft", draft_entry.pk)
        made("Drafted by hand", "draft")

        # A category moved before the upgrade: to 1595, and to not depreciated. The lathe was
        # charged January to March onto 1590; read off the category it was kept on 1595, and its
        # disposal would have left 1590 holding 3,000, with the lathe stopped mid-life.
        moved_accumulated = account.objects.create(code="1595", name="1595", account_type="asset")
        moved = apps.get_model("assets", "AssetCategory").objects.create(
            code="MOVED", name="Moved", asset_account_id=new_plant.pk, accumulated_account_id=moved_accumulated.pk,
            expense_account_id=expense.pk, method="none")
        charge = apps.get_model("assets", "DepreciationEntry")

        def charged(fixed, month, onto):
            day = datetime.date(2026, month + 1, 1) - datetime.timedelta(days=1)
            posted = now.get_model("accounting", "JournalEntry").objects.create(date=day)
            line.objects.create(entry=posted, account=expense, debit=Decimal("1000"))
            line.objects.create(entry=posted, account=onto, credit=Decimal("1000"))
            charge.objects.create(asset=fixed, period_end=day, amount=Decimal("1000"), journal_entry_id=posted.pk)

        def numbered(name, number, **more):
            return asset.objects.create(name=name, number=number, category=moved, status="in_service",
                                        cost=Decimal("12000"), acquisition_date=datetime.date(2026, 1, 1),
                                        in_service_date=datetime.date(2026, 1, 1), life_months=12, **more)

        lathe = numbered("Moved lathe", "FA-2026-00010")
        for month in (1, 2, 3):
            charged(lathe, month, accumulated)
        split = numbered("Split lathe", "FA-2026-00011")
        charged(split, 1, accumulated)
        charged(split, 2, moved_accumulated)
        numbered("Old loom", "FA-2026-00012", opening_depreciation=Decimal("3000"),
                 depreciated_before=datetime.date(2026, 1, 31))
        numbered("Land", "FA-2026-00013")

        printed = io.StringIO()
        executor = MigrationExecutor(connection)
        with contextlib.redirect_stdout(printed):
            executor.migrate(self.after)
        asset = executor.loader.project_state(self.after).apps.get_model("assets", "FixedAsset")
        self.assertEqual(
            sorted(asset.objects.values_list("name", "asset_account_id", "accumulated_account_id", "method")),
            [("Drafted by hand", None, None, ""), ("Drafted from a bill", old_plant.pk, None, ""),
             ("From a bill", old_plant.pk, accumulated.pk, "straight_line"),
             ("Land", new_plant.pk, moved_accumulated.pk, "none"),
             ("Moved lathe", new_plant.pk, accumulated.pk, "straight_line"),
             ("Old loom", new_plant.pk, moved_accumulated.pk, "none"),
             ("Split lathe", new_plant.pk, moved_accumulated.pk, "straight_line"),
             ("Typed in", new_plant.pk, accumulated.pk, "straight_line")])
        told = printed.getvalue()
        self.assertIn("FA-2026-00011 Split lathe: its charges were credited to 1590, 1595; it is kept on 1595", told)
        self.assertIn("FA-2026-00012 Old loom: its category says not depreciated", told)
        self.assertNotIn("FA-2026-00010", told)
        self.assertNotIn("FA-2026-00013", told)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
