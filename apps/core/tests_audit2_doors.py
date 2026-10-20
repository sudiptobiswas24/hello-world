"""
audit2_core probes: doors open to a login that holds no role at all.

Claim: a login with no role may do nothing (apps/web/tests.py says so of /me/).
Walk every address the API serves that takes no argument and ask each by GET as
that login; whatever answers 200 is a door the permission classes left open.
"""
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient


def api_paths(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        pattern = str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from api_paths(entry, prefix + pattern)
        elif isinstance(entry, URLPattern):
            full = prefix + pattern
            if full.startswith("^"):
                full = full[1:]
            if "(?P" in full or "<" in full or full.endswith("$") and "(" in full:
                continue
            yield full


class NoRoleDoorsTests(TestCase):
    def test_a_login_with_no_role_is_let_through_nothing(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("norole")
        client = APIClient()
        client.force_authenticate(user)
        opened = []
        for path in sorted(set(api_paths())):
            clean = path.replace("\\.", ".").replace("$", "").replace("^", "")
            if not clean.startswith("api/") or "(" in clean:
                continue
            response = client.get("/" + clean)
            if response.status_code < 300:
                opened.append((clean, response.status_code))
        # The API roots only list addresses; a login's own saved views, the list of endpoints, its inbox and a
        # search (which asks each kind's permission) answer for any login. Read by hand: all hold.
        own = {"api/core/me/", "api/core/saved-filters/", "api/core/endpoints/", "api/web/", "api/web/inbox/",
               "api/web/search/"}
        roots = {p for p, _ in opened if p.count("/") == 2}
        self.assertEqual([p for p, _ in opened if p not in own | roots], [])
