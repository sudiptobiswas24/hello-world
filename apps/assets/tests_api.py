"""
The assets module over the API, which it did not have — the one
standing finding of every audit so far.

The same lathe: 12,000 over twelve months from 1 January 2026.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from .models import AssetStatus, FixedAsset
from .tests import AssetTestCase, CapitalisationFixture


class ApiTestCase(AssetTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser(
            "controller", "controller@example.com", "x"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def post(self, path, data=None):
        return self.client.post(f"/api/assets/{path}", data or {}, format="json")

    def get(self, path):
        return self.client.get(f"/api/assets/{path}")


class EveryCollectionAnswersTests(ApiTestCase):
    def test_each_collection_lists(self):
        for path in ("categories/", "assets/", "depreciation/"):
            self.assertEqual(self.get(path).status_code, 200, path)


class AnAssetsLifeOverTheApiTests(ApiTestCase):
    def test_register_serve_depreciate_and_dispose(self):
        created = self.post("assets/", {
            "name": "Lathe", "category": self.category.pk,
            "acquisition_date": "2026-01-01", "cost": "12000",
            "salvage_value": "0", "life_months": 12,
        })
        self.assertEqual(created.status_code, 201, created.content)
        pk = created.json()["id"]

        served = self.post(f"assets/{pk}/place-in-service/", {"on_date": "2026-01-01"})
        self.assertEqual(served.json()["status"], AssetStatus.IN_SERVICE)

        charged = self.post(f"assets/{pk}/depreciate/", {"through": "2026-03-31"})
        self.assertEqual(len(charged.json()["charged"]), 3)
        self.assertEqual(
            Decimal(str(charged.json()["asset"]["net_book_value"])), Decimal("9000")
        )

        gone = self.post(f"assets/{pk}/dispose/", {"on_date": "2026-04-15"})
        self.assertEqual(gone.status_code, 200, gone.content)
        self.assertEqual(gone.json()["status"], AssetStatus.DISPOSED)

    def test_a_refusal_comes_back_in_words(self):
        created = self.post("assets/", {
            "name": "Lathe", "category": self.category.pk,
            "acquisition_date": "2026-01-01", "cost": "1000",
            "salvage_value": "1000", "life_months": 12,
        })
        self.assertEqual(created.status_code, 400)
        self.assertIn("below cost", str(created.json()))

    def test_an_asset_in_service_cannot_be_repriced(self):
        asset = self.asset()
        response = self.client.patch(
            f"/api/assets/assets/{asset.pk}/", {"cost": "15000"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("can no longer change", str(response.json()))
        asset.refresh_from_db()
        self.assertEqual(asset.cost, Decimal("12000"))

    def test_but_its_name_can_be_corrected(self):
        asset = self.asset()
        response = self.client.patch(
            f"/api/assets/assets/{asset.pk}/", {"name": "Lathe No. 2"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_an_asset_in_service_cannot_be_deleted(self):
        asset = self.asset()
        response = self.client.delete(f"/api/assets/assets/{asset.pk}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(FixedAsset.objects.filter(pk=asset.pk).exists())

    def test_a_hand_made_draft_can_be(self):
        asset = self.asset(in_service=False)
        response = self.client.delete(f"/api/assets/assets/{asset.pk}/")
        self.assertEqual(response.status_code, 204)


class TheMonthEndRunTests(ApiTestCase):
    def test_every_asset_in_service_is_charged_and_drafts_are_not(self):
        self.asset()
        self.asset()
        self.asset(in_service=False)
        response = self.post("assets/depreciate-all/", {"through": "2026-02-28"})
        self.assertEqual(len(response.json()["charged"]), 4)
        self.assertEqual(self.balance(self.depreciation), Decimal("4000"))


class TheRegisterTests(ApiTestCase):
    def test_it_answers_as_at_a_date(self):
        asset = self.asset()
        asset.depreciate(through=datetime.date(2026, 6, 30))
        response = self.get("assets/register/?as_of=2026-03-31")
        (row,) = response.json()
        self.assertEqual(Decimal(str(row["accumulated"])), Decimal("3000"))
        self.assertEqual(row["category"], "PLANT")

    def test_an_unknown_category_is_refused_in_words(self):
        response = self.get("assets/register/?category=NOPE")
        self.assertEqual(response.status_code, 400)
        self.assertIn("NOPE", str(response.json()))


class WritingOffTakesItsOwnRightTests(ApiTestCase):
    def test_someone_who_can_register_assets_cannot_dispose_of_them(self):
        clerk = get_user_model().objects.create_user("clerk", password="x")
        clerk.user_permissions.add(*Permission.objects.filter(
            codename__in=("add_fixedasset", "change_fixedasset", "view_fixedasset")
        ))
        client = APIClient()
        client.force_authenticate(clerk)
        asset = self.asset()
        response = client.post(
            f"/api/assets/assets/{asset.pk}/dispose/", {}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.IN_SERVICE)


class UndoingCapitalisationOverTheApiTests(CapitalisationFixture):
    def test_a_draft_from_a_bill_is_uncapitalised_not_deleted(self):
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_superuser(
            "controller", "c@example.com", "x"
        ))
        (asset,) = self.bill_line("1", "12000").capitalise_as_asset(self.category)
        refused = client.delete(f"/api/assets/assets/{asset.pk}/")
        self.assertEqual(refused.status_code, 400)
        undone = client.post(
            f"/api/assets/assets/{asset.pk}/uncapitalise/",
            {"on_date": "2026-01-05"}, format="json",
        )
        self.assertEqual(undone.json()["status"], AssetStatus.CANCELLED)
        self.assertEqual(self.balance(self.plant), Decimal("0"))
