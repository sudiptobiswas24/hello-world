"""The quality policy through the API, kept by the quality manager: one record."""

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .models import QualitySettings

URL = "/api/quality/quality-settings/"


class QualitySettingsApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_quality_manager_turns_on_the_second_person(self):
        manager = self.as_("Quality Manager")
        [row] = manager.get(URL).json()
        self.assertFalse(row["concessions_need_a_second_person"])
        changed = manager.patch(f"{URL}{row['id']}/", {"concessions_need_a_second_person": True}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertTrue(QualitySettings.get().concessions_need_a_second_person)
        self.assertEqual(QualitySettings.objects.count(), 1)

    def test_the_inspector_cannot_waive_it(self):
        row = QualitySettings.get()
        refused = self.as_("Quality Inspector").patch(f"{URL}{row.pk}/", {"concessions_need_a_second_person": False},
                                                      format="json")
        self.assertEqual(refused.status_code, 403)

    def test_a_second_record_is_refused(self):
        QualitySettings.get()
        with self.assertRaises(ValidationError):
            QualitySettings.objects.create()
