"""review_yearend: who may make, void and read the restatement and the tax schedule, over the API."""
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.assets.tax import TaxBlockRate
from apps.core.management.commands.setup_roles import ROLES

from .tests_restatement import RestatementTestCase

PATHS = [("get", "/api/accounting/restatements/"), ("get", "/api/accounting/restatements/preview/?as_of=2026-03-31"),
         ("post", "/api/accounting/restatements/restate/"), ("post", "/api/accounting/restatements/1/void/"),
         ("get", "/api/assets/tax-years/schedule/?year=2025-04-01"), ("post", "/api/assets/tax-years/file/"),
         ("post", "/api/assets/tax-years/1/withdraw/"), ("get", "/api/assets/tax-blocks/"),
         ("get", "/api/assets/tax-block-rates/"), ("get", "/api/assets/tax-block-openings/"),
         ("post", "/api/assets/tax-block-rates/1/confirm/")]


class Roles(RestatementTestCase):
    def test_X1_only_the_controller_reaches_any_of_them(self):
        call_command("setup_roles", verbosity=0)
        self.invoice()
        leaks = []
        for role in ROLES:
            user = User.objects.create_user(role.replace(" ", "_"))
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            for method, path in PATHS:
                status = getattr(client, method)(path, {"as_of": "2026-03-31", "year": "2025-04-01"},
                                                 format="json").status_code
                # The Auditor reads every record, read-only (g24, the edit log): it may read these, never write.
                if role == "Auditor" and method == "get":
                    continue
                if status != 403 and role != "Controller":
                    leaks.append((role, method, path, status))
        self.assertEqual(leaks, [])

    def test_X2_a_confirmed_rate_cannot_be_changed_unconfirmed_through_the_api(self):
        from apps.assets.management.commands.load_tax_depreciation_rates import load

        load()
        rate = TaxBlockRate.objects.first()
        rate.confirm(None)
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("ctl")
        user.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(user)
        client.patch(f"/api/assets/tax-block-rates/{rate.pk}/", {"rate_percent": "99"}, format="json")
        rate.refresh_from_db()
        self.assertTrue(rate.confirmed_on is None or str(rate.rate_percent) != "99.0000",
                        f"a rate of {rate.rate_percent} stands confirmed by {rate.confirmed_by_id} on {rate.confirmed_on}")
