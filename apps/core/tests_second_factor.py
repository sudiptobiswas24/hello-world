"""
Two-factor sign-in (L12), as the people in their roles, through the pages
and the API a browser and an integration use.

Who needs it is data (setup_roles.SECOND_FACTOR): the Controller, AR and
AP Managers (they post payments), the Payroll Officer (pay), the HR Admin
(logins); a Stores Manager may choose it. A password alone reaches
nothing the code guards; five wrong codes lock the code step; a code and
a recovery code each sign in once; a lost phone is reset by whoever may
set the login's password, recorded, and the login enrols again; a leaver
loses its factor.
"""

import base64
import datetime
import re

import pyotp
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import second_factor
from .history import RecordEvent
from .second_factor import SESSION_KEY, RecoveryCode, SecondFactor


# For other tests too: a second factor given as a person at the door would give it.
def factor_for_tests(user):
    """For tests: `user`'s confirmed factor, made if it has none, ready for the code the phone shows now."""
    factor, _ = SecondFactor.objects.get_or_create(user=user, defaults={"secret": pyotp.random_base32()})
    SecondFactor.objects.filter(pk=factor.pk).update(confirmed_at=factor.confirmed_at or timezone.now(),
                                                     last_step=None, failures=0, locked_until=None)
    factor.refresh_from_db()
    return factor


def staff_session(client, user):
    """
    The one way a test signs a login in through a session as the door
    would leave it: the password given (force_login) and, for a login that
    needs one, its second factor passed in that session. For tests that
    reach the admin, or the API through a session, as a superuser, a staff
    login or a holder of a guarded right. The product guard is untouched.
    """
    for_tests(client, user, sign_in=True)
    return client


def for_tests(client, user, sign_in=False):
    """For tests: a client's session as one that gave its code; `sign_in`, force_login first."""
    if sign_in:
        client.force_login(user)
    factor = factor_for_tests(user)
    session = client.session
    session[SESSION_KEY] = factor.pk
    session.save()
    return factor

PW = "Mill-Gate-Pw-2026-qz"
ME = "/api/core/me/"


class SecondFactorCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def person(self, name, *roles):
        user = User.objects.create_user(name, password=PW)
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def password(self, username, wanted="/app/"):
        web = Client()
        answer = web.post(f"/accounts/login/?next={wanted}", {"username": username, "password": PW})
        self.assertEqual(answer.status_code, 302, answer.content[:300])
        return web, answer["Location"]

    def enrolled(self, user):
        return SecondFactor.objects.create(user=user, secret=pyotp.random_base32(), confirmed_at=timezone.now())

    def code(self, factor, later=0):
        return pyotp.TOTP(factor.secret).at(timezone.now() + datetime.timedelta(seconds=later))


class TheGuardedRightsAreOnFileFromTheFirstMigrateTests(TransactionTestCase):
    """Review F3, F10: core 0038 puts every guarded right on file, permissions made if missing; reversed, it goes."""

    tags = {"migration"}

    def test_seeded_forward_and_taken_back(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        before, after = [("core", "0037_apikey_apikeyuse_integration_apikey_integration")], [("core", "0038_guarded_rights_seeded")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        state = executor.loader.project_state(before[0]).apps
        self.assertEqual(state.get_model("core", "GuardedRight").objects.count(), 0)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        state = executor.loader.project_state(after[0]).apps
        kept = sorted(f"{row.permission.content_type.app_label}.{row.permission.codename}"
                      for row in state.get_model("core", "GuardedRight").objects.select_related("permission__content_type"))
        from .management.commands.setup_roles import SECOND_FACTOR

        self.assertEqual(kept, sorted(SECOND_FACTOR))
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        self.assertEqual(executor.loader.project_state(before[0]).apps.get_model("core", "GuardedRight").objects.count(), 0)
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


class WhoNeedsItTests(SecondFactorCase):
    def test_the_roles_holding_money_pay_or_logins_need_it_and_the_rest_may_choose(self):
        # Review F3: the Bookkeeper pays expense claims and the Purchasing Clerk keeps vendors' bank accounts.
        needs = {role: second_factor.required(self.person(role.replace(" ", "").lower(), role))
                 for role in ("Controller", "AR Manager", "AP Manager", "Payroll Officer", "HR Admin",
                              "Bookkeeper", "Purchasing Clerk", "Stores Manager", "Employee Self Service")}
        self.assertEqual(needs, {"Controller": True, "AR Manager": True, "AP Manager": True, "Payroll Officer": True,
                                 "HR Admin": True, "Bookkeeper": True, "Purchasing Clerk": True,
                                 "Stores Manager": False, "Employee Self Service": False})

    def test_a_guarded_right_given_on_its_own_needs_it_too(self):
        from django.contrib.auth.models import Permission

        clerk = self.person("clerk", "Stores Manager")
        clerk.user_permissions.add(Permission.objects.get(content_type__app_label="accounting", codename="post_payment"))
        self.assertTrue(second_factor.required(User.objects.get(pk=clerk.pk)))

    def test_which_rights_is_the_setup_data_not_code(self):
        from .management.commands.setup_roles import SECOND_FACTOR

        self.assertEqual(sorted(str(row) for row in second_factor.GuardedRight.objects.all()), sorted(SECOND_FACTOR))


class APasswordAloneTests(SecondFactorCase):
    def test_a_controllers_password_alone_reaches_nothing_until_the_code(self):
        controller = self.person("ctl", "Controller")
        factor = self.enrolled(controller)
        web, step = self.password("ctl", "/app/accounting/payments")
        self.assertTrue(step.startswith("/accounts/second-factor/"), step)
        self.assertEqual(web.get(ME).status_code, 403)
        self.assertEqual(web.get("/api/accounting/payments/").status_code, 403)
        self.assertEqual(web.get("/admin/").status_code, 302)
        # Asked to sign in again, the half-signed-in session is sent on to the code.
        self.assertTrue(web.get("/accounts/login/?next=/app/")["Location"].startswith("/accounts/second-factor/"))
        answer = web.post(step, {"code": self.code(factor)})
        self.assertEqual((answer.status_code, answer["Location"]), (302, "/app/accounting/payments"))
        self.assertEqual(web.get(ME).json()["username"], "ctl")
        self.assertEqual(web.get("/api/accounting/payments/").status_code, 200)

    def test_http_basic_is_a_password_alone_and_is_refused(self):
        self.person("ctl", "Controller")
        self.person("stores", "Stores Manager")
        said = {}
        for username in ("ctl", "stores"):
            api = APIClient()
            api.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(f"{username}:{PW}".encode()).decode())
            answer = api.get(ME)
            said[username] = (answer.status_code, "signs in with a second factor" in answer.content.decode())
        self.assertEqual(said, {"ctl": (403, True), "stores": (200, False)})

    def test_the_admin_site_signs_in_through_the_same_door(self):
        answer = Client().get("/admin/login/?next=/admin/")
        self.assertEqual((answer.status_code, answer["Location"]), (302, "/accounts/login/?next=/admin/"))

    def test_a_login_needing_none_signs_in_with_the_password(self):
        self.person("stores", "Stores Manager")
        web, landed = self.password("stores")
        self.assertEqual(landed, "/app/")
        self.assertEqual(web.get(ME).json()["second_factor"], {"enrolled": False, "required": False})


class EnrolmentTests(SecondFactorCase):
    def enrol(self, web, step):
        page = web.get(step).content.decode()
        secret = re.search(r'<code id="secret">([A-Z2-7]+)</code>', page).group(1)
        self.assertIn("<svg", page)
        done = web.post(step, {"code": pyotp.TOTP(secret).now(), "password": PW})
        self.assertEqual(done.status_code, 200)
        return secret, re.findall(r"<li>([A-Z2-9]{5}-[A-Z2-9]{5})</li>", done.content.decode())

    def test_a_payroll_officer_enrols_at_the_first_sign_in_and_gets_ten_codes_shown_once(self):
        officer = self.person("pay", "Payroll Officer")
        web, step = self.password("pay")
        self.assertEqual(web.get(ME).status_code, 403)
        wrong = web.post(step, {"code": "000000", "password": PW})
        self.assertIn("did not match", wrong.content.decode())
        secret, codes = self.enrol(web, step)
        self.assertEqual(len(set(codes)), 10)
        self.assertEqual(web.get(ME).json()["second_factor"], {"enrolled": True, "required": True})
        kept = list(RecoveryCode.objects.filter(factor__user=officer).values_list("digest", flat=True))
        self.assertEqual(len(kept), 10)
        self.assertFalse(set(kept) & {code.replace("-", "") for code in codes}, "a recovery code kept as it is")
        self.assertTrue(RecordEvent.objects.filter(object_id=officer.pk, action="enrol_second_factor").exists())
        # A second sign-in asks the code, not the enrolment.
        second, step = self.password("pay")
        self.assertNotIn('id="secret"', second.get(step).content.decode())

    def test_a_session_alone_enrols_no_phone_the_password_is_asked_again(self):
        """Review F2 (p13): a password-only session of an optional login, a cookie lifted at a shared PC."""
        stores = self.person("stores", "Stores Manager")
        lifted = Client()
        lifted.force_login(stores)
        page = lifted.get("/accounts/second-factor/")
        secret = re.search(r'<code id="secret">([A-Z2-7]+)</code>', page.content.decode()).group(1)
        for password in ("", "not-the-password"):
            refused = lifted.post("/accounts/second-factor/", {"code": pyotp.TOTP(secret).now(), "password": password})
            self.assertIn("Type the password you signed in with", refused.content.decode())
        self.assertIsNone(second_factor.confirmed(stores))
        _, landed = self.password("stores")
        self.assertEqual(landed, "/app/", "the owner was asked for a code from a phone that is not theirs")

    def test_a_stores_manager_may_choose_it_and_then_needs_it(self):
        self.person("stores", "Stores Manager")
        web, _ = self.password("stores")
        secret, codes = self.enrol(web, "/accounts/second-factor/?next=/app/")
        again, step = self.password("stores")
        self.assertTrue(step.startswith("/accounts/second-factor/"))
        self.assertEqual(again.get(ME).status_code, 403)


class ACodeTests(SecondFactorCase):
    def setUp(self):
        super().setUp()
        self.controller = self.person("ctl", "Controller")
        self.factor = self.enrolled(self.controller)

    def test_five_wrong_codes_lock_the_code_step_for_a_while(self):
        web, step = self.password("ctl")
        for _ in range(5):
            web.post(step, {"code": "123456"})
        refused = web.post(step, {"code": self.code(self.factor)})
        self.assertEqual(refused.status_code, 200)
        self.assertIn("locked for a while", refused.content.decode())
        self.assertEqual(web.get(ME).status_code, 403)
        SecondFactor.objects.filter(pk=self.factor.pk).update(locked_until=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(web.post(step, {"code": self.code(self.factor)}).status_code, 302)
        self.assertEqual(web.get(ME).status_code, 200)

    def test_each_lock_in_a_row_is_twice_the_last_a_day_at_most(self):
        """The review's unproven back-off: five guesses a quarter of an hour was 480 a day."""
        web, step = self.password("ctl")
        lengths = []
        for _ in range(3):
            for _ in range(5):
                web.post(step, {"code": "123456"})
            factor = SecondFactor.objects.get(pk=self.factor.pk)
            lengths.append(round((factor.locked_until - timezone.now()).total_seconds() / 60))
            SecondFactor.objects.filter(pk=self.factor.pk).update(locked_until=timezone.now())
        self.assertEqual(lengths, [15, 30, 60])
        self.assertEqual(second_factor.lock_for(12), datetime.timedelta(hours=24))
        self.assertEqual(web.post(step, {"code": self.code(self.factor)}).status_code, 302)
        self.assertEqual(SecondFactor.objects.get(pk=self.factor.pk).lockouts, 0, "a right code starts it again")

    def test_digits_of_another_script_are_a_wrong_code_not_a_server_error(self):
        web, step = self.password("ctl")
        answer = Client(raise_request_exception=True)
        answer.post("/accounts/login/", {"username": "ctl", "password": PW})
        page = answer.post(step, {"code": "\u0663" * 6})
        self.assertIn("did not match", page.content.decode())

    def test_a_code_signs_in_once(self):
        code = self.code(self.factor)
        web, step = self.password("ctl")
        self.assertEqual(web.post(step, {"code": code}).status_code, 302)
        other, step = self.password("ctl")
        self.assertEqual(other.post(step, {"code": code}).status_code, 200)
        self.assertEqual(other.get(ME).status_code, 403)
        # The next one the phone shows does.
        self.assertEqual(other.post(step, {"code": self.code(self.factor, later=30)}).status_code, 302)

    def test_a_recovery_code_signs_in_once(self):
        (code,) = second_factor.new_recovery_codes(self.factor)[:1]
        web, step = self.password("ctl")
        self.assertEqual(web.post(step, {"code": code.lower()}).status_code, 302)
        self.assertEqual(web.get(ME).status_code, 200)
        other, step = self.password("ctl")
        self.assertEqual(other.post(step, {"code": code}).status_code, 200)
        self.assertEqual(other.get(ME).status_code, 403)
        self.assertEqual(RecoveryCode.objects.filter(factor=self.factor, used_at__isnull=True).count(), 9)


class ALostPhoneTests(SecondFactorCase):
    USERS = "/api/core/users/"

    def api(self, user):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        return client

    def test_whoever_keeps_logins_resets_it_recorded_and_the_login_enrols_again(self):
        hr = self.person("hr", "HR Admin")
        other = self.person("hr2", "HR Admin")
        factor = self.enrolled(other)
        web, step = self.password("hr2")
        self.assertEqual(web.post(step, {"code": self.code(factor)}).status_code, 302)
        self.assertEqual(web.get(ME).status_code, 200)
        reset = self.api(hr).post(f"{self.USERS}{other.pk}/reset_second_factor/", {}, format="json")
        self.assertEqual(reset.status_code, 200, reset.content)
        self.assertEqual(reset.json()["second_factor"], False)
        self.assertTrue(RecordEvent.objects.filter(object_id=other.pk, action="reset_second_factor", who=hr).exists())
        # Every session the login had is ended (review F2), and the next sign-in enrols again.
        self.assertEqual(web.get(ME).status_code, 403)
        self.assertTrue(web.get("/accounts/second-factor/")["Location"].startswith("/accounts/login/"))
        self.assertEqual(self.api(hr).post(f"{self.USERS}{other.pk}/reset_second_factor/", {}, format="json")
                         .status_code, 400)
        again, step = self.password("hr2")
        self.assertIn('id="secret"', again.get(step).content.decode())

    def test_a_controllers_is_reset_by_a_superuser_not_by_hr_and_it_enrols_again(self):
        hr = self.person("hr", "HR Admin")
        controller = self.person("ctl", "Controller")
        self.enrolled(controller)
        self.assertEqual(self.api(hr).post(f"{self.USERS}{controller.pk}/reset_second_factor/", {}, format="json")
                         .status_code, 403)
        root = User.objects.create_superuser("root", password=PW)
        self.assertEqual(self.api(root).post(f"{self.USERS}{controller.pk}/reset_second_factor/", {}, format="json")
                         .status_code, 200)
        web, step = self.password("ctl")
        self.assertIn('id="secret"', web.get(step).content.decode())

    def test_nobody_resets_their_own(self):
        hr = self.person("hr", "HR Admin")
        self.enrolled(hr)
        self.assertEqual(self.api(hr).post(f"{self.USERS}{hr.pk}/reset_second_factor/", {}, format="json")
                         .status_code, 400)

    def test_a_leaver_loses_the_factor_and_enrols_afresh_if_switched_on_again(self):
        hr = self.person("hr", "HR Admin")
        stores = self.person("stores", "Stores Manager")
        self.enrolled(stores)
        self.assertEqual(self.api(hr).post(f"{self.USERS}{stores.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertFalse(SecondFactor.objects.filter(user=stores).exists())
        self.assertEqual(RecoveryCode.objects.count(), 0)


class ASoleAdministratorsWayBackTests(SecondFactorCase):
    """Review F8: one superuser, phone and recovery codes lost: a command on the server, recorded."""

    def test_the_server_command_resets_it_ends_the_sessions_and_says_so_in_the_history(self):
        from io import StringIO

        root = User.objects.create_superuser("root", password=PW)
        factor = SecondFactor.objects.create(user=root, secret=pyotp.random_base32(), confirmed_at=timezone.now())
        web = Client()
        for_tests(web, root, sign_in=True)
        self.assertEqual(web.get(ME).status_code, 200)
        said = StringIO()
        call_command("reset_second_factor", "root", stdout=said)
        self.assertIn("enrols again at its next sign-in", said.getvalue())
        self.assertFalse(SecondFactor.objects.filter(pk=factor.pk).exists())
        self.assertEqual(web.get(ME).status_code, 403)
        event = RecordEvent.objects.get(object_id=root.pk, action="reset_second_factor")
        self.assertEqual((event.who, "manage.py reset_second_factor" in event.summary), (None, True))
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("reset_second_factor", "root")


OLD_KEY = base64.urlsafe_b64encode(b"o" * 32).decode()
NEW_KEY = base64.urlsafe_b64encode(b"n" * 32).decode()


class TheSecretIsSealedAtRestTests(SecondFactorCase):
    """O255: a copy of the database, a backup included, held every login's second factor in clear."""

    def test_the_database_holds_the_secret_sealed_not_in_clear(self):
        from django.db import connection

        from .sealed import unseal

        factor = self.enrolled(self.person("ctl", "Controller"))
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM core_secondfactor WHERE id = %s", [factor.pk])
            row = " ".join(str(value) for value in cursor.fetchone())
        self.assertNotIn(factor.secret, row)
        stored = SecondFactor.objects.values_list("sealed_secret", flat=True).get(pk=factor.pk)
        self.assertEqual(unseal(stored), factor.secret)
        # And the code from that secret still signs in.
        web, step = self.password("ctl")
        self.assertEqual(web.post(step, {"code": self.code(factor)}).status_code, 302)

    def test_a_secret_no_key_opens_says_re_enrol_and_signs_nobody_in(self):
        """A restore without its key: never a server error, never a sign-in, never a guess counted."""
        with self.settings(FIELD_KEYS=[OLD_KEY]):
            factor = self.enrolled(self.person("ctl", "Controller"))
            right = self.code(factor)
        with self.settings(FIELD_KEYS=[NEW_KEY]):
            web, step = self.password("ctl")
            page = web.get(step)
            self.assertEqual(page.status_code, 200)
            self.assertIn("reset your second factor", page.content.decode())
            answer = web.post(step, {"code": right})
            self.assertEqual(answer.status_code, 200)
            self.assertIn("reset your second factor", answer.content.decode())
            self.assertEqual(web.get(ME).status_code, 403)
            self.assertFalse(second_factor.check(factor, right))
            factor.refresh_from_db()
            self.assertEqual((factor.failures, factor.locked_until), (0, None))

    def test_an_unconfirmed_secret_no_key_opens_is_replaced_at_enrolment(self):
        with self.settings(FIELD_KEYS=[OLD_KEY]):
            officer = self.person("pay", "Payroll Officer")
            SecondFactor.objects.create(user=officer, secret=pyotp.random_base32())
        with self.settings(FIELD_KEYS=[NEW_KEY]):
            web, step = self.password("pay")
            page = web.get(step).content.decode()
            secret = re.search(r'<code id="secret">([A-Z2-7]+)</code>', page).group(1)
            done = web.post(step, {"code": pyotp.TOTP(secret).now(), "password": PW})
            self.assertEqual(done.status_code, 200)
            self.assertIsNotNone(second_factor.confirmed(officer))

    def test_rotate_field_keys_seals_again_under_the_first_key(self):
        from io import StringIO

        from django.core.management.base import CommandError

        from .sealed import Unreadable, unseal

        with self.settings(FIELD_KEYS=[OLD_KEY]):
            factor = self.enrolled(self.person("ctl", "Controller"))
            secret = factor.secret
        with self.settings(FIELD_KEYS=[NEW_KEY, OLD_KEY]):
            out = StringIO()
            call_command("rotate_field_keys", stdout=out)
            self.assertIn("1 sealed again", out.getvalue())
        stored = SecondFactor.objects.values_list("sealed_secret", flat=True).get(pk=factor.pk)
        with self.settings(FIELD_KEYS=[NEW_KEY]):
            self.assertEqual(unseal(stored), secret)
        with self.settings(FIELD_KEYS=[OLD_KEY]), self.assertRaises(Unreadable):
            unseal(stored)
        # A value no key opens is named, left as it is, and the command fails so a script sees it.
        with self.settings(FIELD_KEYS=[OLD_KEY]):
            with self.assertRaisesMessage(CommandError, "no key opens"):
                call_command("rotate_field_keys", stdout=StringIO())
        self.assertEqual(SecondFactor.objects.values_list("sealed_secret", flat=True).get(pk=factor.pk), stored)


class TheSecretIsSealedByTheMigrationTests(TransactionTestCase):
    """O255: core 0041 seals every secret on file; backwards it opens them, and one no key opens is no sign-in."""

    tags = {"migration"}

    def test_sealed_forward_and_opened_backward(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        from .sealed import seal, unseal

        before, after = [("core", "0040_apikeyuse_prefix_apikeyuse_refused_and_more")], [
            ("core", "0041_secondfactor_sealed_secret")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        state = executor.loader.project_state(before[0]).apps
        user = state.get_model("auth", "User").objects.create(username="ctl")
        clear = pyotp.random_base32()
        state.get_model("core", "SecondFactor").objects.create(user_id=user.pk, secret=clear)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        state = executor.loader.project_state(after[0]).apps
        row = state.get_model("core", "SecondFactor").objects.get(user_id=user.pk)
        self.assertNotIn(clear, row.sealed_secret)
        self.assertEqual(unseal(row.sealed_secret), clear)

        executor = MigrationExecutor(connection)
        executor.migrate(before)
        state = executor.loader.project_state(before[0]).apps
        self.assertEqual(state.get_model("core", "SecondFactor").objects.get(user_id=user.pk).secret, clear)

        # Backwards, a secret no key opens becomes one no phone holds: no server error, no sign-in.
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        state = executor.loader.project_state(after[0]).apps
        with self.settings(FIELD_KEYS=[OLD_KEY]):
            state.get_model("core", "SecondFactor").objects.filter(user_id=user.pk).update(sealed_secret=seal(clear))
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        state = executor.loader.project_state(before[0]).apps
        reopened = state.get_model("core", "SecondFactor").objects.get(user_id=user.pk).secret
        self.assertNotEqual(reopened, clear)
        self.assertRegex(reopened, r"^[A-Z2-7]{32}$")

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
