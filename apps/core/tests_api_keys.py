"""
API keys for integrations (L12), as the people in their roles and as the
scale bridge's own program.

The HR Admin makes an integration (a login nobody signs in as) and issues
it a key, shown once and kept as a digest. The key signs the integration
in, every use recorded; revoked or expired, it signs nothing in; an
integration holding a right that needs a second factor is refused one;
whoever issues a key must be able to set the login's password.
"""

import datetime
import importlib.util
import pathlib

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import LiveServerTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .api_keys import ApiKey, ApiKeyUse, Integration

INTEGRATIONS = "/api/core/integrations/"
KEYS = "/api/core/api-keys/"
ME = "/api/core/me/"


def scale_permission():
    return Permission.objects.get(content_type__app_label="manufacturing", codename="add_scalereading")


class KeyCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr")
        self.hr.groups.add(Group.objects.get(name="HR Admin"))
        self.hrc = self.api(self.hr)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        return client

    def integration(self, name="WhatsApp channel"):
        made = self.hrc.post(INTEGRATIONS, {"name": name, "description": "Sends invoices"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        return Integration.objects.get(pk=made.json()["id"])

    def issue(self, client, integration, **body):
        return client.post(f"{INTEGRATIONS}{integration.pk}/issue_key/", body, format="json")

    def keyed(self, key):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Api-Key {key}")
        return client


class AKeyIsShownOnceAndSignsInItsIntegrationTests(KeyCase):
    def test_the_hr_admin_makes_an_integration_and_issues_its_key(self):
        channel = self.integration()
        self.assertEqual(channel.user.username, "integration-whatsapp-channel")
        self.assertFalse(channel.user.has_usable_password(), "a person could sign in as an integration")
        issued = self.issue(self.hrc, channel, label="Meta business account")
        self.assertEqual(issued.status_code, 201, issued.content)
        key = issued.json()["key"]
        row = ApiKey.objects.get()
        self.assertTrue(key.startswith(f"erp_{row.prefix}_"))
        self.assertNotIn(key, (row.digest, row.prefix))
        self.assertEqual(round((row.expires_at - timezone.now()) / datetime.timedelta(days=1)), 365)
        listed = self.hrc.get(KEYS).json()
        self.assertEqual([(r["prefix"], r["state"], "key" in r) for r in listed], [(row.prefix, "active", False)])

        me = self.keyed(key).get(ME, REMOTE_ADDR="10.0.4.21")
        self.assertEqual((me.status_code, me.json()["username"]), (200, "integration-whatsapp-channel"))
        # Only the rights it was given: none here.
        self.assertEqual(self.keyed(key).get("/api/accounting/payments/").status_code, 403)
        uses = self.hrc.get(f"{KEYS}{row.pk}/uses/").json()
        self.assertEqual([(u["method"], u["path"], u["address"]) for u in uses],
                         [("GET", "/api/accounting/payments/", "127.0.0.1"), ("GET", ME, "10.0.4.21")])
        self.assertIsNotNone(ApiKey.objects.get().last_used_at)

    def test_revoked_or_expired_it_signs_nothing_in(self):
        channel = self.integration()
        key = self.issue(self.hrc, channel).json()["key"]
        row = ApiKey.objects.get()
        self.assertEqual(self.hrc.post(f"{KEYS}{row.pk}/revoke/", {}, format="json").status_code, 200)
        refused = self.keyed(key).get(ME)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("revoked", refused.json()["detail"])
        self.assertEqual(self.hrc.post(f"{KEYS}{row.pk}/revoke/", {}, format="json").status_code, 400)

        later = self.issue(self.hrc, channel, expires_on=str(timezone.localdate() + datetime.timedelta(days=30)))
        ApiKey.objects.filter(pk=later.json()["id"]).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        refused = self.keyed(later.json()["key"]).get(ME)
        self.assertEqual((refused.status_code, "expired" in refused.json()["detail"]), (403, True))
        self.assertEqual(self.keyed("erp_00000000_not-a-key").get(ME).status_code, 403)
        self.assertEqual(ApiKeyUse.objects.count(), 0)

    def test_every_key_expires_within_a_year(self):
        channel = self.integration()
        for day, status in ((timezone.localdate() + datetime.timedelta(days=400), 400),
                            (timezone.localdate() - datetime.timedelta(days=1), 400)):
            self.assertEqual(self.issue(self.hrc, channel, expires_on=str(day)).status_code, status)


class WhoGetsAKeyTests(KeyCase):
    def test_an_integration_holding_a_right_that_needs_a_second_factor_gets_none(self):
        root = User.objects.create_superuser("root", password="x-root-pw-2026")
        channel = self.integration()
        key = self.issue(self.api(root), channel).json()["key"]
        channel.user.user_permissions.add(Permission.objects.get(content_type__app_label="accounting",
                                                                 codename="post_payment"))
        refused = self.keyed(key).get(ME)
        self.assertEqual(refused.status_code, 403)
        self.assertIn("needs a second factor", refused.json()["detail"])
        self.assertEqual(self.issue(self.api(root), channel).status_code, 400)

    def test_whoever_issues_one_must_be_able_to_set_its_password(self):
        bridge = self.integration("Scale SC-1")
        bridge.user.user_permissions.add(scale_permission())
        self.assertEqual(self.issue(self.hrc, bridge).status_code, 403)
        root = User.objects.create_superuser("root", password="x-root-pw-2026")
        self.assertEqual(self.issue(self.api(root), bridge).status_code, 201)

    def test_a_bookkeeper_issues_none_and_reads_none(self):
        bookkeeper = User.objects.create_user("bk")
        bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))
        channel = self.integration()
        self.assertEqual(self.issue(self.api(bookkeeper), channel).status_code, 403)
        self.assertEqual(self.api(bookkeeper).get(KEYS).status_code, 403)


class TheScaleBridgePostsWithItsKeyTests(LiveServerTestCase):
    """The bridge's own program (deploy/scale_bridge), posting a reading to this server with its key."""

    def test_the_bridge_posts_a_settled_weight_with_its_key(self):
        call_command("setup_roles", verbosity=0)
        root = User.objects.create_superuser("root", password="x-root-pw-2026")
        api = APIClient()
        api.force_authenticate(root)
        made = api.post(INTEGRATIONS, {"name": "Scale SC-1"}, format="json").json()
        bridge = Integration.objects.get(pk=made["id"])
        bridge.user.user_permissions.add(scale_permission())
        key = api.post(f"{INTEGRATIONS}{bridge.pk}/issue_key/", {"label": "Station PC"}, format="json").json()["key"]

        folder = pathlib.Path(settings.BASE_DIR) / "deploy" / "scale_bridge"
        spec = importlib.util.spec_from_file_location("scale_bridge_under_test", folder / "scale_bridge.py")
        program = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(program)
        kept = pathlib.Path(self.id().replace(".", "_") + ".key")
        kept.write_text(key + "\n")
        try:
            send = program.poster({**program.DEFAULTS, "server": self.live_server_url, "api_key_file": str(kept)})
            send({"scale": "SC-1", "gross_kg": "106.8", "stable": True, "key": "SC-1:1:1:gross:106.8"})
        finally:
            kept.unlink()
        from apps.manufacturing.station_scale import ScaleReading

        reading = ScaleReading.objects.get()
        self.assertEqual((reading.scale_code, str(reading.gross_kg), reading.key), ("SC-1", "106.800", "SC-1:1:1:gross:106.8"))
        self.assertEqual(list(ApiKeyUse.objects.values_list("method", "path")),
                         [("POST", "/api/manufacturing/scale-readings/")])
