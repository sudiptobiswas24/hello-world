"""
A record's history is written for every kind of record: made, which
fields changed (with their old and new values: tests_edit_log.py), each
action, deleted; and read under the record's own view permission. Made,
changed and deleted by the model layer, so a record made in code has its
line too.
"""

import datetime

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .history import RecordEvent, words_for
from .models import Party


class HistoryIsWrittenByTheApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("asha"))

    def test_made_changed_and_deleted_each_leave_a_line(self):
        made = self.client.post("/api/core/parties/", {"code": "C-9", "name": "Shree Cement"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        changed = self.client.patch(f"/api/core/parties/{pk}/", {"name": "Shree Cement Ltd", "phone": "9"}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        rows = self.client.get("/api/core/history/", {"model": "core.party", "id": pk}).json()
        self.assertEqual([(row["kind"], row["label"], row["summary"], row["who"]) for row in rows],
                         [("updated", "Changed", "name, phone", "asha"), ("created", "Made", "", "asha")])
        self.assertEqual(self.client.delete(f"/api/core/parties/{pk}/").status_code, 204)
        rows = self.client.get("/api/core/history/", {"model": "core.party", "id": pk}).json()
        self.assertEqual((rows[0]["kind"], rows[0]["summary"]), ("deleted", "C-9 - Shree Cement Ltd"))
        self.assertEqual(RecordEvent.objects.filter(content_type__model="party", object_id=pk).count(), 3)

    def test_the_history_is_read_under_the_records_own_view_permission(self):
        party = Party.objects.create(code="C-1", name="Acme")
        nobody = APIClient()
        nobody.force_authenticate(User.objects.create_user("nobody"))
        self.assertEqual(nobody.get("/api/core/history/", {"model": "core.party", "id": party.pk}).status_code, 403)
        reader = User.objects.create_user("reader")
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_party"))
        client = APIClient()
        client.force_authenticate(reader)
        # Made in code, by nobody signed in: its line says so (it said nothing at all when only the API wrote).
        self.assertEqual([(row["kind"], row["who"]) for row in client.get(
            "/api/core/history/", {"model": "core.party", "id": party.pk}).json()], [("created", "")])
        self.assertEqual(client.get("/api/core/history/", {"model": "nowhere.nothing", "id": 1}).status_code, 400)
        self.assertEqual(client.get("/api/core/history/", {"model": "core.party", "id": "x"}).status_code, 400)

    def test_an_actions_name_reads_as_what_was_done(self):
        self.assertEqual([words_for(name) for name in ("post", "post_delivery", "void", "send", "close_short", "mark_sent", "credit_note")],
                         ["Posted", "Posted delivery", "Voided", "Sent", "Closed short", "Marked sent", "Credit note"])


class AMomentReadsInThePlantsTimeTests(TestCase):
    """
    O421: a login's history read "date joined: nothing -> 2026-11-01T22:11:17.290540+00:00",
    raw and in UTC, beside the event's own "02 Nov 2026, 03:41" in plant time. A date-time
    now reads as the event line does; what the log keeps is unchanged.
    """

    joined = datetime.datetime(2026, 11, 1, 22, 11, 17, 290540, tzinfo=datetime.timezone.utc)

    def made(self):
        from .history import history

        login = User.objects.create_user("asha", date_joined=self.joined)
        return login, {change["field"]: change for change in history(User, login.pk)[-1]["changes"]}

    def test_in_kolkata_it_is_the_next_morning(self):
        with override_settings(TIME_ZONE="Asia/Kolkata"):
            login, changes = self.made()
            self.assertEqual(changes["date_joined"]["to"], "02 Nov 2026, 03:41")
            client = APIClient()
            client.force_authenticate(User.objects.create_superuser("hr"))
            rows = client.get("/api/core/history/", {"model": "auth.user", "id": login.pk}).json()
            shown = {change["field"]: change["to"] for change in rows[-1]["changes"]}
            self.assertEqual(shown["date_joined"], "02 Nov 2026, 03:41")
        from django.contrib.contenttypes.models import ContentType

        kept = RecordEvent.objects.filter(content_type=ContentType.objects.get_for_model(User),
                                          object_id=login.pk).order_by("pk").first().changes
        self.assertEqual(kept["date_joined"]["to"], "2026-11-01T22:11:17.290540+00:00")

    def test_on_a_server_kept_in_utc_it_is_the_same_evening(self):
        with override_settings(TIME_ZONE="UTC"):
            _, changes = self.made()
            self.assertEqual(changes["date_joined"]["to"], "01 Nov 2026, 22:11")
