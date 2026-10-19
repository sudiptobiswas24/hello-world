"""
Probes for the review of fix_sec3 (g22 review_sec): L12 (two-factor, session
time-outs, API keys) and O216/O217. Each test states one fact about who can
do what; a FAIL here is a defect on the tip (24fb445), not a typo in the
test. Nothing here edits product code.

Run: PYTHONPATH=<scratchpad> DJANGO_SETTINGS_MODULE=fastsettings \
     .venv/bin/python manage.py test apps.core.tests_probe_review_sec --settings=fastsettings
"""

import datetime
import hashlib
import json
import os
import re
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock

import pyotp
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import URLResolver, get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from . import api_keys, second_factor, session_limits
from .api_keys import ApiKey, ApiKeyUse
from .history import RecordEvent
from .second_factor import SESSION_KEY, RecoveryCode, SecondFactor
from .tests_second_factor import ME, PW, SecondFactorCase, for_tests, staff_session
from .tests_users_api import PW as KEEPER_PW
from .tests_users_api import USERS, KeeperAndHolder

OWN = "Worker-Own-Chosen-Pw-26"


def api_as(user):
    client = APIClient()
    client.force_authenticate(User.objects.get(pk=user.pk))
    return client


def _walk(patterns, prefix=""):
    for each in patterns:
        raw = str(each.pattern)
        if isinstance(each, URLResolver):
            yield from _walk(each.url_patterns, prefix + raw.lstrip("^"))
        else:
            yield prefix + raw.lstrip("^")


def every_get_path():
    """Every route a browser or client could GET, detail routes with a pk of 1."""
    seen = set()
    for raw in _walk(get_resolver().url_patterns):
        raw = raw.rstrip("$")
        raw = re.sub(r"\(\?P<[a-z_0-9]+>[^)]*\)", "1", raw)
        raw = raw.replace("\\.", ".").replace("?", "")
        if "(" in raw or "<" in raw or "[" in raw or "*" in raw:
            continue
        if raw.startswith(("static", "accounts", "app", "favicon", "healthz")):
            continue
        seen.add("/" + raw.lstrip("/"))
    return sorted(seen)


class Probe(SecondFactorCase):
    def root(self):
        return User.objects.create_superuser("root", password=PW)

    def api(self, user):
        return api_as(user)

    def passed_client(self, user):
        web = Client()
        staff_session(web, user)
        return web

    def password_only_client(self, user):
        web = Client()
        web.force_login(user)
        return web


# ---------------------------------------------------------------------------
# 1. Two-factor
# ---------------------------------------------------------------------------
class TwoFactorProbes(Probe):
    def test_p01_a_code_given_once_does_not_sign_in_a_second_time(self):
        ctl = self.person("ctl", "Controller")
        factor = self.enrolled(ctl)
        code = self.code(factor)
        first, step = self.password("ctl")
        self.assertEqual(first.post(step, {"code": code}).status_code, 302)
        second, step2 = self.password("ctl")
        again = second.post(step2, {"code": code})
        self.assertEqual(again.status_code, 200, "a code used a second time signed in")
        self.assertEqual(second.get(ME).status_code, 403)

    def test_p02_codes_two_windows_away_are_refused_and_one_window_away_is_taken_once(self):
        ctl = self.person("ctl", "Controller")
        factor = self.enrolled(ctl)
        for later in (-90, 90):
            web, step = self.password("ctl")
            self.assertEqual(web.post(step, {"code": self.code(factor, later)}).status_code, 200,
                             f"a code from {later}s away signed in")
        SecondFactor.objects.filter(pk=factor.pk).update(failures=0, locked_until=None)
        # One window either side is the usual tolerance for a phone's clock; it signs in once.
        web, step = self.password("ctl")
        self.assertEqual(web.post(step, {"code": self.code(factor, 30)}).status_code, 302)
        web2, step2 = self.password("ctl")
        self.assertEqual(web2.post(step2, {"code": self.code(factor, 30)}).status_code, 200)
        # The code of the window before the one taken is refused too (steps only go forward).
        web3, step3 = self.password("ctl")
        self.assertEqual(web3.post(step3, {"code": self.code(factor, 0)}).status_code, 200)

    def test_p03_a_recovery_code_signs_in_once_and_only_its_own_login(self):
        a, b = self.person("ctla", "Controller"), self.person("ctlb", "Controller")
        fa, fb = self.enrolled(a), self.enrolled(b)
        codes_a = second_factor.new_recovery_codes(fa)
        second_factor.new_recovery_codes(fb)
        web, step = self.password("ctlb")
        self.assertEqual(web.post(step, {"code": codes_a[0]}).status_code, 200, "B signed in with A's recovery code")
        SecondFactor.objects.filter(pk=fb.pk).update(failures=0, locked_until=None)
        web, step = self.password("ctla")
        self.assertEqual(web.post(step, {"code": codes_a[0]}).status_code, 302)
        self.assertTrue(RecordEvent.objects.filter(action="recovery_code").exists(), "the use is not in the history")
        web, step = self.password("ctla")
        self.assertEqual(web.post(step, {"code": codes_a[0]}).status_code, 200, "a recovery code signed in twice")
        # lower case and without the hyphen is the same code, still once
        web, step = self.password("ctla")
        self.assertEqual(web.post(step, {"code": codes_a[1].lower().replace("-", "")}).status_code, 302)

    def test_p04_five_wrong_codes_lock_the_login_including_for_the_right_code(self):
        ctl = self.person("ctl", "Controller")
        factor = self.enrolled(ctl)
        web, step = self.password("ctl")
        for _ in range(5):
            web.post(step, {"code": "000000"})
        right = web.post(step, {"code": self.code(factor)})
        self.assertEqual(right.status_code, 200, "the right code signed in while locked")
        self.assertIn("locked", right.content.decode())
        # a fresh password sign-in does not clear the lock
        other, step2 = self.password("ctl")
        self.assertEqual(other.post(step2, {"code": self.code(factor)}).status_code, 200)
        # the lock is on the factor, so no other door takes a code: the admin's sign-in only forwards.
        admin = Client().post("/admin/login/", {"username": "ctl", "password": PW, "code": self.code(factor)})
        self.assertEqual(admin.status_code, 302)
        self.assertIn("/accounts/login/", admin["Location"])

    def test_p05_a_non_ascii_digit_is_a_wrong_code_not_a_server_error(self):
        ctl = self.person("ctl", "Controller")
        self.enrolled(ctl)
        web, step = self.password("ctl")
        web = Client(raise_request_exception=False, **{})
        web.post("/accounts/login/", {"username": "ctl", "password": PW})
        answer = web.post(second_factor.STEP_URL, {"code": "٣٣٣٣٣٣"})
        self.assertEqual(answer.status_code, 200, f"a code of Arabic-Indic digits answered {answer.status_code}")
        # and in the enrolment page
        fresh = self.person("fresh", "Controller")
        web2 = Client(raise_request_exception=False)
        web2.post("/accounts/login/", {"username": "fresh", "password": PW})
        answer2 = web2.post(second_factor.STEP_URL, {"code": "٣٣٣٣٣٣"})
        self.assertEqual(answer2.status_code, 200, f"enrolment given Arabic-Indic digits answered {answer2.status_code}")

    def test_p06_after_a_reset_the_old_session_cannot_enrol_a_new_phone_without_the_password(self):
        ctl = self.person("ctl", "Controller")
        factor = self.enrolled(ctl)
        stolen = self.passed_client(ctl)  # a live session that passed the old phone's code
        self.assertEqual(stolen.get(ME).status_code, 200)
        reset = self.api(self.root()).post(f"{USERS}{ctl.pk}/reset_second_factor/", {}, format="json")
        self.assertEqual(reset.status_code, 200, reset.content)
        self.assertEqual(stolen.get(ME).status_code, 403, "the reset did not end the session")
        page = stolen.get(second_factor.STEP_URL)
        took_over = False
        if page.status_code == 200 and page.context and "secret" in page.context:
            code = pyotp.TOTP(page.context["secret"]).now()
            done = stolen.post(second_factor.STEP_URL, {"code": code})
            took_over = done.status_code == 200 and stolen.get(ME).status_code == 200
        self.assertFalse(took_over, "a session that gave only its cookie (no password) enrolled its own phone and "
                                    "is fully signed in again after the administrator's reset")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/accounts/login/", page["Location"])

    def test_p07_a_password_only_session_reaches_nothing(self):
        ctl = self.person("ctl", "Controller")
        self.enrolled(ctl)
        half, full = self.password_only_client(ctl), self.passed_client(ctl)
        paths = every_get_path()
        self.assertGreater(len(paths), 300, len(paths))
        print(f"P07 routes tried: {len(paths)}")
        reached, sanity = [], 0
        for path in paths:
            seen = half.get(path).status_code
            if seen not in (301, 302, 401, 403, 404, 405):
                reached.append((path, seen))
            if full.get(path).status_code == 200:
                sanity += 1
        print(f"P07 control (passed session) got 200 on {sanity}; password-only reached {len(reached)}")
        self.assertGreater(sanity, 100, "the control (a passed session) reached too little to prove anything")
        self.assertEqual(reached, [], "a password-only Controller session reached these")

    def test_p08_a_login_that_gains_a_guarded_right_after_signing_in_is_stopped_at_once(self):
        user = self.person("sm", "Stores Manager")
        web = self.password_only_client(user)
        self.assertEqual(web.get(ME).status_code, 200)
        user.groups.add(Group.objects.get(name="Controller"))
        self.assertEqual(web.get(ME).status_code, 403)
        self.assertEqual(web.get("/api/accounting/payments/").status_code, 403)
        # and an HTTP-basic call by the same login
        basic = APIClient()
        basic.credentials(HTTP_AUTHORIZATION="Basic " + __import__("base64").b64encode(f"sm:{PW}".encode()).decode())
        self.assertEqual(basic.get(ME).status_code in (401, 403), True)

    def test_p09_every_role_that_pays_or_redirects_money_needs_the_code(self):
        from .management.commands.setup_roles import ROLES

        money = ["hr.pay_expenseclaim", "core.add_partybankaccount", "core.change_partybankaccount",
                 "accounting.post_payment", "hr.view_payslip", "hr.view_employeepaydetails", "auth.change_user"]
        loose = {}
        for role, perms in ROLES.items():
            held = sorted(set(perms) & set(money))
            if held:
                user = self.person(f"u-{role}".replace(" ", "-").lower(), role)
                if not second_factor.required(User.objects.get(pk=user.pk)):
                    loose[role] = held
        self.assertEqual(loose, {}, "roles that move or redirect money and sign in on a password alone")

    def test_p10_a_reset_is_for_whoever_may_know_the_password_never_ones_own_and_is_recorded(self):
        hr = self.person("hr", "HR Admin")
        payroll = self.person("pay", "Payroll Officer")
        ctl = self.person("ctl", "Controller")
        for user in (payroll, ctl, hr):
            self.enrolled(user)
        hr_api = self.api(hr)
        self.assertEqual(hr_api.post(f"{USERS}{payroll.pk}/reset_second_factor/", {}, format="json").status_code, 403)
        self.assertIn(hr_api.post(f"{USERS}{hr.pk}/reset_second_factor/", {}, format="json").status_code, (400, 403))
        root = self.root()
        self.assertEqual(self.api(root).post(f"{USERS}{ctl.pk}/reset_second_factor/", {}, format="json").status_code, 200)
        event = RecordEvent.objects.filter(action="reset_second_factor").first()
        self.assertIsNotNone(event, "the reset is not in the login's history")
        self.assertEqual(self.api(root).post(f"{USERS}{ctl.pk}/reset_second_factor/", {}, format="json").status_code, 400)

    def test_p11_a_leavers_factor_goes_with_the_login(self):
        ctl = self.person("ctl", "Controller")
        self.enrolled(ctl)
        root = self.root()
        self.assertEqual(self.api(root).post(f"{USERS}{ctl.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertFalse(SecondFactor.objects.filter(user=ctl).exists())
        self.api(root).post(f"{USERS}{ctl.pk}/reactivate/", {}, format="json")
        self.assertFalse(second_factor.confirmed(User.objects.get(pk=ctl.pk)))

    def test_p12_basic_authentication_is_refused_for_a_login_with_a_factor(self):
        ctl = self.person("ctl", "Controller")
        self.enrolled(ctl)
        basic = APIClient()
        basic.credentials(HTTP_AUTHORIZATION="Basic " + __import__("base64").b64encode(f"ctl:{PW}".encode()).decode())
        self.assertIn(basic.get("/api/accounting/payments/").status_code, (401, 403))
        # an ordinary login with no factor is unchanged
        sm = self.person("sm", "Stores Manager")
        basic.credentials(HTTP_AUTHORIZATION="Basic " + __import__("base64").b64encode(f"sm:{PW}".encode()).decode())
        self.assertEqual(basic.get(ME).status_code, 200)

    def test_p13_a_password_only_session_of_a_login_with_a_confirmed_but_unrequired_factor_cannot_remove_it(self):
        # An optional enrolment: the owner is locked out if someone with a password-only session enrols first.
        sm = self.person("sm", "Stores Manager")
        stranger = self.password_only_client(sm)  # e.g. a session cookie lifted at a shared PC
        page = stranger.get(second_factor.STEP_URL)
        self.assertEqual(page.status_code, 200)
        code = pyotp.TOTP(page.context["secret"]).now()
        stranger.post(second_factor.STEP_URL, {"code": code})
        # Review F2 fixed: the code alone, with no password, enrols nothing (this line asserted the takeover).
        self.assertFalse(second_factor.confirmed(User.objects.get(pk=sm.pk)))
        owner, step = self.password("sm")
        # The owner now meets a code step for a phone they never enrolled. This documents the takeover: the
        # claim "enrolling an optional factor asks for the password again" fails when the owner is locked out.
        self.assertFalse(step.startswith("/accounts/second-factor/"),
                         "a password-only session enrolled a factor on the owner's login and the owner is now asked "
                         "for a code from a phone that is not theirs")


# ---------------------------------------------------------------------------
# 2. Session time-outs
# ---------------------------------------------------------------------------
class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def time(self):
        return self.now


class SessionProbes(Probe):
    def stub(self, clock):
        return mock.patch.object(session_limits, "time", SimpleNamespace(time=clock.time))

    def signed_in(self, clock):
        sm = self.person("sm", "Stores Manager")
        web = Client()
        with self.stub(clock):
            web.force_login(sm)
        return web

    @override_settings(SESSION_IDLE_SECONDS=120, SESSION_ABSOLUTE_SECONDS=3600)
    def test_p14_idle_limit_on_the_api(self):
        clock = Clock()
        web = self.signed_in(clock)
        with self.stub(clock):
            self.assertEqual(web.get(ME).status_code, 200)
            clock.now += 119
            self.assertEqual(web.get(ME).status_code, 200)
            clock.now += 125
            gone = web.get(ME)
            self.assertEqual(gone.status_code, 403)
            self.assertEqual(gone["X-Session-Ended"], "idle")
            clock.now += 1
            self.assertEqual(web.get(ME).status_code, 403)

    @override_settings(SESSION_IDLE_SECONDS=120, SESSION_ABSOLUTE_SECONDS=1000)
    def test_p15_absolute_limit_holds_for_a_session_in_constant_use(self):
        clock = Clock()
        web = self.signed_in(clock)
        why = None
        with self.stub(clock):
            for _ in range(80):
                clock.now += 20
                answer = web.get(ME)
                if answer.status_code != 200:
                    why = answer.headers.get("X-Session-Ended")
                    break
        self.assertEqual(why, "absolute")
        self.assertLessEqual(clock.now - 1_800_000_000.0, 1000 + 20)

    @override_settings(SESSION_IDLE_SECONDS=120, SESSION_ABSOLUTE_SECONDS=3600)
    def test_p16_a_page_polling_in_the_background_does_not_keep_an_idle_session_open(self):
        # The station screen asks the server what the scale reads every second (station.html: scale.follow),
        # as whoever is signed in at that PC, with no X-Session-Check header.
        clock = Clock()
        web = self.signed_in(clock)
        ended = None
        with self.stub(clock):
            for second in range(1, 400):
                clock.now += 1
                answer = web.get("/api/manufacturing/stations/SC-1/scale/")
                if answer.status_code in (401, 403) and answer.headers.get("X-Session-Ended"):
                    ended = second
                    break
        self.assertIsNotNone(ended, "400 seconds of a station page polling (nobody at the PC) kept the session open "
                                    "past a 120 s idle limit")

    @override_settings(SESSION_IDLE_SECONDS=120, SESSION_ABSOLUTE_SECONDS=3600)
    def test_p17_the_header_the_application_means_for_its_own_clock_is_sent_by_someone(self):
        # X-Session-Check keeps the session from being extended; if no front-end call sends it, it is inert.
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
        senders = [str(p) for p in src.rglob("*.ts*") if re.search(r"check:\s*true", p.read_text())]
        self.assertTrue(senders, "no screen passes check: true, so X-Session-Check is never sent")


# ---------------------------------------------------------------------------
# 3. API keys
# ---------------------------------------------------------------------------
class KeyProbes(Probe):
    def setUp(self):
        super().setUp()
        self.root_user = self.root()
        self.rootc = self.api(self.root_user)

    def integration(self, name="Scale SC-1", perm=("manufacturing", "add_scalereading")):
        made = self.rootc.post("/api/core/integrations/", {"name": name}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        login = User.objects.get(pk=made.json()["user"])
        if perm:
            login.user_permissions.add(Permission.objects.get(content_type__app_label=perm[0], codename=perm[1]))
        return made.json()["id"], login

    def key_for(self, client, integration_id, **body):
        answer = client.post(f"/api/core/integrations/{integration_id}/issue_key/", body, format="json")
        self.assertEqual(answer.status_code, 201, answer.content)
        return answer.json()["key"], answer.json()

    def with_key(self, key):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Api-Key {key}")
        return client

    def test_p20_a_key_is_shown_once_and_kept_only_as_a_digest(self):
        iid, login = self.integration()
        key, shown = self.key_for(self.rootc, iid, label="loom shed")
        row = ApiKey.objects.get()
        self.assertNotIn(key, json.dumps(row.__dict__, default=str))
        self.assertEqual(row.digest, hashlib.sha256(key.encode()).hexdigest())
        for path in ("/api/core/api-keys/", f"/api/core/api-keys/{row.pk}/", "/api/core/integrations/",
                     f"/api/core/api-keys/{row.pk}/uses/"):
            self.assertNotIn(key, self.rootc.get(path).content.decode(), path)
            self.assertNotIn(row.digest, self.rootc.get(path).content.decode(), path)
        everything = " ".join(json.dumps(e.__dict__, default=str) for e in RecordEvent.objects.all())
        self.assertNotIn(key, everything)
        self.assertEqual(len(key.split("_", 2)[2]) >= 40, True)

    def test_p21_revoked_and_expired_keys_are_refused_and_each_use_is_recorded(self):
        iid, login = self.integration()
        key, shown = self.key_for(self.rootc, iid)
        client = self.with_key(key)
        self.assertEqual(client.get(ME).status_code, 200)
        denied = client.get("/api/accounting/payments/")  # not this login's business
        self.assertIn(denied.status_code, (401, 403))
        paths = list(ApiKeyUse.objects.values_list("path", flat=True))
        self.assertEqual(paths.count("/api/accounting/payments/"), 1, "a refused request left no record of the use")
        self.assertEqual(paths.count(ME), 1)
        self.assertEqual(self.rootc.post(f"/api/core/api-keys/{shown['id']}/revoke/", {}, format="json").status_code, 200)
        self.assertIn(client.get(ME).status_code, (401, 403))
        iid2, login2 = self.integration("Second")
        key2, shown2 = self.key_for(self.rootc, iid2)
        ApiKey.objects.filter(pk=shown2["id"]).update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertIn(self.with_key(key2).get(ME).status_code, (401, 403))
        self.assertIn(self.with_key(key + "x").get(ME).status_code, (401, 403))

    def test_p22_a_key_reaches_only_its_logins_rights(self):
        iid, login = self.integration()
        key, _ = self.key_for(self.rootc, iid)
        client = self.with_key(key)
        for path in ("/api/accounting/payments/", "/api/hr/payslips/", "/api/core/users/", "/api/core/api-keys/",
                     "/api/core/integrations/", "/api/sales/orders/", "/api/hr/employee-pay-details/"):
            self.assertIn(client.get(path).status_code, (403, 404), path)
        # write scope: only the one right
        self.assertEqual(client.post("/api/core/integrations/", {"name": "evil"}, format="json").status_code, 403)

    def test_p23_a_key_is_refused_once_its_login_holds_a_right_that_needs_the_code(self):
        iid, login = self.integration()
        key, _ = self.key_for(self.rootc, iid)
        client = self.with_key(key)
        self.assertEqual(client.get(ME).status_code, 200)
        for perm in (("accounting", "post_payment"), ("hr", "view_payslip"), ("auth", "change_user")):
            login.user_permissions.add(Permission.objects.get(content_type__app_label=perm[0], codename=perm[1]))
            fresh = self.with_key(key)
            self.assertIn(fresh.get(ME).status_code, (401, 403), perm)
            login.user_permissions.clear()
        # a refusal at issue too
        login.user_permissions.add(Permission.objects.get(content_type__app_label="accounting", codename="post_payment"))
        refused = self.rootc.post(f"/api/core/integrations/{iid}/issue_key/", {}, format="json")
        self.assertEqual(refused.status_code, 400)

    def test_p24_a_key_does_not_outlive_the_person_who_saw_it(self):
        # Passwords a keeper sets lapse when the keeper leaves or is demoted (roles.lapse_with_the_setter).
        # An issuer saw the key, so the key is the same kind of fact.
        hr = self.person("hr", "HR Admin")
        iid, login = self.integration(perm=None)
        # HR Admin holds nothing the integration holds, so it can issue a key for a login that has no rights
        key, _ = self.key_for(self.api(hr), iid)
        self.assertEqual(self.with_key(key).get(ME).status_code, 200)
        self.assertEqual(self.rootc.post(f"{USERS}{hr.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertIn(self.with_key(key).get(ME).status_code, (401, 403),
                      "a key issued by a leaver still signs in")

    def test_p25_a_key_does_not_outlive_the_demotion_of_the_issuer(self):
        hr = self.person("hr", "HR Admin")
        iid, login = self.integration(perm=None)
        key, _ = self.key_for(self.api(hr), iid)
        self.assertEqual(self.rootc.post(f"{USERS}{hr.pk}/revoke/", {"role": "HR Admin"}, format="json").status_code, 200)
        self.assertIn(self.with_key(key).get(ME).status_code, (401, 403),
                      "a key issued by a demoted keeper still signs in")

    def test_p27_a_key_does_not_outlive_the_rights_its_issuer_could_give(self):
        # O157's shape for a key: a keeper who learned a credential must not keep one to a login that has since been
        # given what the keeper could not give (roles.lapse_on_giving does this for passwords and emails).
        hr = self.person("hr", "HR Admin")
        iid, login = self.integration(perm=None)
        key, _ = self.key_for(self.api(hr), iid)
        self.assertEqual(self.with_key(key).get(ME).status_code, 200, "control: the key works")
        login.user_permissions.add(Permission.objects.get(content_type__app_label="manufacturing",
                                                          codename="add_scalereading"))
        self.assertIn(self.with_key(key).get(ME).status_code, (401, 403),
                      "HR, which saw this key, now holds a credential to a login with a right HR cannot give")

    def test_p28_hr_cannot_issue_the_scale_integrations_key_so_only_a_superuser_holds_it(self):
        hr = self.person("hr", "HR Admin")
        iid, login = self.integration()  # holds manufacturing.add_scalereading
        refused = self.api(hr).post(f"/api/core/integrations/{iid}/issue_key/", {}, format="json")
        self.assertEqual(refused.status_code, 403, refused.content)

    def test_p39_an_integrations_login_cannot_be_given_a_password_beside_its_key(self):
        iid, login = self.integration()
        gave = self.rootc.post(f"{USERS}{login.pk}/set_password/", {"password": "Integration-Pw-2026-xq"}, format="json")
        basic = APIClient()
        basic.credentials(HTTP_AUTHORIZATION="Basic " + __import__("base64").b64encode(
            b"integration-scale-sc-1:Integration-Pw-2026-xq").decode())
        reached = basic.get(ME).status_code == 200
        self.assertFalse(gave.status_code == 200 and reached,
                         "a password on an integration's login signs in by HTTP basic: no expiry, no revoke, no use "
                         "recorded")

    def test_p26_the_scale_bridge_refuses_a_key_file_other_people_can_read(self):
        import importlib.util
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "scale_bridge" / "scale_bridge.py"
        spec = importlib.util.spec_from_file_location("scale_bridge_probe", path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder:
            keyfile = os.path.join(folder, "SC-1.key")
            with open(keyfile, "w") as out:
                out.write("erp_deadbeef_notarealkey")
            os.chmod(keyfile, 0o644)
            config = {"server": "https://erp.example", "api_key_file": keyfile, "post_timeout_seconds": 1}
            refused = False
            try:
                bridge.poster(config)
            except Exception:  # noqa: BLE001 - any refusal is the right answer
                refused = True
            self.assertTrue(refused, f"the bridge used a key file with mode {oct(stat.S_IMODE(os.stat(keyfile).st_mode))}")


# ---------------------------------------------------------------------------
# 4. O216 and O217
# ---------------------------------------------------------------------------
class MakerProbes(KeeperAndHolder):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root", password="x-root-pw-2026")
        self.rootc = self.api(self.root)

    def the_worker_changes_their_own_password(self, username, old=KEEPER_PW, new=OWN):
        web = Client()
        self.assertTrue(web.login(username=username, password=old))
        changed = web.post("/accounts/password_change/", {"old_password": old, "new_password1": new,
                                                          "new_password2": new})
        self.assertEqual(changed.status_code, 302)
        self.assertTrue(self.signs_in(username, new), "control: the change took")

    def test_p30_a_worker_hr_made_keeps_their_own_password_when_hr_leaves(self):
        self.make("worker", [])
        self.the_worker_changes_their_own_password("worker")  # HR never saw this one
        self.assertTrue(self.signs_in("worker", OWN), "control: the worker's own password works before HR leaves")
        self.assertEqual(self.rootc.post(f"{USERS}{self.hr.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertTrue(self.signs_in("worker", OWN), "the worker's own password lapsed because HR left")

    def test_p31_and_when_hr_is_demoted(self):
        self.make("worker", [])
        self.the_worker_changes_their_own_password("worker")
        self.assertTrue(self.signs_in("worker", OWN), "control")
        self.assertEqual(self.rootc.post(f"{USERS}{self.hr.pk}/revoke/", {"role": "HR Admin"}, format="json").status_code, 200)
        self.assertTrue(self.signs_in("worker", OWN), "the worker's own password lapsed because HR was demoted")

    def test_p32_and_when_the_worker_is_promoted_above_hr(self):
        # The commonest path: nobody leaves; the worker is given a role HR could not give.
        self.make("worker", [])
        self.the_worker_changes_their_own_password("worker")
        self.assertTrue(self.signs_in("worker", OWN), "control")
        given = self.rootc.post(f"{USERS}{User.objects.get(username='worker').pk}/grant/", {"role": "Controller"},
                                format="json")
        self.assertEqual(given.status_code, 200, given.content)
        self.assertTrue(self.signs_in("worker", OWN), "the worker's own password lapsed when they were promoted")

    # p33 and p34 encoded the rule the integrator replaced (review F1): a password someone else set is replaced
    # at the first sign-in. Their cases, with the decided expectations, are tests_users_api
    # APasswordSomeoneElseSetIsReplacedFirstTests (test_p33_..., test_p34_...).

    def test_p35_a_link_mailed_while_the_address_was_trusted_works_for_a_role_hr_can_give(self):
        # The refusal must be only for an address since out-ranked, not for any change in the login.
        import re as _re

        from django.core import mail

        asha = User.objects.create_user("asha", password="Asha-Old-Pw-2026")
        self.hrc.patch(f"{USERS}{asha.pk}/", {"email": "asha@plant.example"}, format="json")
        mail.outbox.clear()
        Client().post("/accounts/password_reset/", {"email": "asha@plant.example"})
        link = _re.search(r"/accounts/reset/[^/]+/[^/\s]+/", mail.outbox[0].body).group(0)
        # a role HR itself can give changes nothing about trust
        self.assertEqual(self.hrc.post(f"{USERS}{asha.pk}/grant/", {"role": "Bookkeeper"}, format="json").status_code in (200, 201), True)
        web = Client()
        landed = web.get(link)
        done = web.post(landed["Location"], {"new_password1": "Through-The-Mailbox-26", "new_password2": "Through-The-Mailbox-26"})
        self.assertEqual(done.status_code, 302, done.content.decode()[:400])
        # and the person's own address resets nothing refused: the person changes it themselves, then resets
        self.assertTrue(self.signs_in("asha", "Through-The-Mailbox-26"))


# ---------------------------------------------------------------------------
# More on two-factor: the way back, and what the data being missing does
# ---------------------------------------------------------------------------
class WayBackProbes(Probe):
    def test_p36_with_no_guarded_rights_on_file_nobody_but_a_superuser_needs_the_code(self):
        # GuardedRight is seeded only by setup_roles (the entrypoint runs it at every start). A database where it
        # has not run yet signs the Controller in on a password alone, with nothing to say so.
        from django.contrib.auth.models import Permission

        second_factor.GuardedRight.objects.all().delete()
        user = User.objects.create_user("direct", password=PW)
        user.user_permissions.add(Permission.objects.get(content_type__app_label="accounting", codename="post_payment"))
        self.assertTrue(second_factor.required(User.objects.get(pk=user.pk)),
                        "a login that may post a payment needs no code when the guarded-rights table is empty")

    def test_p37_a_sole_administrator_who_lost_the_phone_has_a_way_back_that_is_not_a_database_shell(self):
        from django.core.management import get_commands

        self.assertTrue(any("second_factor" in name or "second-factor" in name for name in get_commands()),
                        "no management command resets a factor: with one administrator and a lost phone, "
                        "the only way back is SQL, and the RUNBOOK does not say so")

    def test_p38_the_step_goes_only_where_the_application_goes(self):
        ctl = self.person("ctl", "Controller")
        factor = self.enrolled(ctl)
        for target in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)"):
            web, _ = self.password("ctl")
            done = web.post(second_factor.STEP_URL, {"code": self.code(factor), "next": target})
            SecondFactor.objects.filter(pk=factor.pk).update(last_step=None, failures=0, locked_until=None)
            self.assertEqual(done.status_code, 302)
            self.assertEqual(done["Location"], "/app/", target)
