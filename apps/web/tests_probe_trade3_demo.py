"""Review probe (trade3): the demo loader with the lock-order check ON (unchecked() made a no-op)."""
import contextlib
import re
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


def load(**options):
    with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
        executor.return_value.migration_plan.return_value = []
        call_command("load_demo", **options)


class DemoWithTheCheckOn(TestCase):
    def test_demo_with_only_party_order_violations_ignored(self):
        """Same, but a violation between two customers is logged and skipped: what else hides behind unchecked()?"""
        from apps.core import lock_order
        real, seen = lock_order.taken, []

        def lenient(connection, label, pks):
            try:
                real(connection, label, pks)
            except lock_order.LockOrderViolation as exc:
                seen.append(str(exc))
                state = lock_order._state(connection)
                state["top"] = None  # start the ranking afresh after logging
        out, err = StringIO(), StringIO()
        message = ""
        with mock.patch("apps.web.management.commands.load_demo.unchecked",
                        new=lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(lock_order, "taken", lenient):
            try:
                load(password="Trial-2026", stdout=out, stderr=err)
            except CommandError as exc:
                message = str(exc)
        from collections import Counter
        kinds = Counter(re.sub(r"\d+", "N", m) for m in seen)
        print("\n=== lenient run: CommandError:", message)
        print("=== violations logged:", len(seen))
        for kind, n in kinds.most_common():
            print(n, kind)
        print("=== stderr:", err.getvalue()[:3000])

    def test_demo_loads_with_the_sentinel_on(self):
        out, err = StringIO(), StringIO()
        message = ""
        with mock.patch("apps.web.management.commands.load_demo.unchecked",
                        new=lambda *a, **k: contextlib.nullcontext()):
            try:
                load(password="Trial-2026", stdout=out, stderr=err)
            except CommandError as exc:
                message = str(exc)
        print("\n=== CommandError:", message)
        text = err.getvalue()
        print("=== stderr (first 9000 chars):\n", text[:9000])
        self.assertEqual(message, "")
