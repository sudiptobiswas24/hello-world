"""
What a security pass found that the role tests did not.

- A Purchasing Clerk could PATCH a purchase order's status to
  "confirmed", past the approval tiers and every check confirm() makes.
  Sales had always kept its order status read-only.
- Warehouse Staff could POST raw stock movements: stock out of nothing,
  at any cost, with no ledger entry and none of the shelf's rules.
- Nothing limited password guesses, at the login page, the admin or the
  API's basic authentication.
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.purchasing.tests_lifecycle import PurchasingLifecycleTestCase


class APurchaseOrderIsConfirmedOnlyByConfirmingTests(PurchasingLifecycleTestCase):
    def test_status_cannot_be_written(self):
        call_command("setup_roles", verbosity=0)
        clerk = User.objects.create_user("clerk")
        clerk.groups.add(Group.objects.get(name="Purchasing Clerk"))
        client = APIClient()
        client.force_authenticate(clerk)
        order = self.make_order(confirm=False)
        client.patch(f"/api/purchasing/purchase-orders/{order.pk}/", {"status": "confirmed"},
                     format="json")
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")


class StockMovesOnlyThroughDocumentsTests(PurchasingLifecycleTestCase):
    def test_a_raw_movement_cannot_be_posted(self):
        call_command("setup_roles", verbosity=0)
        store = User.objects.create_user("store")
        store.groups.add(Group.objects.get(name="Warehouse Staff"))
        client = APIClient()
        client.force_authenticate(store)
        response = client.post("/api/inventory/stock-movements/", {
            "item": self.item.pk, "warehouse": self.warehouse.pk, "movement_type": "receipt",
            "uom": self.uom.pk, "quantity": "1000", "unit_cost": "1",
            "occurred_at": timezone.now().isoformat()}, format="json")
        self.assertIn(response.status_code, (403, 405))
        self.assertEqual(self.item.on_hand_at(self.warehouse), Decimal("0"))
        self.assertEqual(client.get("/api/inventory/stock-movements/").status_code, 200)


class NoOneWritesMovementsTests(PurchasingLifecycleTestCase):
    def test_not_even_with_the_permission(self):
        """The route is read-only, whatever a role is given by mistake."""
        from django.contrib.auth.models import Permission

        user = User.objects.create_user("granted")
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory", codename__in=["add_stockmovement",
                                                               "view_stockmovement"]))
        client = APIClient()
        client.force_authenticate(user)
        response = client.post("/api/inventory/stock-movements/", {
            "item": self.item.pk, "warehouse": self.warehouse.pk, "movement_type": "receipt",
            "uom": self.uom.pk, "quantity": "1000", "unit_cost": "1",
            "occurred_at": timezone.now().isoformat()}, format="json")
        self.assertEqual(response.status_code, 405)


class GuessingStopsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("accounts", password="right-horse-battery")

    def guess(self, times, password="wrong"):
        for _ in range(times):
            authenticate(username="accounts", password=password)

    def test_ten_wrong_and_then_the_right_one_is_refused(self):
        self.guess(10)
        self.assertIsNone(authenticate(username="accounts", password="right-horse-battery"))

    def test_nine_wrong_and_the_right_one_still_works(self):
        self.guess(9)
        self.assertEqual(authenticate(username="accounts", password="right-horse-battery"),
                         self.user)

    def test_a_success_clears_the_count(self):
        self.guess(9)
        authenticate(username="accounts", password="right-horse-battery")
        self.guess(9)
        self.assertIsNotNone(authenticate(username="accounts", password="right-horse-battery"))

    def test_the_lock_lifts_after_a_quarter_of_an_hour(self):
        self.guess(10)
        later = timezone.now() + datetime.timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertIsNotNone(
                authenticate(username="accounts", password="right-horse-battery"))

    def test_guesses_while_locked_do_not_keep_it_locked(self):
        """One guess every ninety seconds held a name locked for ever."""
        self.guess(10)
        start = timezone.now()
        for minutes in (2, 5, 8, 11, 14):
            with mock.patch("django.utils.timezone.now", return_value=start + datetime.timedelta(minutes=minutes)):
                self.assertIsNone(authenticate(username="accounts", password="wrong"))
        with mock.patch("django.utils.timezone.now", return_value=start + datetime.timedelta(minutes=16)):
            self.assertEqual(authenticate(username="accounts", password="right-horse-battery"), self.user)

    def test_failures_older_than_the_window_are_not_kept(self):
        from apps.core.models import LoginFailure

        authenticate(username="typo-once", password="x")
        later = timezone.now() + datetime.timedelta(minutes=20)
        with mock.patch("django.utils.timezone.now", return_value=later):
            authenticate(username="someone-else", password="x")
        self.assertEqual(list(LoginFailure.objects.values_list("username", flat=True)), ["someone-else"])

    def test_the_api_s_basic_authentication_is_counted_too(self):
        client = APIClient()
        for _ in range(10):
            client.credentials(HTTP_AUTHORIZATION="Basic YWNjb3VudHM6d3Jvbmc=")  # accounts:wrong
            client.get("/api/core/currencies/")
        self.assertIsNone(authenticate(username="accounts", password="right-horse-battery"))

    def test_an_unknown_name_is_counted_like_a_known_one(self):
        """Or the lock itself would say which names exist."""
        for _ in range(10):
            authenticate(username="nobody", password="x")
        from apps.core.models import LoginFailure

        self.assertEqual(LoginFailure.objects.filter(username="nobody").count(), 10)
