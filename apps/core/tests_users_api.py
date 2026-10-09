"""
Logins kept from the office: the HR Admin makes one with its first
password and roles, and it signs in; gives and takes a role; sets a new
password, which the validators check; deactivates a leaver, who can no
longer sign in. Nobody deletes a login or changes their own standing; a
bookkeeper sees no logins; superusers are not in the list.
"""

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework.test import APIClient

from .history import RecordEvent

USERS = "/api/core/users/"


class UsersApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        User.objects.create_superuser("root", password="not-listed-x9")

    def as_(self, role, *also):
        user = User.objects.create_user(role.replace(" ", "_").lower(), password="pw-for-tests-77")
        user.groups.add(*Group.objects.filter(name__in=[role, *also]))
        client = APIClient()
        client.force_authenticate(user)
        client.user = user
        return client

    def test_made_signed_in_given_roles_and_let_go(self):
        # An HR Admin who is also the bookkeeper: a role is given by someone who holds it.
        hr = self.as_("HR Admin", "Bookkeeper", "Employee Self Service")
        made = hr.post(USERS, {"username": "asha", "first_name": "Asha", "email": "asha@example.com",
                              "password": "loom-shed-2026", "roles": ["Bookkeeper"]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["roles"], "password" in made.json()), (["Bookkeeper"], False))
        self.assertTrue(Client().login(username="asha", password="loom-shed-2026"))
        pk = made.json()["id"]
        self.assertEqual(RecordEvent.objects.filter(object_id=pk).count(), 1)

        granted = hr.post(f"{USERS}{pk}/grant/", {"role": "Employee Self Service"}, format="json")
        self.assertEqual((granted.status_code, granted.json()["roles"]),
                         (200, ["Bookkeeper", "Employee Self Service"]))
        revoked = hr.post(f"{USERS}{pk}/revoke/", {"role": str(Group.objects.get(name="Bookkeeper").pk)}, format="json")
        self.assertEqual(revoked.json()["roles"], ["Employee Self Service"])
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
        # Nobody holds the right to delete a login; given it anyway, the answer is still no.
        self.assertEqual(hr.delete(f"{USERS}{pk}/").status_code, 403)
        hr.user.user_permissions.add(Permission.objects.get(codename="delete_user"))
        deleter = APIClient()
        deleter.force_authenticate(User.objects.get(pk=hr.user.pk))
        self.assertEqual(deleter.delete(f"{USERS}{pk}/").status_code, 400)
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


class NobodyGivesMoreThanTheyHoldTests(TestCase):
    """
    O86: changing a login asked only auth.change_user, so an HR Admin made
    a second login as Controller and set a real Controller's password. A
    role is given only by someone who holds it; a login holding what its
    keeper does not is not theirs to change.
    """

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr")
        self.hr.groups.add(Group.objects.get(name="HR Admin"))
        self.client_ = APIClient()
        self.client_.force_authenticate(self.hr)
        self.controller = User.objects.create_user("controller", email="ctl@example.com",
                                                   password="Original-Pass-2026!")
        self.controller.groups.add(Group.objects.get(name="Controller"))

    def test_hr_admin_makes_no_login_with_a_role_they_do_not_hold(self):
        response = self.client_.post(USERS, {
            "username": "hr-too", "password": "Plenty-Long-Pass-2026!", "roles": ["Controller"]}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("roles", response.json())
        self.assertFalse(User.objects.filter(username="hr-too").exists())

    def test_hr_admin_sets_no_password_on_a_login_above_them(self):
        response = self.client_.post(f"{USERS}{self.controller.pk}/set_password/",
                                     {"password": "Hr-Knows-This-2026!"}, format="json")
        self.controller.refresh_from_db()
        self.assertEqual((response.status_code, self.controller.check_password("Hr-Knows-This-2026!")),
                         (403, False), response.content)

    def test_nothing_else_is_changed_on_a_login_above_them_either(self):
        pk = self.controller.pk
        self.assertEqual(self.client_.patch(f"{USERS}{pk}/", {"email": "hr@example.com"}, format="json").status_code, 403)
        self.assertEqual(self.client_.post(f"{USERS}{pk}/deactivate/", {}, format="json").status_code, 403)
        self.assertEqual(self.client_.post(f"{USERS}{pk}/revoke/", {"role": "Controller"}, format="json").status_code,
                         403)
        self.controller.refresh_from_db()
        self.assertEqual((self.controller.email, self.controller.is_active), ("ctl@example.com", True))
        # Reading it is still the HR Admin's: the list shows every login.
        self.assertEqual(self.client_.get(f"{USERS}{pk}/").status_code, 200)

    def test_hr_admin_gives_no_role_they_do_not_hold(self):
        clerk = User.objects.create_user("clerk")
        response = self.client_.post(f"{USERS}{clerk.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("role", response.json())
        self.assertFalse(clerk.groups.exists())

    def test_a_permission_of_its_own_or_the_admin_site_is_above_them_too(self):
        poster = User.objects.create_user("poster")
        poster.user_permissions.add(Permission.objects.get(codename="post_journalentry"))
        staff = User.objects.create_user("staff-hr", is_staff=True)
        staff.groups.add(Group.objects.get(name="HR Admin"))
        for login in (poster, staff):
            response = self.client_.post(f"{USERS}{login.pk}/set_password/", {"password": "Hr-Knows-This-2026!"},
                                         format="json")
            self.assertEqual(response.status_code, 403, (login.username, response.content))

    def test_a_login_of_their_own_standing_is_theirs_and_a_superuser_gives_anything(self):
        colleague = User.objects.create_user("colleague")
        colleague.groups.add(Group.objects.get(name="HR Admin"))
        response = self.client_.post(f"{USERS}{colleague.pk}/set_password/", {"password": "Hr-Knows-This-2026!"},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.content)
        root = APIClient()
        root.force_authenticate(User.objects.create_superuser("root", password="not-listed-x9"))
        granted = root.post(f"{USERS}{colleague.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual((granted.status_code, granted.json()["roles"]), (200, ["Controller", "HR Admin"]))


class TheAdminsUserFormTests(TestCase):
    """O86: Django's own user admin asked only auth.change_user; a staff HR Admin made themselves superuser."""

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        self.hr.groups.add(Group.objects.get(name="HR Admin"))

    def test_a_staff_hr_admin_cannot_make_themselves_superuser(self):
        from django.test import override_settings

        self.client.force_login(self.hr)
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            page = self.client.get(f"/admin/auth/user/{self.hr.pk}/change/")
            self.assertEqual(page.status_code, 403)
            self.client.post(f"/admin/auth/user/{self.hr.pk}/change/", {
                "username": "hr-staff", "first_name": "", "last_name": "", "email": "",
                "is_active": "on", "is_staff": "on", "is_superuser": "on",
                "groups": [str(pk) for pk in self.hr.groups.values_list("pk", flat=True)],
                "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00",
                "initial-date_joined_0": "2026-01-01", "initial-date_joined_1": "00:00:00",
            })
            self.assertEqual(self.client.get("/admin/auth/group/").status_code, 403)
        self.hr.refresh_from_db()
        self.assertFalse(self.hr.is_superuser)

    def test_a_superuser_still_keeps_logins_there(self):
        from django.test import override_settings

        self.client.force_login(User.objects.create_superuser("root", password="pw"))
        with override_settings(ALLOWED_HOSTS=["testserver"]):
            self.assertEqual(self.client.get(f"/admin/auth/user/{self.hr.pk}/change/").status_code, 200)
            self.assertEqual(self.client.get("/admin/auth/group/").status_code, 200)
