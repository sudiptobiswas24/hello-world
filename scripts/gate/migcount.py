"""
Print each app holding migration-tagged tests, with its count, and the total:

    .venv/bin/python scripts/gate/migcount.py          # app count ... total N
    .venv/bin/python scripts/gate/migcount.py --skips  # the test ids this database skips

The skips are decided when a class is imported (skipUnless on the vendor),
so they can be read without running anything. On SQLite they are the
PostgreSQL-only migration tests, which the mig lane then runs on PostgreSQL:
before g25 one had never run in any gate.

The mig lane runs one app at a time from this list, and gateline.py checks
the apps' logs add up to the total, so an app that gains its first migration
test cannot be left out unnoticed.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.test.runner import DiscoverRunner  # noqa: E402

suite = DiscoverRunner(tags=["migration"], verbosity=0).build_suite(["apps"])
if sys.argv[1:] == ["--skips"]:
    for test in suite:
        method = getattr(test, test._testMethodName)
        if getattr(type(test), "__unittest_skip__", False) or getattr(method, "__unittest_skip__", False):
            print(test.id())
    sys.exit(0)
apps = collections.Counter(test.id().split(".")[1] for test in suite)
for app in sorted(apps):
    print(app, apps[app])
print("total", sum(apps.values()))
