"""The planning settings through the API, kept by the production planner: one set, changed in place (O626)."""

from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from .models import PlanningSettings

URL = "/api/planning/settings/"


class PlanningSettingsApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def as_everything(self):
        # Who could make a second set or delete the one before (the planner holds neither right): the refusal is
        # the method's, whoever asks.
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("everything"))
        return client

    def test_the_planner_reads_the_one_set_made_on_first_read_and_changes_it(self):
        planner = self.as_("Production Planner")
        [row] = planner.get(URL).json()
        self.assertEqual(row["horizon_days"], 90)
        changed = planner.patch(f"{URL}{row['id']}/", {"horizon_days": 60}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(PlanningSettings.get().horizon_days, 60)
        self.assertEqual(PlanningSettings.objects.count(), 1)

    def test_a_second_set_cannot_be_posted(self):
        PlanningSettings.get()
        refused = self.as_everything().post(URL, {"horizon_days": 30}, format="json")
        self.assertEqual(refused.status_code, 405, refused.content)
        self.assertEqual(PlanningSettings.objects.count(), 1)

    def test_the_one_set_cannot_be_deleted(self):
        settings = PlanningSettings.get()
        settings.horizon_days = 45
        settings.save()
        refused = self.as_everything().delete(f"{URL}{settings.pk}/")
        self.assertEqual(refused.status_code, 405, refused.content)
        self.assertEqual(PlanningSettings.get().horizon_days, 45)

    def test_a_second_record_is_refused(self):
        PlanningSettings.get()
        with self.assertRaisesMessage(ValidationError, "There is one set of planning settings"):
            PlanningSettings.objects.create()
        self.assertEqual(PlanningSettings.objects.count(), 1)


class TheOneSetInTheAdminTests(TestCase):
    """The admin of each module's settings offers Add only while none stands, and no Delete (O626)."""

    def test_each_settings_admin(self):
        from apps.manufacturing.orders import ManufacturingSettings
        from apps.quality.models import QualitySettings

        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_superuser("admin-o626")
        for model in (PlanningSettings, QualitySettings, ManufacturingSettings):
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertEqual(model.objects.count(), 0)
                self.assertTrue(model_admin.has_add_permission(request))
                made = model.get()
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request, made))
