"""
PostgreSQL probes for the review of fix_sec3 (g22 review_sec): a code, a
recovery code, and a burst of wrong codes, each submitted by several
requests at once. SQLite serialises writers, so these skip there.

Run: DATABASE_URL=postgres://erp:erp@localhost:5432/review_sec PYTHONPATH=<scratchpad> \
     .venv/bin/python manage.py test apps.core.tests_probe_review_sec_races --settings=fastsettings
"""

import datetime
import threading
import unittest
from unittest import mock

import pyotp
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from . import second_factor
from .second_factor import SecondFactor

PW = "Mill-Gate-Pw-2026-qz"


def at_once(count, work):
    """Run work(index) in `count` threads released together; their results in order."""
    barrier = threading.Barrier(count, timeout=10)
    results = [None] * count

    def run(index):
        try:
            results[index] = work(index, barrier)
        except Exception as exc:  # noqa: BLE001
            results[index] = f"error: {exc!r}"
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


@unittest.skipUnless(connection.vendor == "postgresql", "races need PostgreSQL")
class CodeRaceProbes(TransactionTestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.user = User.objects.create_user("ctl", password=PW)
        self.user.groups.add(Group.objects.get(name="Controller"))
        self.factor = SecondFactor.objects.create(user=self.user, secret=pyotp.random_base32(),
                                                  confirmed_at=timezone.now())

    def half_signed_in(self):
        web = Client()
        answer = web.post("/accounts/login/", {"username": "ctl", "password": PW})
        assert answer.status_code == 302, answer.status_code
        return web

    def test_r01_one_code_submitted_by_two_requests_at_once_signs_in_once(self):
        code = pyotp.TOTP(self.factor.secret).now()
        clients = [self.half_signed_in(), self.half_signed_in()]
        real = second_factor._took_step

        def work(index, barrier):
            return clients[index].post(second_factor.STEP_URL, {"code": code}).status_code

        # Both requests are held after reading the code as valid and before taking its step.
        gate = threading.Barrier(2, timeout=5)

        def held(factor, step):
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
            return real(factor, step)

        with mock.patch.object(second_factor, "_took_step", held):
            results = at_once(2, work)
        self.assertEqual(sorted(results), [200, 302], f"two submissions of one code: {results}")

    def test_r02_a_recovery_code_submitted_by_eight_requests_at_once_signs_in_once(self):
        codes = second_factor.new_recovery_codes(self.factor)
        clients = [self.half_signed_in() for _ in range(8)]

        def work(index, barrier):
            barrier.wait()
            return clients[index].post(second_factor.STEP_URL, {"code": codes[0]}).status_code

        results = at_once(8, work)
        self.assertEqual(results.count(302), 1, f"one recovery code, eight requests at once: {results}")

    def test_r03_a_burst_of_wrong_codes_is_cut_off_at_five_not_at_the_size_of_the_burst(self):
        clients = [self.half_signed_in() for _ in range(30)]
        shown = {pyotp.TOTP(self.factor.secret).at(timezone.now() + datetime.timedelta(seconds=s)) for s in (-30, 0, 30)}
        wrong = [c for c in (f"{n:06d}" for n in range(111111, 111111 + 60)) if c not in shown][:30]

        def work(index, barrier):
            barrier.wait()
            body = clients[index].post(second_factor.STEP_URL, {"code": wrong[index]}).content.decode()
            return "judged" if "did not match" in body else "locked" if "locked" in body else body[:60]

        results = at_once(30, work)
        judged = results.count("judged")
        print(f"R03 judged={judged} locked={results.count('locked')} other={[r for r in results if r not in ('judged', 'locked')][:3]}")
        self.assertLessEqual(judged, 8, f"{judged} of 30 simultaneous wrong codes were each judged before the lock "
                                        f"took hold (5 were allowed): {sorted(set(results))}")
