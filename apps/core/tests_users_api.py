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

        # O157: with Bookkeeper proposed and the HR Admin not holding it, its password is not theirs to set.
        self.assertEqual(hr.post(f"{USERS}{pk}/set_password/", {"password": "printing-line-9"}, format="json")
                         .status_code, 403)
        proposal = RoleProposal.objects.get(user_id=pk, status="pending")
        self.assertEqual(hr.post(f"/api/core/role-proposals/{proposal.pk}/decline/", {}, format="json").status_code,
                         200)
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


PW = "First-Pw-2026-xq"


class KeeperAndHolder(TestCase):
    """The keeper (HR Admin) and a holder of the role it proposes (Bookkeeper), each a person in the role."""

    PROPOSALS = "/api/core/role-proposals/"

    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.hr = self.person("hr", "HR Admin")
        self.bk = self.person("bk", "Bookkeeper")
        self.hrc = self.api(self.hr)

    def person(self, name, *roles):
        user = User.objects.create_user(name)
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def api(self, user):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        return client

    def make(self, username, roles, **also):
        response = self.hrc.post(USERS, {"username": username, "password": PW, "roles": roles, **also}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return User.objects.get(username=username)

    def pending(self, user, role):
        return RoleProposal.objects.get(user=user, group__name=role, status="pending")

    def confirm(self, user, proposal, **body):
        return self.api(user).post(f"{self.PROPOSALS}{proposal.pk}/confirm/", body, format="json")

    def signs_in(self, username, password):
        return APIClient().login(username=username, password=password)

    def holds(self, user, role):
        return user.groups.filter(name=role).exists()


class KeeperKnowsNoCredentialAboveItTests(KeeperAndHolder):
    """
    O157: whoever sets a login's password or email can sign in as it. The
    keeper set asha's first password, a Bookkeeper confirmed asha's role,
    and the keeper signed in as asha, confirmed its own next proposal and
    gave itself the role (review_sec2 F1). Now the keeper sets neither on
    a login holding or proposed for a role above it, and what it set
    stops working when such a role is given. Asked as the people in their
    roles, through the API, with the reviewer's numbers.
    """

    def test_the_keepers_first_password_stops_working_when_the_role_is_confirmed(self):
        asha = self.make("asha", ["Bookkeeper"])
        # Holding nothing above the keeper yet, it is the keeper's to sign in as.
        self.assertTrue(self.signs_in("asha", PW))
        self.assertEqual(self.confirm(self.bk, self.pending(asha, "Bookkeeper")).status_code, 200)
        self.assertFalse(self.signs_in("asha", PW), "HR signs in as the Bookkeeper with the first password it chose")
        reset = self.hrc.post(f"{USERS}{asha.pk}/set_password/", {"password": "Another-Pw-2026-xq"}, format="json")
        self.assertEqual(reset.status_code, 403)

    def test_the_reviewers_chain_stops_after_the_one_honest_confirm(self):
        asha = self.make("asha", ["Bookkeeper"])                                    # 1: HR makes asha
        self.assertEqual(self.confirm(self.bk, self.pending(asha, "Bookkeeper")).status_code, 200)  # 2: bk confirms
        self.assertFalse(self.signs_in("asha", PW))                                 # 3: HR cannot be asha
        puppet = self.make("puppet", ["HR Admin", "Bookkeeper"])                    # 4: HR Admin given, Bookkeeper proposed
        self.assertTrue(self.signs_in("puppet", PW))
        # 5 and 6 need asha's login, which HR no longer has. The honest confirm of puppet's role lapses HR's password
        # for puppet too, and a Bookkeeper cannot issue one to a login holding HR Admin above them.
        proposal = self.pending(puppet, "Bookkeeper")
        refused = self.confirm(self.bk, proposal, password="Bk-Chose-2026-xq")
        self.assertEqual(refused.status_code, 403, refused.content)
        self.assertEqual(RoleProposal.objects.get(pk=proposal.pk).status, "pending")
        self.assertEqual(self.confirm(self.bk, proposal).status_code, 200)
        self.assertFalse(self.signs_in("puppet", PW))
        self.hr.refresh_from_db()
        self.assertFalse(self.holds(self.hr, "Bookkeeper"))

    def test_the_confirmer_issues_the_next_password(self):
        asha = self.make("asha", ["Bookkeeper"])
        confirmed = self.confirm(self.bk, self.pending(asha, "Bookkeeper"), password="Bk-Chose-2026-xq")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual((self.signs_in("asha", PW), self.signs_in("asha", "Bk-Chose-2026-xq")), (False, True))

    def test_an_existing_colleague_is_not_the_keepers_once_a_role_above_it_is_proposed(self):
        ravi = self.person("ravi", "HR Admin")
        self.assertEqual(self.hrc.post(f"{USERS}{ravi.pk}/set_password/", {"password": PW}, format="json").status_code,
                         200)
        self.assertEqual(self.hrc.post(f"{USERS}{ravi.pk}/grant/", {"role": "Bookkeeper"}, format="json").status_code,
                         200)
        # Proposed, not yet confirmed: neither credential is the keeper's to set now.
        self.assertEqual(self.hrc.post(f"{USERS}{ravi.pk}/set_password/", {"password": "Other-Pw-2026-xq"},
                                       format="json").status_code, 403)
        self.assertEqual(self.hrc.patch(f"{USERS}{ravi.pk}/", {"email": "hr-mailbox@example.com"}, format="json")
                         .status_code, 403)
        self.assertEqual(self.confirm(self.bk, self.pending(ravi, "Bookkeeper")).status_code, 200)
        self.assertEqual(self.hrc.post(f"{USERS}{ravi.pk}/set_password/", {"password": "Other-Pw-2026-xq"},
                                       format="json").status_code, 403)
        self.assertFalse(self.signs_in("ravi", PW), "HR set ravi's password, proposed a role, and signs in as ravi")

    def test_the_keepers_email_takes_no_reset_after_the_confirm_until_set_again(self):
        from django.core import mail

        asha = self.make("asha", ["Bookkeeper"], email="hr-mailbox@example.com")
        Client().post("/accounts/password_reset/", {"email": "hr-mailbox@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # holding nothing above the keeper, it is the keeper's
        self.assertEqual(self.confirm(self.bk, self.pending(asha, "Bookkeeper"),
                                      password="Bk-Chose-2026-xq").status_code, 200)
        reset = Client().post("/accounts/password_reset/", {"email": "hr-mailbox@example.com"})
        self.assertEqual((reset.status_code, len(mail.outbox)), (302, 1))
        # Set again by someone who may keep the login, it is trusted again.
        root = User.objects.create_superuser("root", password="not-listed-x9")
        self.assertEqual(self.api(root).patch(f"{USERS}{asha.pk}/", {"email": "asha@example.com"}, format="json")
                         .status_code, 200)
        Client().post("/accounts/password_reset/", {"email": "asha@example.com"})
        self.assertEqual(len(mail.outbox), 2)

    def test_a_role_given_by_any_door_lapses_what_the_keeper_set(self):
        asha = self.make("asha", [])
        root = User.objects.create_superuser("root", password="not-listed-x9")
        self.assertEqual(self.api(root).post(f"{USERS}{asha.pk}/grant/", {"role": "Controller"}, format="json")
                         .status_code, 200)
        self.assertFalse(self.signs_in("asha", PW))
        # A role the keeper holds itself lapses nothing.
        ravi = self.make("ravi", [])
        self.assertEqual(self.hrc.post(f"{USERS}{ravi.pk}/grant/", {"role": "HR Admin"}, format="json").status_code, 200)
        self.assertTrue(self.signs_in("ravi", PW))


class AProposalLapsesTests(KeeperAndHolder):
    """O192, O193, O194: a proposal is only as live as its proposer and its login, and counts as held."""

    def test_O192_a_proposal_lapses_with_its_proposers_right_to_propose(self):
        a = self.make("a", ["Bookkeeper"])
        self.hr.groups.clear()
        self.hr.is_active = False
        self.hr.save()
        got = (self.confirm(self.bk, self.pending(a, "Bookkeeper")).status_code, self.holds(a, "Bookkeeper"))
        self.assertEqual(got, (400, False), "a proposal by a keeper who has since lost the right is still confirmable")
        self.assertEqual(self.api(self.bk).get(self.PROPOSALS, {"waiting": "true"}).json(), [])
        # Another keeper proposes it afresh, and that one is confirmed.
        hr2 = self.person("hr2", "HR Admin")
        self.assertEqual(self.api(hr2).post(f"{USERS}{a.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 200)
        self.assertEqual(self.confirm(self.bk, self.pending(a, "Bookkeeper")).status_code, 200)
        self.assertEqual(list(RoleProposal.objects.filter(user=a).values_list("status", flat=True)),
                         ["confirmed", "lapsed"])

    def test_O192_taking_the_keepers_role_away_marks_its_proposals_lapsed(self):
        a = self.make("a", ["Bookkeeper"])
        hr2 = self.person("hr2", "HR Admin")
        self.assertEqual(self.api(hr2).post(f"{USERS}{self.hr.pk}/revoke/", {"role": "HR Admin"}, format="json")
                         .status_code, 200)
        self.assertEqual(RoleProposal.objects.get(user=a).status, "lapsed")
        self.assertEqual(self.confirm(self.bk, RoleProposal.objects.get(user=a)).status_code, 400)

    def test_O193_a_proposal_lapses_when_its_login_is_switched_off(self):
        asha = self.make("asha", ["Bookkeeper"])
        proposal = self.pending(asha, "Bookkeeper")
        self.assertEqual(self.hrc.post(f"{USERS}{asha.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertEqual(self.api(self.bk).get(self.PROPOSALS, {"waiting": "true"}).json(), [])
        confirmed = self.confirm(self.bk, proposal).status_code
        self.assertEqual((confirmed, self.holds(asha, "Bookkeeper")), (400, False),
                         "a role is given to a switched-off login by confirming its pending proposal")
        self.assertEqual(RoleProposal.objects.get(pk=proposal.pk).status, "lapsed")

    def test_O194_a_login_with_a_role_proposed_is_linked_as_if_it_held_it(self):
        import datetime

        from apps.hr.models import Employee
        from apps.hr.tests_people import make_employee_party

        asha = self.make("asha", ["Controller"])
        boss = Employee.objects.create(party=make_employee_party("B1", "B1"), employee_number="B1",
                                       hire_date=datetime.date(2025, 1, 1))
        linked = self.hrc.patch(f"/api/hr/employees/{boss.pk}/", {"user": asha.pk}, format="json")
        self.assertEqual(linked.status_code, 400, linked.content)
        self.assertIn("Controller", str(linked.content))
        # Withdrawn, it is an empty login again, and linked.
        self.assertEqual(self.hrc.post(f"{self.PROPOSALS}{self.pending(asha, 'Controller').pk}/decline/", {},
                                       format="json").status_code, 200)
        self.assertEqual(self.hrc.patch(f"/api/hr/employees/{boss.pk}/", {"user": asha.pk}, format="json")
                         .status_code, 200)
