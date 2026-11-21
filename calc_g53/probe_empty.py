"""Throwaway: every list route on the demo, read as the administrator; which answer nothing, which fail."""
import json
from io import StringIO

from django.test import TestCase
from django.urls import get_resolver, URLPattern, URLResolver

from apps.web.tests_load_demo import load


def routes(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from routes(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p


class Empty(TestCase):
    @classmethod
    def setUpTestData(cls):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())

    def test_sweep(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        from apps.core.tests_second_factor import for_tests
        client = APIClient()
        assert client.login(username="admin", password="Trial-2026")
        for_tests(client, get_user_model().objects.get(username="admin"))
        empty, failed, full = [], [], 0
        seen = set()
        for path, p in routes(get_resolver().url_patterns):
            if not path.startswith("api/") or "<" in path or "(?P" in path:
                continue
            name = getattr(p, "name", "") or ""
            if not name.endswith("-list"):
                continue
            url = "/" + path.replace("^", "").replace("$", "").replace("\\.(?P<format>[a-z0-9]+)/?", "")
            if url in seen:
                continue
            seen.add(url)
            answer = client.get(url)
            if answer.status_code != 200:
                failed.append((url, answer.status_code, answer.content[:160]))
                continue
            try:
                body = json.loads(answer.content)
            except ValueError:
                continue
            rows = body if isinstance(body, list) else body.get("results") if isinstance(body, dict) else None
            if rows is None:
                continue
            if len(rows) == 0:
                empty.append(url)
            else:
                full += 1
        print("\nFULL", full, "EMPTY", len(empty), "FAILED", len(failed))
        for url in sorted(empty):
            print("EMPTY", url)
        for row in failed:
            print("FAILED", row)
