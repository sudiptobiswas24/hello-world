"""
Logins kept from the office: the HR Admin makes one with its first
password and roles, and it signs in; gives and takes a role; sets a new
password, which the validators check; deactivates a leaver, who can no
longer sign in. Nobody deletes a login or changes their own standing; a
bookkeeper sees no logins; superusers are not in the list.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .history import RecordEvent

USERS = "/api/core/users/"


class UsersApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        User.objects.create_superuser("root", password="not-listed-x9")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower(), password="pw-for-tests-77")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        client.user = user
        return client

    def test_made_signed_in_given_roles_and_let_go(self):
        hr = self.as_("HR Admin")
        made = hr.post(USERS, {"username": "asha", "first_name": "Asha", "email": "asha@example.com",
                              "password": "loom-shed-2026", "roles": ["Bookkeeper"]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["roles"], "password" in made.json()), (["Bookkeeper"], False))
        self.assertTrue(Client().login(username="asha", password="loom-shed-2026"))
        pk = made.json()["id"]
        self.assertEqual(RecordEvent.objects.filter(object_id=pk).count(), 1)

        granted = hr.post(f"{USERS}{pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual((granted.status_code, granted.json()["roles"]), (200, ["Bookkeeper", "Controller"]))
        revoked = hr.post(f"{USERS}{pk}/revoke/", {"role": str(Group.objects.get(name="Bookkeeper").pk)}, format="json")
        self.assertEqual(revoked.json()["roles"], ["Controller"])
        self.assertEqual(hr.post(f"{USERS}{pk}/grant/", {"role": "Wizard"}, format="json").status_code, 400)

        weak = hr.post(f"{USERS}{pk}/set_password/", {"password": "asha"}, format="json")
        self.assertEqual(weak.status_code, 400, weak.content)
        self.assertEqual(hr.post(f"{USERS}{pk}/set_password/", {"password": "printing-line-9"}, format="json").status_code, 200)
        self.assertTrue(Client().login(username="asha", password="printing-line-9"))
        self.assertFalse(Client().login(username="asha", password="loom-shed-2026"))

        gone = hr.post(f"{USERS}{pk}/deactivate/", {}, format="json")
        self.assertEqual((gone.status_code, gone.json()["is_active"]), (200, False))
        self.assertFalse(Client().login(username="asha", password="printing-line-9"))
        self.assertEqual(hr.post(f"{USERS}{pk}/reactivate/", {}, format="json").json()["is_active"], True)
        self.assertEqual(hr.delete(f"{USERS}{pk}/").status_code, 400)
        self.assertTrue(User.objects.filter(pk=pk).exists())

    def test_what_is_refused(self):
        hr = self.as_("HR Admin")
        self.assertEqual(hr.post(USERS, {"username": "nopass"}, format="json").status_code, 400)
        self.assertEqual(hr.post(USERS, {"username": "weak", "password": "123"}, format="json").status_code, 400)
        self.assertEqual(hr.post(USERS, {"username": "x", "password": "loom-shed-2026", "roles": ["Wizard"]},
                                 format="json").status_code, 400)
        me = hr.user.pk
        self.assertEqual(hr.post(f"{USERS}{me}/deactivate/", {}, format="json").status_code, 400)
        self.assertEqual(hr.post(f"{USERS}{me}/grant/", {"role": "Controller"}, format="json").status_code, 400)
        self.assertEqual(hr.patch(f"{USERS}{me}/", {"roles": ["Controller"]}, format="json").status_code, 400)
        self.assertEqual(hr.patch(f"{USERS}{me}/", {"is_active": False}, format="json").status_code, 400)
        self.assertEqual(hr.patch(f"{USERS}{me}/", {"email": "hr@example.com"}, format="json").status_code, 200)
        listed = {row["username"] for row in hr.get(USERS).json()}
        self.assertIn("hr_admin", listed)
        self.assertNotIn("root", listed)

    def test_who_may(self):
        self.assertEqual(self.as_("Bookkeeper").get(USERS).status_code, 403)
        controller = self.as_("Controller")
        self.assertEqual(controller.get(USERS).status_code, 200)
        self.assertEqual(controller.post(USERS, {"username": "y", "password": "loom-shed-2026"}, format="json").status_code, 403)
