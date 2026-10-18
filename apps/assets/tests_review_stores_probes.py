"""
g22 review of fix_stores: probes for O29, O34, O12, O13, O14, O26 (assets, the period close, the books' opening).
Each asserts what SHOULD be true of the register, the ledger and who may act.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import (
    Account, AccountingPeriod, AccountType, BooksOpening, JournalEntry, JournalLine,
)
from apps.assets import tests as base
from apps.assets import tests_capitalisation as capbase
from apps.assets.models import AssetStatus, FixedAsset, register_against_ledger

D = Decimal
DAY = datetime.date


def only_mine(cls):
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


@only_mine
class StoresReviewAssetProbes(base.AssetTestCase):
    def foots(self, day=DAY(2026, 6, 30)):
        rows = register_against_ledger(day)
        self.assertEqual([r["difference"] for r in rows], [D("0.00")] * len(rows),
                         f"register vs ledger on {day}: {[(r['account'].code, r['register'], r['ledger']) for r in rows]}")

    def loom(self, acquired=DAY(2025, 4, 1), **kw):
        return FixedAsset.objects.create(name="Loom", category=self.category, acquisition_date=acquired,
                                         cost=D("12000"), life_months=48, **kw)

    # ---- O29
    def test_a1_a_period_with_depreciation_uncharged_does_not_close(self):
        self.asset()
        jan = AccountingPeriod.objects.create(name="Jan", start_date=DAY(2026, 1, 1), end_date=DAY(2026, 1, 31))
        with self.assertRaises(ValidationError):
            jan.close()

    def test_a2_the_admin_has_no_box_that_closes_a_period_around_the_check(self):
        from django.contrib import admin

        registered = admin.site._registry.get(AccountingPeriod)
        if registered is None:
            return
        editable = "closed" not in registered.get_readonly_fields(None)
        self.assertFalse(editable, "the admin's closed box closes a period without close()'s checks")

    def test_a3_a_period_closes_after_a_mid_period_disposal_and_the_ledger_foots(self):
        asset = self.asset()
        asset.depreciate(through=DAY(2026, 6, 30))
        asset.dispose(on_date=DAY(2026, 3, 15))
        feb = AccountingPeriod.objects.create(name="Feb", start_date=DAY(2026, 2, 1), end_date=DAY(2026, 2, 28))
        feb.close()
        self.foots()
        self.assertEqual(self.balance(self.plant), D("0"))
        self.assertEqual(self.balance(self.accumulated), D("0"))

    def test_a4_charged_then_taken_back_by_a_disposal_then_reinstated_owes_again(self):
        asset = self.asset()
        asset.depreciate(through=DAY(2026, 3, 31))
        asset.dispose(on_date=DAY(2026, 2, 10))
        asset.reinstate(on_date=DAY(2026, 2, 20))
        self.foots()
        mar = AccountingPeriod.objects.create(name="Mar", start_date=DAY(2026, 3, 1), end_date=DAY(2026, 3, 31))
        # whatever reinstate re-charged stands, so March closes; if it did not, the close must refuse
        try:
            mar.close()
        except ValidationError as error:
            self.assertIn("Depreciation is still to charge", " ".join(error.messages))
        else:
            self.assertEqual(asset.periods_due(DAY(2026, 3, 31)), [], "closed with a month still uncharged")

    def test_a5_reopen_then_close_again_asks_for_the_depreciation_again(self):
        asset = self.asset()
        asset.depreciate(through=DAY(2026, 1, 31))
        jan = AccountingPeriod.objects.create(name="Jan", start_date=DAY(2026, 1, 1), end_date=DAY(2026, 1, 31))
        jan.close()
        jan.reopen()
        # a second asset put into service in the reopened January
        second = FixedAsset.objects.create(name="Press", category=self.category, acquisition_date=DAY(2026, 1, 1),
                                           cost=D("6000"), life_months=12)
        second.place_in_service(on_date=DAY(2026, 1, 1))
        with self.assertRaises(ValidationError):
            jan.close()

    # ---- O34 and BooksOpening
    def test_a6_depreciation_never_posts_before_the_day_the_books_opened(self):
        loom = self.loom()   # in service Apr 2025, nothing declared as depreciated by the old system
        loom.place_in_service(on_date=DAY(2025, 4, 1))
        loom.depreciate(through=DAY(2026, 1, 31))
        early = JournalEntry.objects.filter(posted=True, date__lt=DAY(2026, 1, 1)).count()
        self.assertEqual(early, 0, "ten months of depreciation were posted before the books opened, "
                                   "after which the opening day can never be changed")

    def test_a7_assets_typed_in_before_on_and_after_the_opening_day(self):
        before = self.loom(acquired=DAY(2025, 12, 31))
        before.place_in_service(on_date=DAY(2025, 12, 31))
        on = self.loom(acquired=DAY(2026, 1, 1))
        on.place_in_service(on_date=DAY(2026, 1, 1))
        after = self.loom(acquired=DAY(2026, 1, 2))
        with self.assertRaises(ValidationError):
            after.place_in_service(on_date=DAY(2026, 1, 2))
        self.foots(DAY(2026, 1, 31))
        self.assertEqual(self.balance(self.plant), D("24000.00"))
        self.assertEqual(self.balance(self.opening), D("-24000.00"))

    def test_a8_the_opening_day_cannot_move_once_an_asset_stands_on_it_and_not_after_a_later_posting(self):
        self.loom().place_in_service(on_date=DAY(2025, 4, 1))
        for day in (DAY(2026, 1, 2), DAY(2025, 12, 31)):
            self.books_opened.opened_on = day
            with self.assertRaises(ValidationError):
                self.books_opened.save()
            self.books_opened.refresh_from_db()
        self.assertEqual(self.books_opened.opened_on, DAY(2026, 1, 1))
        # and the account the opening stands against
        other = Account.objects.create(code="3950", name="Other equity", account_type=AccountType.EQUITY)
        self.books_opened.opening_account = other
        with self.assertRaises(ValidationError):
            self.books_opened.save()

    def test_a9_the_books_cannot_be_opened_on_a_day_after_things_were_already_posted(self):
        BooksOpening.objects.all().delete()
        entry = JournalEntry.objects.create(date=DAY(2025, 11, 1), memo="Posted before the books were said to open")
        JournalLine.objects.create(entry=entry, account=self.plant, debit=D("5"))
        JournalLine.objects.create(entry=entry, account=self.opening, credit=D("5"))
        entry.post()
        with self.assertRaises(ValidationError, msg="BooksOpening created on 1 Jan 2026 with an entry posted on 1 Nov 2025"):
            BooksOpening.objects.create(opened_on=DAY(2026, 1, 1), opening_account=self.opening)

    def test_a10_removing_an_opening_asset_after_depreciation_disposal_or_twice_is_refused(self):
        loom = self.loom()
        loom.place_in_service(on_date=DAY(2025, 4, 1))
        loom.remove_opening()
        self.foots()
        self.assertEqual((self.balance(self.plant), self.balance(self.opening)), (D("0"), D("0")))
        with self.assertRaises(ValidationError):
            loom.remove_opening()
        second = self.loom(acquired=DAY(2025, 5, 1))
        second.place_in_service(on_date=DAY(2025, 5, 1))
        second.depreciate(through=DAY(2026, 2, 28))
        with self.assertRaises(ValidationError):
            second.remove_opening()
        second.dispose(on_date=DAY(2026, 3, 10))
        with self.assertRaises(ValidationError):
            second.remove_opening()

    def test_a12_the_health_check_names_a_posting_no_asset_accounts_for(self):
        from apps.web.health import _fixed_assets

        self.loom().place_in_service(on_date=DAY(2025, 4, 1))
        self.assertTrue(_fixed_assets(DAY(2026, 6, 30))["ok"])
        entry = JournalEntry.objects.create(date=DAY(2026, 2, 1), memo="Plant by journal")
        JournalLine.objects.create(entry=entry, account=self.plant, debit=D("500"))
        JournalLine.objects.create(entry=entry, account=self.opening, credit=D("500"))
        entry.post()
        self.assertFalse(_fixed_assets(DAY(2026, 6, 30))["ok"])

    # ---- O12, O13, O14
    def test_a13_no_asset_goes_into_service_from_a_day_to_come(self):
        from django.utils import timezone

        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        asset = FixedAsset.objects.create(name="Lathe", category=self.category, acquisition_date=DAY(2026, 1, 1),
                                          cost=D("1200"), life_months=12)
        with self.assertRaises(ValidationError):
            asset.place_in_service(on_date=tomorrow)
        typed = FixedAsset(name="Drill", category=self.category, acquisition_date=DAY(2026, 1, 1), cost=D("1200"),
                           life_months=12, in_service_date=tomorrow)
        try:
            typed.save()
        except ValidationError:
            return
        with self.assertRaises(ValidationError):
            typed.place_in_service()

    def test_a14_go_live_depreciation_is_a_day_that_has_come_and_is_never_edited_after(self):
        from django.utils import timezone

        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.loom(depreciated_before=tomorrow, opening_depreciation=D("100"))
        loom = self.loom(depreciated_before=DAY(2025, 12, 31), opening_depreciation=D("3000"))
        loom.opening_depreciation = D("2000")
        with self.assertRaises(ValidationError):
            loom.save()
        # placed in service, still never edited
        loom.refresh_from_db()
        loom.place_in_service(on_date=DAY(2025, 4, 1))
        loom.depreciated_before = DAY(2025, 11, 30)
        with self.assertRaises(ValidationError):
            loom.save()

    def test_a15_status_is_what_happened_to_the_asset_by_every_door(self):
        from django.contrib import admin

        asset = self.loom()
        asset.status = AssetStatus.DISPOSED
        with self.assertRaises(ValidationError):
            asset.save()
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.DRAFT)
        self.assertIn("status", admin.site._registry[FixedAsset].get_readonly_fields(None, asset))
        # the API's PATCH
        client = self.as_role("Controller")
        patched = client.patch(f"/api/assets/assets/{asset.pk}/", {"status": "in_service"}, format="json")
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.DRAFT, patched.content)

    def test_a16_a_failed_disposal_leaves_no_door_open_for_a_status_edit(self):
        # _status_by_method is set before save(); a save that raises must not leave it standing.
        asset = self.asset()
        asset._status_by_method = True
        try:
            asset.save(update_fields=["status"], force_update=True)   # no change, passes
        except Exception:
            pass
        self.assertFalse(asset._status_by_method)


@only_mine
class StoresReviewAssetRoleProbes(base.AssetTestCase):
    def test_r1_only_those_who_may_post_journals_act_on_assets_and_the_register_is_read_by_its_readers(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        asset = FixedAsset.objects.create(name="Loom", category=self.category, acquisition_date=DAY(2025, 4, 1),
                                          cost=D("12000"), life_months=48)
        wrong = []
        for group in Group.objects.order_by("name"):
            user = User.objects.create_user(f"u-{group.pk}")
            user.groups.add(group)
            client = APIClient()
            client.force_authenticate(user)
            may_post = user.has_perm("accounting.post_journalentry")
            may_read = user.has_perm("assets.view_fixedasset")
            for action in ("place-in-service", "remove-opening", "uncapitalise", "depreciate"):
                status = client.post(f"/api/assets/assets/{asset.pk}/{action}/", {}, format="json").status_code
                if (status == 403) == may_post:
                    wrong.append((group.name, action, status, "may post" if may_post else "may not"))
            status = client.get("/api/assets/assets/").status_code
            if (status == 403) == may_read:
                wrong.append((group.name, "list", status, "may read" if may_read else "may not"))
            status = client.post(f"/api/assets/assets/{asset.pk}/dispose/", {}, format="json").status_code
            may_dispose = user.has_perm("assets.dispose_fixedasset")
            if (status == 403) == may_dispose:
                wrong.append((group.name, "dispose", status))
            asset.refresh_from_db()
        self.assertEqual(wrong, [])


@only_mine
class StoresReviewCapitaliseRoleProbes(capbase.WithVehicles):
    def test_r2_capitalise_by_api_is_the_posters_alone_and_a_voided_or_debited_bill_is_refused(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        line = self.bill_line("1", "12000")
        url = f"/api/purchasing/bills/{line.bill_id}/capitalise/"
        wrong = []
        for group in Group.objects.order_by("name"):
            user = User.objects.create_user(f"c-{group.pk}")
            user.groups.add(group)
            client = APIClient()
            client.force_authenticate(user)
            status = client.post(url, {"line": 999999, "category": self.category.pk}, format="json").status_code
            may = user.has_perm("accounting.post_journalentry")
            sees = user.has_perm("purchasing.view_bill")
            # a role that may act reaches the (missing) line: 404; one that may not is told 403, or cannot see the bill (403/404)
            if may and sees and status != 404:
                wrong.append((group.name, status, "may"))
            if not may and status not in (403, 404):
                wrong.append((group.name, status, "may not"))
            if not may and status == 404 and sees:
                wrong.append((group.name, status, "may not but reached the line"))
        self.assertEqual(wrong, [])
