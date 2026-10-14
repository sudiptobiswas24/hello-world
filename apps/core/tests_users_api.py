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


class AKeepersCredentialLapsesWithAnyRightTests(KeeperAndHolder):
    """
    O198: what the keeper set lapsed only when a role was given. A
    permission of its own or the admin site, given in the admin, left the
    keeper's password working; what was kept of it was a copy of its
    hash; and one set before the record began carried no word of who set
    it. Asked as the people in their roles; the admin's doors are the
    model's own (user_permissions.add, save), which its forms call.
    """

    def test_a_permission_of_its_own_given_in_the_admin_lapses_the_keepers_password(self):
        asha = self.make("asha", ["HR Admin"])
        self.assertTrue(self.signs_in("asha", PW))
        asha.user_permissions.add(Permission.objects.get(content_type__app_label="accounting",
                                                         codename="add_journalentry"))
        self.assertFalse(self.signs_in("asha", PW), "HR signs in with a permission it does not hold")

    def test_the_admin_site_given_lapses_it(self):
        asha = self.make("asha", ["HR Admin"])
        asha.is_staff = True
        asha.save()
        self.assertFalse(self.signs_in("asha", PW), "HR signs in to the admin site it cannot open")
        # One the keeper could give lapses nothing.
        ravi = self.make("ravi", [])
        ravi.groups.add(Group.objects.get(name="HR Admin"))
        self.assertTrue(self.signs_in("ravi", PW))

    def test_what_is_kept_of_a_password_is_no_copy_of_its_hash(self):
        from .roles import KeptCredential

        asha = self.make("asha", [])
        kept = KeptCredential.objects.get(user=asha, kind="password")
        # Nothing of it at all (review F3, F8): who set it, kept when it was set.
        self.assertEqual((kept.value, kept.set_by_id), ("", self.hr.pk))
        self.assertTrue(kept.in_force(asha))

    def test_a_password_set_before_the_record_is_said_to_the_confirmer_and_not_lapsed(self):
        from .roles import KeptCredential

        old = User.objects.create_user("old", password=PW)
        # As migration 0033 records a login it found with a password and no record of who set it.
        KeptCredential.objects.create(user=old, kind="password", value="", set_by=None)
        self.assertEqual(self.hrc.post(f"{USERS}{old.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 200)
        [row] = self.api(self.bk).get(self.PROPOSALS, {"waiting": "true"}).json()
        self.assertTrue(row["credentials"].startswith("Who set old's password is not on record"), row)
        self.assertEqual(self.confirm(self.bk, self.pending(old, "Bookkeeper")).status_code, 200)
        self.assertTrue(self.signs_in("old", PW))


from django.test import TransactionTestCase, tag  # noqa: E402


@tag("migration")
class KeptPasswordsAreDigestedTests(TransactionTestCase):
    """O198: migration 0033 digests the hashes 0032 kept and records, with no setter, what nobody recorded."""

    before = [("core", "0032_kept_credentials")]
    after = [("core", "0033_kept_credentials_digested")]

    def test_a_kept_hash_becomes_its_digest_and_an_unrecorded_password_is_recorded(self):
        from django.contrib.auth.hashers import make_password
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from django.utils.crypto import salted_hmac

        def fingerprint(password_hash):
            return salted_hmac("apps.core.roles.KeptCredential.password", password_hash, algorithm="sha256").hexdigest()

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        user, kept = apps.get_model("auth", "User"), apps.get_model("core", "KeptCredential")
        keeper = user.objects.create(username="hr", password=make_password(PW))
        asha = user.objects.create(username="asha", password=make_password(PW))
        kept.objects.create(user=asha, kind="password", value=asha.password, set_by=keeper)
        user.objects.create(username="locked", password=make_password(None))

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        kept = executor.loader.project_state(self.after).apps.get_model("core", "KeptCredential")
        rows = {(row.user.username, row.kind): (row.value, row.set_by_id) for row in kept.objects.all()}
        self.assertEqual(rows, {("asha", "password"): (fingerprint(asha.password), keeper.pk),
                                ("hr", "password"): (fingerprint(keeper.password), None)})
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


class NobodyActsThroughALoginTheyKeepTests(KeeperAndHolder):
    """
    O195: "nobody acts on their own login" compared logins only. HR made
    puppet holding HR Admin (its to give, so its first password stays
    HR's), and as puppet proposed Bookkeeper for HR's own login; a real
    Bookkeeper confirmed, seeing "proposed by puppet". A login whose
    password or email someone set is theirs to act through, and nothing
    they do through it is for themselves. As the people in their roles.
    """

    def test_the_keeper_proposes_nothing_for_itself_through_a_login_it_made(self):
        puppet = self.make("puppet", ["HR Admin"])
        proposed = self.api(puppet).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        self.assertEqual(proposed.status_code, 400, proposed.content)
        self.assertIn("signs in as you", str(proposed.content))
        self.assertFalse(RoleProposal.objects.filter(user=self.hr).exists())

    def test_one_made_before_the_rule_is_not_confirmed(self):
        puppet = self.make("puppet", ["HR Admin"])
        proposal = RoleProposal.objects.create(user=self.hr, group=Group.objects.get(name="Bookkeeper"),
                                               proposed_by=puppet)
        self.assertEqual(self.confirm(self.bk, proposal).status_code, 400)
        self.assertFalse(self.holds(self.hr, "Bookkeeper"))

    # Review F1: this test was test_once_its_person_sets_their_own_password_it_is_theirs, and encoded the
    # bypass: the "person" setting puppet's own password was HR, signed in as puppet. Who made the login is
    # a fact no password change ends; the way back is someone other than its maker confirming a role for it.
    def test_its_own_password_changed_frees_no_login_from_its_maker(self):
        puppet = self.make("puppet", ["HR Admin"])
        own = self.api(puppet).post(f"{USERS}{puppet.pk}/set_password/", {"password": "Their-Own-Pw-2026-zq"},
                                    format="json")
        self.assertEqual(own.status_code, 200, own.content)
        proposed = self.api(puppet).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        self.assertEqual(proposed.status_code, 400, proposed.content)
        self.assertFalse(RoleProposal.objects.filter(user=self.hr).exists())

    def test_laundered_through_djangos_password_change_or_a_second_login_it_is_still_the_makers(self):
        """The reviewer's: the password-change page, and p1 making p2 then changing its own password."""
        from django.test import Client

        puppet = self.make("puppet", ["HR Admin"])
        web = Client()
        self.assertTrue(web.login(username="puppet", password=PW))
        self.assertEqual(web.post("/accounts/password_change/", {
            "old_password": PW, "new_password1": "HR-Chosen-Pw-2026-zq", "new_password2": "HR-Chosen-Pw-2026-zq"})
            .status_code, 302)
        self.assertEqual(self.api(puppet).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 400)
        p1 = self.make("p1", ["HR Admin"])
        self.assertEqual(self.api(p1).post(USERS, {"username": "p2", "password": PW, "roles": ["HR Admin"]},
                                           format="json").status_code, 201)
        self.api(p1).post(f"{USERS}{p1.pk}/set_password/", {"password": "HR-Chosen-Pw-2026-zq"}, format="json")
        p2 = User.objects.get(username="p2")
        self.assertEqual(self.api(p2).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 400)

    def test_the_way_back_is_someone_other_than_the_maker_confirming_a_role_for_it(self):
        puppet = self.make("puppet", ["HR Admin"])
        self.assertEqual(self.hrc.post(f"{USERS}{puppet.pk}/grant/", {"role": "Bookkeeper"}, format="json")
                         .status_code, 200)
        self.assertEqual(self.confirm(self.bk, self.pending(puppet, "Bookkeeper")).status_code, 200)
        # The password HR chose, and any HR set as puppet, no longer signs in: puppet is its person's now.
        self.assertFalse(self.signs_in("puppet", PW))
        proposed = self.api(puppet).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(proposed.status_code, 200, proposed.content)

    def test_the_confirmer_is_told_who_can_sign_in_as_the_login_that_proposed(self):
        puppet = self.make("puppet", ["HR Admin"])
        third = self.make("third", [])
        self.api(puppet).post(f"{USERS}{third.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        row = self.api(self.bk).get(f"{self.PROPOSALS}{self.pending(third, 'Bookkeeper').pk}/").json()
        self.assertIn("hr set puppet's password, so may sign in as puppet, who proposed this.", row["credentials"])


class ACredentialIsKeptAtTheEventTests(KeeperAndHolder):
    """
    Review F2, F3, F8: who set a password was read off its hash, so a
    re-hash at sign-in or a new SECRET_KEY forgot the keeper, and a keeper
    demoted or switched off still signed in as the login it made. Now it
    is kept when set, ends at the next change, and lapses with the
    setter's reach. The reviewer's probes.
    """

    def root(self):
        User.objects.create_superuser("root", password="x-root-pw-2026")
        return self.api(User.objects.get(username="root"))

    def test_a_demoted_keeper_no_longer_signs_in_as_the_login_it_made(self):
        root = self.root()
        self.make("puppet", ["HR Admin"])
        self.assertEqual(root.post(f"{USERS}{self.hr.pk}/revoke/", {"role": "HR Admin"}, format="json").status_code, 200)
        self.assertFalse(self.signs_in("puppet", PW), "a login holding HR Admin is open to someone who no longer does")

    def test_a_switched_off_keeper_no_longer_signs_in_as_the_login_it_made(self):
        root = self.root()
        self.make("puppet", ["HR Admin"])
        self.assertEqual(root.post(f"{USERS}{self.hr.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertFalse(self.signs_in("puppet", PW), "a leaver who made a login still knows its password")

    def test_a_keeper_losing_the_right_through_its_role_knows_nothing_it_set(self):
        self.make("puppet", ["HR Admin"])
        Group.objects.get(name="HR Admin").permissions.remove(
            Permission.objects.get(codename="change_user", content_type__app_label="auth"))
        self.assertFalse(self.signs_in("puppet", PW))

    def test_a_re_hash_at_sign_in_or_a_new_secret_key_forgets_no_keeper(self):
        from django.test import Client, override_settings

        from .roles import KeptCredential, who_knows

        puppet = self.make("puppet", ["HR Admin"])
        hashers = ["django.contrib.auth.hashers.PBKDF2PasswordHasher", "django.contrib.auth.hashers.MD5PasswordHasher"]
        with override_settings(PASSWORD_HASHERS=hashers):
            self.assertTrue(Client().login(username="puppet", password=PW))
        puppet = User.objects.get(pk=puppet.pk)
        self.assertTrue(puppet.password.startswith("pbkdf2"), "the hash was upgraded")
        self.assertEqual(set(KeptCredential.objects.filter(user=puppet).values_list("kind", "set_by")),
                         {("login", self.hr.pk), ("password", self.hr.pk)})
        with override_settings(SECRET_KEY="a-rotated-key-" + "x" * 40):
            self.assertIn(self.hr.pk, who_knows(puppet))
        # And the lapse still finds it.
        puppet.user_permissions.add(Permission.objects.get(content_type__app_label="accounting",
                                                           codename="add_journalentry"))
        self.assertFalse(self.signs_in("puppet", PW))


class APersonsLoginIsGivenWhatItsLinkWouldTakeTests(KeeperAndHolder):
    """
    O196, O194 in reverse: HR linked an empty login to an employee, then
    proposed Controller for it, and a Controller confirmed: a Controller
    login linked to a person, which the link rule refuses HR. One rule
    (users_api.linked_holding), read at the link and at the role.
    """

    def setUp(self):
        import datetime

        from apps.hr.models import Employee
        from apps.hr.tests_people import make_employee_party

        super().setUp()
        self.ctl = self.person("ctl", "Controller")
        self.b1 = self.make("b1", [])
        self.boss = Employee.objects.create(party=make_employee_party("B1", "B1"), employee_number="B1",
                                            hire_date=datetime.date(2025, 1, 1))
        linked = self.hrc.patch(f"/api/hr/employees/{self.boss.pk}/", {"user": self.b1.pk}, format="json")
        self.assertEqual(linked.status_code, 200, linked.content)

    def test_the_keeper_proposes_no_role_the_link_would_have_refused(self):
        proposed = self.hrc.post(f"{USERS}{self.b1.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(proposed.status_code, 400, proposed.content)
        self.assertIn("Controller", str(proposed.content))
        self.assertFalse(RoleProposal.objects.filter(user=self.b1).exists())
        # One the keeper gives itself, the link takes.
        self.assertEqual(self.hrc.post(f"{USERS}{self.b1.pk}/grant/", {"role": "HR Admin"}, format="json")
                         .status_code, 200)

    def test_one_proposed_before_the_rule_is_not_confirmed_by_a_holder_who_keeps_no_logins(self):
        proposal = RoleProposal.objects.create(user=self.b1, group=Group.objects.get(name="Controller"),
                                               proposed_by=self.hr)
        got = (self.confirm(self.ctl, proposal).status_code, self.holds(self.b1, "Controller"))
        self.assertEqual(got, (400, False), "a Controller login is linked to a person by confirming its role")


@tag("migration")
class KeptCredentialsAtTheEventTests(TransactionTestCase):
    """Review F1, F3, F8: 0034 keeps a password still as set with no value, drops one changed since, and records makers."""

    before = [("core", "0033_kept_credentials_digested")]
    after = [("core", "0034_kept_credentials_at_the_event")]

    def test_a_password_still_as_set_stands_one_changed_goes_and_makers_are_recorded(self):
        from django.contrib.auth.hashers import make_password
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils.crypto import salted_hmac

        def fingerprint(password_hash):
            return salted_hmac("apps.core.roles.KeptCredential.password", password_hash, algorithm="sha256").hexdigest()

        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        user, kept = apps.get_model("auth", "User"), apps.get_model("core", "KeptCredential")
        event, types = apps.get_model("core", "RecordEvent"), apps.get_model("contenttypes", "ContentType")
        group = apps.get_model("auth", "Group")
        hr_admin, bookkeeper = group.objects.create(name="HR Admin"), group.objects.create(name="Bookkeeper")
        keeper = user.objects.create(username="hr", password=make_password(PW))
        keeper.groups.add(hr_admin)
        same = user.objects.create(username="same", password=make_password(PW))
        moved = user.objects.create(username="moved", password=make_password(PW))
        above = user.objects.create(username="above", password=make_password(PW))
        above.groups.add(bookkeeper)
        kept.objects.create(user=same, kind="password", value=fingerprint(same.password), set_by=keeper)
        kept.objects.create(user=moved, kind="password", value=fingerprint("an-earlier-hash"), set_by=keeper)
        logins = types.objects.get_or_create(app_label="auth", model="user")[0]
        for login in (same, moved, above):
            event.objects.create(content_type=logins, object_id=login.pk, who=keeper, kind="created")

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        kept = executor.loader.project_state(self.after).apps.get_model("core", "KeptCredential")
        rows = {(row.user.username, row.kind): (row.value, row.set_by_id, row.lapsed)
                for row in kept.objects.exclude(set_by__isnull=True)}
        self.assertEqual(rows, {("same", "password"): ("", keeper.pk, False),
                                ("same", "login"): ("", keeper.pk, False),
                                ("moved", "login"): ("", keeper.pk, False),
                                ("above", "login"): ("", keeper.pk, True)})
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
