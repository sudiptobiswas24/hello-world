"""
review_sec3: adversarial probes for gate g20 (O124 O129 O131 O191 O195 O196 O198).

Each test asserts what should be true and is driven through the API as a
person in a role (setup_roles), never a superuser; the one exception is
`root`, used only to do what a superuser alone does (demote a keeper,
grant in the admin). A test that fails here is a finding.
"""

import datetime
import json
import re
import traceback
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import get_resolver
from rest_framework.parsers import JSONParser
from rest_framework.test import APIClient

from apps.core import tests_users_api as base
from apps.core.api import ObjectJSONParser
from apps.core.roles import KeptCredential, RoleProposal, who_knows

USERS = base.USERS
PW = base.PW
OUT = "/tmp/claude-0/-home-user-hello-world/2ec41226-46f2-5ea2-a139-e69651007cc2/scratchpad/review_sec3_out"


def routes():
    """[(path with 1 for every parameter, view class)] for every /api/ route."""
    found = []

    def walk(patterns, prefix):
        for entry in patterns:
            here = prefix + str(entry.pattern)
            if hasattr(entry, "url_patterns"):
                walk(entry.url_patterns, here)
                continue
            cls = getattr(entry.callback, "cls", None) or getattr(entry.callback, "view_class", None)
            if cls is None or "format" in here:
                continue
            path = "/" + re.sub(r"\(\?P<\w+>[^)]*\)", "1", here).replace("^", "").replace("$", "").replace("\\.", ".")
            path = path.replace("//", "/").replace("?", "")
            if path.startswith("/api/"):
                found.append((path, cls))

    walk(get_resolver().url_patterns, "")
    return found


def endpoint_for(cls):
    """The list route of a viewset class, e.g. /api/inventory/warehouses/."""
    for path, view in routes():
        if view is cls and not path.rstrip("/").endswith("/1") and "/1/" not in path:
            return path
    raise LookupError(cls)


class O195(base.KeeperAndHolder):
    """The two-person rule: nobody acts through a login they made or keep."""

    def attack(self, launder):
        """HR makes puppet (HR Admin), launders it, puppet proposes Bookkeeper for HR, a Bookkeeper confirms."""
        puppet = self.make("puppet", ["HR Admin"])
        launder(puppet)
        out = {"propose": self.api(puppet).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"},
                                               format="json").status_code}
        if out["propose"] == 200:
            out["confirm"] = self.confirm(self.bk, self.pending(self.hr, "Bookkeeper")).status_code
        out["hr_holds_bookkeeper"] = self.holds(self.hr, "Bookkeeper")
        return out

    def test_laundered_through_the_logins_own_set_password_action(self):
        """HR signs in as puppet and sets puppet's 'own' password: the kept record is wiped (record_credential, by == user)."""
        def launder(puppet):
            self.assertTrue(self.signs_in("puppet", PW), "HR signs in as puppet with the password it set")
            done = self.api(puppet).post(f"{USERS}{puppet.pk}/set_password/", {"password": "HR-Chosen-Pw-2026-zq"},
                                         format="json")
            self.assertEqual(done.status_code, 200, done.content)
        out = self.attack(launder)
        self.assertFalse(out["hr_holds_bookkeeper"], f"HR holds HR Admin and Bookkeeper on one login: {out}")

    def test_laundered_through_djangos_password_change(self):
        def launder(puppet):
            web = Client()
            self.assertTrue(web.login(username="puppet", password=PW))
            done = web.post("/accounts/password_change/", {"old_password": PW, "new_password1": "HR-Chosen-Pw-2026-zq",
                                                           "new_password2": "HR-Chosen-Pw-2026-zq"})
            self.assertEqual(done.status_code, 302, getattr(done, "content", b"")[:300])
        out = self.attack(launder)
        self.assertFalse(out["hr_holds_bookkeeper"], f"HR holds HR Admin and Bookkeeper on one login: {out}")

    def test_laundered_through_a_chain_a_second_login_made_by_the_first(self):
        """HR makes p1; as p1 makes p2; p1 sets its own password (HR keeps it); p2 proposes for HR."""
        p1 = self.make("p1", ["HR Admin"])
        made = self.api(p1).post(USERS, {"username": "p2", "password": PW, "roles": ["HR Admin"]}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        p2 = User.objects.get(username="p2")
        self.assertIn(self.hr.pk, who_knows(p2), "HR is known to sign in as p2 while p1 is its login")
        self.api(p1).post(f"{USERS}{p1.pk}/set_password/", {"password": "HR-Chosen-Pw-2026-zq"}, format="json")
        got = self.api(p2).post(f"{USERS}{self.hr.pk}/grant/", {"role": "Bookkeeper"}, format="json").status_code
        self.assertEqual(got, 400, "p2's password is still HR's; only the record of it went")

    def test_a_demoted_keeper_still_signs_in_as_the_login_it_made(self):
        """HR loses HR Admin (a superuser takes it): the login it made still holds it, and HR still knows its password."""
        User.objects.create_superuser("root", password="x-root-pw-2026")
        root = self.api(User.objects.get(username="root"))
        self.make("puppet", ["HR Admin"])
        self.assertEqual(root.post(f"{USERS}{self.hr.pk}/revoke/", {"role": "HR Admin"}, format="json").status_code, 200)
        self.assertFalse(self.holds(self.hr, "HR Admin"))
        self.assertFalse(self.signs_in("puppet", PW),
                         "a login that holds HR Admin is still open to someone who no longer holds it")

    def test_a_deactivated_keeper_still_signs_in_as_the_login_it_made(self):
        User.objects.create_superuser("root", password="x-root-pw-2026")
        root = self.api(User.objects.get(username="root"))
        self.make("puppet", ["HR Admin"])
        self.assertEqual(root.post(f"{USERS}{self.hr.pk}/deactivate/", {}, format="json").status_code, 200)
        self.assertFalse(self.signs_in("puppet", PW), "a leaver who made a login still knows its password")

    def test_a_hash_upgrade_forgets_the_keeper(self):
        """Django re-hashes a password on sign-in when the hasher or its cost moved on; the fingerprint no longer matches."""
        puppet = self.make("puppet", ["HR Admin"])
        self.assertIn(self.hr.pk, who_knows(puppet))
        hashers = ["django.contrib.auth.hashers.PBKDF2PasswordHasher", "django.contrib.auth.hashers.MD5PasswordHasher"]
        with override_settings(PASSWORD_HASHERS=hashers):
            self.assertTrue(Client().login(username="puppet", password=PW))
            self.assertTrue(User.objects.get(pk=puppet.pk).password.startswith("pbkdf2"), "the hash was upgraded")
        self.assertIn(self.hr.pk, User.objects.get(pk=puppet.pk).kept_credentials.values_list("set_by", flat=True))
        self.assertIn(self.hr.pk, who_knows(User.objects.get(pk=puppet.pk)),
                      "HR still knows puppet's password; a re-hash on sign-in does not change that")

    def test_the_confirmer_is_shown_whose_login_proposed_it(self):
        """The confirmer reads 'proposed by puppet' and the subject's credentials; is the proposer's own maker said?"""
        puppet = self.make("puppet", ["HR Admin"])
        third = self.make("third", [])
        self.api(puppet).post(f"{USERS}{third.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        row = self.api(self.bk).get(f"{self.PROPOSALS}{self.pending(third, 'Bookkeeper').pk}/").json()
        said = f"{row.get('proposed_by_name')} {row.get('credentials')}"
        self.assertRegex(said, r"(?i)puppet's (password|email)",
                         f"the confirmer is not told that puppet's password is HR's: {row}")

    def test_a_proposal_outlives_neither_a_demoted_nor_a_deactivated_proposer(self):
        User.objects.create_superuser("root", password="x-root-pw-2026")
        root = self.api(User.objects.get(username="root"))
        a, b = self.make("a", []), self.make("b", [])
        self.hrc.post(f"{USERS}{a.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        self.hrc.post(f"{USERS}{b.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        pa, pb = RoleProposal.objects.get(user=a), RoleProposal.objects.get(user=b)
        self.assertEqual(root.post(f"{USERS}{self.hr.pk}/revoke/", {"role": "HR Admin"}, format="json").status_code, 200)
        first = self.confirm(self.bk, pa).status_code
        # Directly: the keeper loses auth.change_user without a revoke (a role widened down, a permission taken).
        self.hr.groups.add(Group.objects.get(name="HR Admin"))
        group = Group.objects.get(name="HR Admin")
        group.permissions.remove(Permission.objects.get(codename="change_user", content_type__app_label="auth"))
        second = self.confirm(self.bk, pb).status_code
        self.assertEqual((first, second, self.holds(a, "Bookkeeper"), self.holds(b, "Bookkeeper")), (400, 400, False, False),
                         "a proposal confirmed after its proposer lost the right to keep logins")

    def test_waiting_list_hides_what_the_confirmer_may_not_confirm(self):
        puppet = self.make("puppet", ["HR Admin"])
        third = self.make("third", [])
        self.api(puppet).post(f"{USERS}{third.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        rows = self.api(self.bk).get(self.PROPOSALS, {"waiting": "true"}).json()
        self.assertEqual([r["may_confirm"] for r in rows], [True] * len(rows))

    def test_rotating_the_secret_key_forgets_every_keeper(self):
        puppet = self.make("puppet", ["HR Admin"])
        self.assertIn(self.hr.pk, who_knows(puppet))
        with override_settings(SECRET_KEY="a-rotated-key-" + "x" * 40):
            self.assertIn(self.hr.pk, who_knows(User.objects.get(pk=puppet.pk)),
                          "the digest is keyed by SECRET_KEY: a rotation drops every kept password from who_knows and from the lapse")


class O191Actions(base.KeeperAndHolder):
    """Non-object bodies to actions that read a body, with data behind the route (the sweep met 404s)."""

    BODIES = ["[]", '[{"role": "Bookkeeper"}]', '"x"', "7", "null", "true", "{", ""]

    def test_actions_on_a_real_login_and_a_real_proposal_answer_400_in_words(self):
        target = self.make("target", [])
        self.hrc.post(f"{USERS}{target.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        proposal = self.pending(target, "Bookkeeper")
        hr = APIClient(raise_request_exception=False)
        hr.force_authenticate(User.objects.get(pk=self.hr.pk))
        bk = APIClient(raise_request_exception=False)
        bk.force_authenticate(User.objects.get(pk=self.bk.pk))
        bad = []
        for who, method, url in [(hr, "post", f"{USERS}{target.pk}/grant/"), (hr, "post", f"{USERS}{target.pk}/revoke/"),
                                 (hr, "post", f"{USERS}{target.pk}/set_password/"), (hr, "patch", f"{USERS}{target.pk}/"),
                                 (hr, "put", f"{USERS}{target.pk}/"), (hr, "post", USERS),
                                 (bk, "post", f"{self.PROPOSALS}{proposal.pk}/confirm/"),
                                 (bk, "post", f"{self.PROPOSALS}{proposal.pk}/decline/")]:
            for body in self.BODIES:
                got = getattr(who, method)(url, body, content_type="application/json")
                said = got.content.decode("utf8", "replace")
                if got.status_code >= 500 or (body in ("[]", '"x"', "7", "null", "true", "{") and got.status_code != 400):
                    bad.append((method, url, body, got.status_code, said[:100]))
        self.assertEqual(bad, [])


class O196(base.KeeperAndHolder):
    """A person's login is given only what its link would take: both orders, re-link."""

    def setUp(self):
        from apps.hr.models import Employee
        from apps.hr.tests import make_employee_party

        super().setUp()
        self.ctl = self.person("ctl", "Controller")
        User.objects.create_superuser("root", password="x-root-pw-2026")
        self.root = self.api(User.objects.get(username="root"))
        self.b1 = self.make("b1", [])
        self.e1 = Employee.objects.create(party=make_employee_party("B1", "B1"), employee_number="B1",
                                          hire_date=datetime.date(2025, 1, 1))
        self.e2 = Employee.objects.create(party=make_employee_party("B2", "B2"), employee_number="B2",
                                          hire_date=datetime.date(2025, 1, 1))

    def link(self, login, employee, client=None):
        user = login.pk if login is not None else None
        return (client or self.hrc).patch(f"/api/hr/employees/{employee.pk}/", {"user": user}, format="json")

    def test_proposed_then_linked_is_refused(self):
        self.assertEqual(self.hrc.post(f"{USERS}{self.b1.pk}/grant/", {"role": "Controller"}, format="json").status_code, 200)
        got = self.link(self.b1, self.e1).status_code
        self.assertIn(got, (400, 403), "a login with Controller pending is linked to a person")

    def test_confirmed_then_linked_is_refused(self):
        self.hrc.post(f"{USERS}{self.b1.pk}/grant/", {"role": "Controller"}, format="json")
        self.assertEqual(self.confirm(self.ctl, self.pending(self.b1, "Controller")).status_code, 200)
        got = self.link(self.b1, self.e1).status_code
        self.assertIn(got, (400, 403), "a Controller login is linked to a person")

    def test_unlinked_then_proposed_then_relinked_is_refused(self):
        self.assertEqual(self.link(self.b1, self.e1).status_code, 200)
        self.assertEqual(self.link(None, self.e1).status_code, 200)
        self.assertEqual(self.hrc.post(f"{USERS}{self.b1.pk}/grant/", {"role": "Controller"}, format="json").status_code, 200)
        self.assertEqual(self.confirm(self.ctl, self.pending(self.b1, "Controller")).status_code, 200)
        self.assertIn(self.link(self.b1, self.e1).status_code, (400, 403), "re-linked a Controller login")

    def test_a_superusers_grant_to_a_linked_login_then_hr_relinks_it(self):
        self.assertEqual(self.link(self.b1, self.e1).status_code, 200)
        self.assertEqual(self.root.post(f"{USERS}{self.b1.pk}/grant/", {"role": "Controller"}, format="json").status_code, 200)
        self.assertEqual(self.link(None, self.e1).status_code, 200)
        self.assertIn(self.link(self.b1, self.e2).status_code, (400, 403), "HR moves a Controller login onto another person")

    def test_hr_links_its_own_puppet_to_a_manager_and_acts_as_the_manager(self):
        """HR makes puppet (Employee Self Service), links it to a manager with reports: HR now reads their leave as the manager."""
        puppet = self.make("puppet", ["Employee Self Service"])
        self.e1.user = None
        linked = self.link(puppet, self.e1)
        self.assertEqual(linked.status_code, 200, linked.content)
        self.assertIn(self.hr.pk, who_knows(puppet))

    def test_a_proposal_for_a_linked_login_is_not_listed_as_waiting_for_a_holder_who_may_not_confirm(self):
        self.link(self.b1, self.e1)
        RoleProposal.objects.create(user=self.b1, group=Group.objects.get(name="Controller"), proposed_by=self.hr)
        rows = self.api(self.ctl).get(self.PROPOSALS, {"waiting": "true"}).json()
        offered = [(row["username"], row["may_confirm"]) for row in rows]
        self.assertEqual([row for row in offered if row[1] is False], [], f"listed as waiting but not confirmable: {offered}")


class O198(base.KeeperAndHolder):
    def test_no_trace_of_the_keeper_set_hash_in_the_history(self):
        from apps.core.history import RecordEvent

        puppet = self.make("puppet", ["HR Admin"])
        stored = User.objects.get(pk=puppet.pk).password
        text = json.dumps(list(RecordEvent.objects.values()), default=str)
        self.assertNotIn(stored, text)
        history = self.hrc.get("/api/core/history/", {"model": "auth.user", "id": puppet.pk})
        self.assertNotIn(stored, history.content.decode())

    def test_the_keepers_email_is_not_trusted_for_a_reset_once_the_admin_gives_staff(self):
        User.objects.create_superuser("root", password="x-root-pw-2026")
        made = self.hrc.post(USERS, {"username": "puppet", "password": PW, "roles": ["HR Admin"],
                                     "email": "hr-mailbox@example.com"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        puppet = User.objects.get(username="puppet")
        puppet.is_staff = True
        puppet.save()
        mail.outbox.clear()
        Client().post("/accounts/password_reset/", {"email": "hr-mailbox@example.com"})
        self.assertEqual(len(mail.outbox), 0, "a reset link goes to the address HR set after the login gained the admin site")
        self.assertFalse(self.signs_in("puppet", PW))

    def test_a_right_given_by_a_queryset_update_is_not_a_door_but_the_admin_bulk_action_is(self):
        """The admin's 'make staff' style actions use queryset.update: no signal. Recorded, not a defect of the diff."""
        puppet = self.make("puppet", ["HR Admin"])
        User.objects.filter(pk=puppet.pk).update(is_superuser=True)
        self.assertFalse(self.signs_in("puppet", PW), "a superuser login whose password HR chose")

    def test_a_role_gained_through_group_user_set_in_the_admin_lapses_it(self):
        puppet = self.make("puppet", ["HR Admin"])
        Group.objects.get(name="Bookkeeper").user_set.add(puppet)
        self.assertFalse(self.signs_in("puppet", PW))

    def test_a_pending_proposal_does_not_hide_the_credentials_from_the_confirmer(self):
        """Credentials said on the proposal for a login whose password HR set: names HR."""
        sub = self.make("sub", [])
        self.hrc.post(f"{USERS}{sub.pk}/grant/", {"role": "Bookkeeper"}, format="json")
        row = self.api(self.bk).get(f"{self.PROPOSALS}{self.pending(sub, 'Bookkeeper').pk}/").json()
        self.assertIn("hr", row["credentials"].lower())


class O191Parsers(TestCase):
    def test_every_routed_view_parses_json_with_the_object_parser(self):
        bad = []
        for path, cls in routes():
            if cls.get_parsers is not __import__("rest_framework.views", fromlist=["APIView"]).APIView.get_parsers:
                bad.append((path, "overrides get_parsers"))
            for parser in cls.parser_classes:
                if "json" in parser.media_type and not issubclass(parser, ObjectJSONParser):
                    bad.append((path, parser.__name__))
        self.assertEqual(sorted(set(bad)), [])

    def test_no_view_reads_the_raw_body(self):
        import pathlib
        hits = []
        for file in pathlib.Path("apps").rglob("*.py"):
            if "tests" in file.name or "migrations" in str(file) or file.name == "audit_invariants.py":
                continue
            for number, line in enumerate(file.read_text().splitlines(), 1):
                if re.search(r"request\.body|request\._request\.body|json\.loads\(request|request\.stream", line):
                    hits.append(f"{file}:{number}")
        self.assertEqual(hits, [])

    def test_deeply_nested_json_is_a_400_not_a_500(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("bk")
        user.groups.add(Group.objects.get(name="Bookkeeper"))
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user)
        for body in ("[" * 200000 + "]" * 200000, '{"a":' * 200000 + "1" + "}" * 200000):
            got = client.post("/api/core/role-proposals/1/confirm/", body, content_type="application/json")
            self.assertLess(got.status_code, 500, f"{body[:6]}...: {got.status_code}")


class O191Sweep(TestCase):
    """Every POST/PATCH/PUT route with a body that is not an object, as a login holding every permission but no superuser."""

    NOT_OBJECT = {"list": "[]", "list_of_objects": '[{"a": 1}]', "string": '"x"', "number": "7", "null": "null",
                  "true": "true"}
    OTHER = {"broken": "{", "empty": "", "object": "{}"}

    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        try:
            call_command("load_demo", verbosity=0, password="Demo-Pw-2026-xq")
        except BaseException:  # noqa: BLE001
            cls.demo_error = traceback.format_exc()[-600:]
        group = Group.objects.create(name="Everything")
        group.permissions.set(Permission.objects.all())
        user = User.objects.create_user("everything")
        user.groups.add(group)
        cls.user = user

    def sweep(self, bodies, methods=("POST", "PATCH", "PUT")):
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(User.objects.get(pk=self.user.pk))
        seen = {}
        for path, cls in routes():
            allowed = {m.upper() for m in getattr(cls, "http_method_names", [])}
            for method in methods:
                if method.lower() not in cls.http_method_names:
                    continue
                for name, body in bodies.items():
                    try:
                        got = client.generic(method, path, body, content_type="application/json")
                        code, text = got.status_code, got.content[:160].decode("utf8", "replace")
                    except BaseException as exc:  # noqa: BLE001
                        code, text = 599, f"{type(exc).__name__}: {exc}"[:160]
                    seen[f"{method} {path} {name}"] = (code, text)
        return seen

    def test_sweep_non_object_bodies(self):
        import os
        os.makedirs(OUT, exist_ok=True)
        after = self.sweep(self.NOT_OBJECT)
        crashed = {k: v for k, v in after.items() if v[0] >= 500}
        accepted = {k: v for k, v in after.items() if v[0] < 300}
        unworded = {k: v for k, v in after.items() if v[0] == 400 and "object" not in v[1] and "detail" not in v[1]
                    and "Send the fields" not in v[1]}
        with mock.patch.object(ObjectJSONParser, "parse", JSONParser.parse):
            before = self.sweep(self.NOT_OBJECT)
        worked_before = {k: v for k, v in before.items() if v[0] < 300}
        with open(f"{OUT}/sweep_non_object.json", "w") as handle:
            json.dump({"routes": len(routes()), "requests": len(after), "demo_error": getattr(self, "demo_error", ""),
                       "crashed_now": crashed, "accepted_now": accepted, "unworded_400": unworded,
                       "worked_before_the_parser": worked_before,
                       "crashed_before": {k: v for k, v in before.items() if v[0] >= 500}.__len__()}, handle, indent=1)
        self.assertEqual((len(crashed), len(accepted), len(worked_before)), (0, 0, 0),
                         f"see {OUT}/sweep_non_object.json")

    def test_sweep_broken_empty_and_empty_object_bodies(self):
        import os
        os.makedirs(OUT, exist_ok=True)
        got = self.sweep(self.OTHER)
        crashed = {k: v for k, v in got.items() if v[0] >= 500}
        with open(f"{OUT}/sweep_other.json", "w") as handle:
            json.dump({"requests": len(got), "crashed": crashed}, handle, indent=1)
        self.assertEqual(len(crashed), 0, f"see {OUT}/sweep_other.json")
