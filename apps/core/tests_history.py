"""
A record's history is written by the API layer for every kind of record:
made, which fields changed, each action, deleted; and read under the
record's own view permission.
"""

from django.contrib.auth.models import Permission, User
from django.test import TestCase
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
        self.assertEqual(RecordEvent.objects.count(), 3)

    def test_the_history_is_read_under_the_records_own_view_permission(self):
        party = Party.objects.create(code="C-1", name="Acme")
        nobody = APIClient()
        nobody.force_authenticate(User.objects.create_user("nobody"))
        self.assertEqual(nobody.get("/api/core/history/", {"model": "core.party", "id": party.pk}).status_code, 403)
        reader = User.objects.create_user("reader")
        reader.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename="view_party"))
        client = APIClient()
        client.force_authenticate(reader)
        self.assertEqual(client.get("/api/core/history/", {"model": "core.party", "id": party.pk}).json(), [])
        self.assertEqual(client.get("/api/core/history/", {"model": "nowhere.nothing", "id": 1}).status_code, 400)
        self.assertEqual(client.get("/api/core/history/", {"model": "core.party", "id": "x"}).status_code, 400)

    def test_an_actions_name_reads_as_what_was_done(self):
        self.assertEqual([words_for(name) for name in ("post", "post_delivery", "void", "send", "close_short", "mark_sent", "credit_note")],
                         ["Posted", "Posted delivery", "Voided", "Sent", "Closed short", "Marked sent", "Credit note"])
