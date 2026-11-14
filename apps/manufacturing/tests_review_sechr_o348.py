"""review_sechr probe: O348, who can read an item's standard cost through ANY endpoint (crawl)."""
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.core.management.commands.setup_roles import ROLES
from .tests_orders import RunTestCase

NEEDLE = "7.7777"


def routes(patterns=None, prefix=""):
    for p in (patterns if patterns is not None else get_resolver().url_patterns):
        route = prefix + str(p.pattern)
        if hasattr(p, "url_patterns"):
            yield from routes(p.url_patterns, route)
        else:
            yield route


class StandardCostCrawl(RunTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        su = User.objects.create_superuser("root", password="x")
        self.su = APIClient(); self.su.force_authenticate(su)
        from apps.inventory.models import Item
        Item.objects.filter(pk=self.tape.pk).update(costing_method="standard", standard_cost="7.7000")
        r = self.su.post(f"/api/inventory/items/{self.tape.pk}/set_standard_cost/", {"standard_cost": NEEDLE}, format="json")
        print("set_standard_cost", r.status_code, r.content[:200])
        self.run_ = self.order(); self.run_.release()

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        c = APIClient(); c.force_authenticate(user)
        return c

    def crawl(self, client):
        leaks = []
        lists = sorted({r for r in routes() if r.startswith("api/") and "<" not in r and "(?P" not in r and "^" not in r.lstrip("^")[:0]})
        urls = []
        for r in lists:
            url = "/" + r.replace("^", "").replace("$", "").rstrip("?")
            urls.append(url)
        for url in urls:
            for suffix in ("", "?format=csv"):
                try:
                    resp = client.get(url + suffix, HTTP_ACCEPT="*/*")
                except Exception as e:  # noqa
                    continue
                if resp.status_code == 200 and NEEDLE in resp.content.decode(errors="ignore"):
                    leaks.append(url + suffix)
                if resp.status_code == 200 and suffix == "":
                    try:
                        body = resp.json()
                    except Exception:
                        continue
                    rows = body if isinstance(body, list) else body.get("results", []) if isinstance(body, dict) else []
                    for row in rows[:3]:
                        if isinstance(row, dict) and "id" in row:
                            d = client.get(f"{url}{row['id']}/")
                            if d.status_code == 200 and NEEDLE in d.content.decode(errors="ignore"):
                                leaks.append(f"{url}{row['id']}/")
                            h = client.get(f"{url}{row['id']}/history/")
                            if h.status_code == 200 and NEEDLE in h.content.decode(errors="ignore"):
                                leaks.append(f"{url}{row['id']}/history/")
        return leaks

    def probe(self, client):
        out = []
        pk = self.tape.pk
        for u in (f"/api/inventory/items/{pk}/", f"/api/inventory/items/{pk}/history/", f"/api/inventory/items/{pk}/revaluations/",
                  "/api/core/history/", f"/api/core/history/?model=inventory.item&id={pk}", "/api/core/audit-log/",
                  f"/api/core/events/?model=inventory.item&id={pk}", "/api/inventory/items/?search=TAPE",
                  "/api/inventory/items/?ordering=standard_cost", "/api/inventory/items/?standard_cost=7.7777",
                  "/api/inventory/items/?format=csv", "/api/inventory/items/export/"):
            r = client.get(u)
            print("  ", u, r.status_code, NEEDLE in r.content.decode(errors="ignore"))
            if r.status_code == 200 and NEEDLE in r.content.decode(errors="ignore"):
                out.append(u)
        return out

    def test_runcost_crawl(self):
        self.run_.refresh_from_db()
        needle = str(self.run_.planned_unit_cost)
        print("run cost needle", needle)
        global NEEDLE
        old, NEEDLE = NEEDLE, needle
        try:
            out = {}
            for role in ("Quality Inspector", "Process Engineer", "Warehouse Staff", "Sales Rep", "Purchasing Clerk", "AP Manager", "AR Manager"):
                leaks = self.crawl(self.as_(role))
                if leaks:
                    out[role] = leaks
            print("SU", self.crawl(self.su))
        finally:
            NEEDLE = old
        self.assertEqual(out, {})

    def test_probe(self):
        print("SU", self.probe(self.su))
        for role in ("Quality Inspector", "Warehouse Staff", "Sales Rep", "Process Engineer", "Purchasing Clerk"):
            print(role, self.probe(self.as_(role)))

    def test_superuser_sees_it_somewhere(self):
        print("SU leaks", self.crawl(self.su))

    def test_blind_roles_see_it_nowhere(self):
        blind = [r for r in ROLES if "inventory.view_stock_value" not in ROLES[r]]
        out = {}
        for role in blind:
            leaks = self.crawl(self.as_(role))
            if leaks:
                out[role] = leaks
        self.assertEqual(out, {})
