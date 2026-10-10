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
from .roles import RoleProposal

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
        hr = self.as_("HR Admin")
        # O142, the owner's two-person rule: the HR Admin does not hold Bookkeeper, so making the new
        # bookkeeper's login only proposes the role; a bookkeeper confirms it (TwoPeopleGiveARoleTests).
        made = hr.post(USERS, {"username": "asha", "first_name": "Asha", "email": "asha@example.com",
                              "password": "loom-shed-2026", "roles": ["Bookkeeper"]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["roles"], made.json()["proposed_roles"], "password" in made.json()),
                         ([], ["Bookkeeper"], False))
        self.assertTrue(Client().login(username="asha", password="loom-shed-2026"))
        pk = made.json()["id"]
        self.assertEqual(RecordEvent.objects.filter(object_id=pk).count(), 1)

        # A role the HR Admin holds is given at once.
        granted = hr.post(f"{USERS}{pk}/grant/", {"role": "HR Admin"}, format="json")
        self.assertEqual((granted.status_code, granted.json()["roles"]), (200, ["HR Admin"]))
        revoked = hr.post(f"{USERS}{pk}/revoke/", {"role": str(Group.objects.get(name="HR Admin").pk)}, format="json")
        self.assertEqual(revoked.json()["roles"], [])
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
        # Only proposes it (O142's two-person rule): the login holds nothing until a Controller confirms.
        response = self.client_.post(USERS, {
            "username": "hr-too", "password": "Plenty-Long-Pass-2026!", "roles": ["Controller"]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["roles"], response.json()["proposed_roles"]), ([], ["Controller"]))
        self.assertFalse(User.objects.get(username="hr-too").groups.exists())

    def test_hr_admin_sets_no_password_on_a_login_above_them(self):
        response = self.client_.post(f"{USERS}{self.controller.pk}/set_password/",
                                     {"password": "Hr-Knows-This-2026!"}, format="json")
        self.controller.refresh_from_db()
        self.assertEqual((response.status_code, self.controller.check_password("Hr-Knows-This-2026!")),
                         (403, False), response.content)

    def test_nothing_is_given_back_to_a_login_above_them_either(self):
        pk = self.controller.pk
        self.assertEqual(self.client_.patch(f"{USERS}{pk}/", {"email": "hr@example.com"}, format="json").status_code, 403)
        self.controller.is_active = False
        self.controller.save(update_fields=["is_active"])
        self.assertEqual(self.client_.post(f"{USERS}{pk}/reactivate/", {}, format="json").status_code, 403)
        self.controller.refresh_from_db()
        self.assertEqual((self.controller.email, self.controller.is_active), ("ctl@example.com", False))
        # Reading it is still the HR Admin's: the list shows every login.
        self.assertEqual(self.client_.get(f"{USERS}{pk}/").status_code, 200)

    def test_hr_admin_gives_no_role_they_do_not_hold(self):
        clerk = User.objects.create_user("clerk")
        response = self.client_.post(f"{USERS}{clerk.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual((response.status_code, response.json()["proposed_roles"]), (200, ["Controller"]),
                         response.content)
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


class TwoPeopleGiveARoleTests(TestCase):
    """
    O142, the owner's rule: whoever keeps logins proposes a role they do
    not hold, and it is given when someone who holds it (or a superuser)
    confirms it. The proposer never confirms their own proposal; nobody
    proposes or confirms for their own login. Asked as the people in
    their roles.
    """

    PROPOSALS = "/api/core/role-proposals/"

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.people = {}
        for name, role in (("hr", "HR Admin"), ("books", "Bookkeeper"), ("ctl", "Controller"),
                           ("ctl2", "Controller"), ("rep", "Sales Rep")):
            user = User.objects.create_user(name)
            user.groups.add(Group.objects.get(name=role))
            self.people[name] = user
        self.asha = User.objects.create_user("asha")

    def as_(self, name):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.people[name].pk))
        return client

    def propose(self, user, role, by="hr"):
        response = self.as_(by).post(f"{USERS}{user.pk}/grant/", {"role": role}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return RoleProposal.objects.get(user=user, group__name=role, status="pending")

    def decide(self, name, proposal, verb="confirm"):
        return self.as_(name).post(f"{self.PROPOSALS}{proposal.pk}/{verb}/", {}, format="json")

    def roles(self, user):
        return sorted(user.groups.values_list("name", flat=True))

    def test_hr_proposes_bookkeeper_and_a_bookkeeper_confirms_it(self):
        proposal = self.propose(self.asha, "Bookkeeper")
        self.assertEqual(self.roles(self.asha), [])
        waiting = self.as_("books").get(self.PROPOSALS, {"waiting": "true"}).json()
        self.assertEqual([(row["username"], row["role"], row["may_confirm"]) for row in waiting],
                         [("asha", "Bookkeeper", True)])
        inbox = {row["key"]: row["count"] for row in self.as_("books").get("/api/web/inbox/").json()["rows"]}
        self.assertEqual(inbox.get("roles_to_confirm"), 1, inbox)
        confirmed = self.decide("books", proposal)
        self.assertEqual((confirmed.status_code, confirmed.json()["status"], confirmed.json()["decided_by"]),
                         (200, "confirmed", self.people["books"].pk), confirmed.content)
        self.assertEqual(self.roles(self.asha), ["Bookkeeper"])

    def test_hr_cannot_confirm_its_own_proposal(self):
        proposal = self.propose(self.asha, "Bookkeeper")
        self.assertEqual(self.decide("hr", proposal).status_code, 400)
        # Not even once it holds the role itself: the second person is someone else.
        self.people["hr"].groups.add(Group.objects.get(name="Bookkeeper"))
        refused = self.decide("hr", proposal)
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("You proposed Bookkeeper", str(refused.content))
        self.assertEqual(self.roles(self.asha), [])

    def test_a_controller_grant_confirmed_by_a_controller(self):
        proposal = self.propose(self.asha, "Controller")
        # Holding Bookkeeper, or a rep's role, they do not even see a Controller proposal.
        self.assertEqual(self.decide("books", proposal).status_code, 404)
        self.assertEqual(self.decide("rep", proposal).status_code, 404)
        self.assertEqual(self.decide("ctl", proposal).status_code, 200)
        self.assertEqual(self.roles(self.asha), ["Controller"])

    def test_nobody_acts_on_their_own_login(self):
        mine = self.as_("hr").post(f"{USERS}{self.people['hr'].pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(mine.status_code, 400, mine.content)
        self.assertFalse(RoleProposal.objects.filter(user=self.people["hr"]).exists())
        # A proposal for ctl2's login, waiting, while ctl2 is given the role another way: not theirs to confirm.
        proposal = self.propose(self.people["books"], "Controller")
        for_ctl2 = RoleProposal.objects.create(user=self.people["ctl2"], group=Group.objects.get(name="Controller"),
                                               proposed_by=self.people["hr"])
        self.assertEqual(self.decide("ctl2", for_ctl2).status_code, 400)
        self.assertEqual(self.decide("ctl2", for_ctl2, "decline").status_code, 400)
        self.assertEqual(self.decide("ctl2", proposal).status_code, 200)

    def test_declined_or_withdrawn_it_is_not_given(self):
        declined = self.propose(self.asha, "Bookkeeper")
        self.assertEqual(self.decide("rep", declined, "decline").status_code, 404)
        self.assertEqual(self.decide("books", declined, "decline").json()["status"], "declined")
        self.assertEqual(self.decide("books", declined).status_code, 400)
        withdrawn = self.propose(self.asha, "Controller")
        self.assertEqual(self.decide("hr", withdrawn, "decline").json()["status"], "declined")
        self.assertEqual(self.roles(self.asha), [])

    def test_a_login_with_no_role_reads_none(self):
        self.propose(self.asha, "Bookkeeper")
        nobody = APIClient()
        nobody.force_authenticate(self.asha)
        self.assertEqual(nobody.get(self.PROPOSALS).status_code, 403)
        self.assertEqual(self.as_("rep").get(self.PROPOSALS).json(), [])


class TakingAccessAwayIsTheKeepersTests(TestCase):
    """
    O142: the first fix refused the HR Admin any change to a login holding
    a role they do not, taking access away included. Deactivating a leaver
    or taking a role off a login gives nobody anything: always the keeper's.
    """

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr")
        self.hr.groups.add(Group.objects.get(name="HR Admin"))
        self.client_ = APIClient()
        self.client_.force_authenticate(self.hr)

    def test_hr_admin_deactivates_a_leaving_sales_rep(self):
        leaver = User.objects.create_user("leaving-rep")
        leaver.groups.add(Group.objects.get(name="Sales Rep"))
        response = self.client_.post(f"{USERS}{leaver.pk}/deactivate/", {}, format="json")
        leaver.refresh_from_db()
        self.assertEqual((response.status_code, leaver.is_active), (200, False), response.content)

    def test_hr_admin_takes_a_role_off_a_login_above_them(self):
        controller = User.objects.create_user("controller")
        controller.groups.add(Group.objects.get(name="Controller"))
        response = self.client_.post(f"{USERS}{controller.pk}/revoke/", {"role": "Controller"}, format="json")
        self.assertEqual((response.status_code, list(controller.groups.all())), (200, []), response.content)

    def test_but_not_their_own(self):
        me = self.hr.pk
        self.assertEqual(self.client_.post(f"{USERS}{me}/revoke/", {"role": "HR Admin"}, format="json").status_code, 400)
        self.assertEqual(self.client_.post(f"{USERS}{me}/deactivate/", {}, format="json").status_code, 400)
        self.assertEqual(list(self.hr.groups.values_list("name", flat=True)), ["HR Admin"])


class TheAdminsUserFormTests(TestCase):
    """O86: Django's own user admin asked only auth.change_user; a staff HR Admin made themselves superuser."""

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = User.objects.create_user("hr-staff", password="pw", is_staff=True)
        self.hr.groups.add(Group.objects.get(name="HR Admin"))

    def test_a_staff_hr_admin_cannot_make_themselves_superuser(self):
        # The page itself is refused (403): the audit's probe expected it to open and the save to
        # fail. Logins are kept on the office's screen; the admin's are superusers' (reviewed).
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
