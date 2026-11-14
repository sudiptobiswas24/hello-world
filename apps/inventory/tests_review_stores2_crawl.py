"""Review probe (review_stores2): crawl every readable endpoint as the floor roles and list money-looking keys."""
import re
from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.web.tests_load_demo import load

MONEY = re.compile(r"cost|value|worth|price|amount|rate|valu", re.I)


def walk(obj, path=""):
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if MONEY.search(k):
                found.add(path + k)
            found |= walk(v, path + k + ".")
    elif isinstance(obj, list):
        for v in obj[:3]:
            found |= walk(v, path)
    return found


class Crawl(TestCase):
    def test_crawl(self):
        load(password="Trial-2026", stdout=StringIO(), stderr=StringIO())
        call_command("setup_roles", verbosity=0)
        out = {}
        for role in ("Warehouse Staff", "Quality Inspector", "Stores Manager"):
            u = User.objects.create_user(role.replace(" ", "_"))
            u.groups.add(Group.objects.get(name=role))
            c = APIClient(); c.force_authenticate(u)
            seen = {}
            seen_status = []
            mods = ["/api/%s/" % m for m in ("inventory","purchasing","sales","manufacturing","quality","planning","core","accounting","assets")]
            urls = []
            def collect(url, depth=0):
                r = c.get(url)
                if r.status_code != 200:
                    seen_status.append((role, url, r.status_code))
                    return
                j = r.json()
                if isinstance(j, dict) and j and all(isinstance(v, str) and v.startswith("http") for v in j.values()):
                    for v in j.values():
                        collect(v.replace("http://testserver", ""), depth + 1)
                else:
                    urls.append(url)
            for m in mods:
                if isinstance(m, str):
                    collect(m.replace("http://testserver", ""))
            for url in urls:
                r = c.get(url)
                j = r.json()
                rows = j if isinstance(j, list) else j.get("results", j)
                keys = walk(rows)
                if keys:
                    seen[url] = sorted(keys)
                # detail
                if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "id" in rows[0]:
                    d = c.get(url.rstrip("/") + f"/{rows[0]['id']}/")
                    if d.status_code == 200:
                        k2 = walk(d.json())
                        if k2 - set(keys):
                            seen[url + "{id}"] = sorted(k2)
            out[role] = (seen, len(urls))
        import json
        for role in ("Warehouse Staff", "Quality Inspector"):
            print("=====", role, out[role][1], "urls")
            for u, k in out[role][0].items():
                print(u, k)
