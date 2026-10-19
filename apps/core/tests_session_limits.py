"""
Session time-outs (L12), as a Stores Manager signed in with a password
through the sign-in page. Idle for SESSION_IDLE_SECONDS (30 minutes) the
session ends; a request keeps it open, a check of how long is left does
not; twelve hours after signing in it ends whatever was done.
"""

import time
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

PW = "Shift-Desk-Pw-2026-zq"
ME = "/api/core/me/"


class SessionLimitTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("stores", password=PW)
        user.groups.add(Group.objects.get(name="Stores Manager"))
        self.start = time.time()
        self.web = Client()
        with self.at(0):
            self.assertEqual(self.web.post("/accounts/login/", {"username": "stores", "password": PW}).status_code, 302)

    def at(self, seconds):
        return mock.patch("apps.core.session_limits.time.time", return_value=self.start + seconds)

    def me(self, seconds, **headers):
        with self.at(seconds):
            return self.web.get(ME, **headers)

    def test_idle_for_the_setting_it_ends(self):
        self.assertEqual(self.me(1799).status_code, 200)
        self.assertEqual(self.me(1799 + 1799).status_code, 200)
        ended = self.me(1799 + 1799 + 1800)
        self.assertEqual((ended.status_code, ended["X-Session-Ended"]), (403, "idle"))
        # And stays ended: the session was flushed, not paused.
        self.assertEqual(self.me(1799 + 1799 + 1801).status_code, 403)

    def test_asking_how_long_is_left_keeps_nothing_open(self):
        answer = self.me(1000, HTTP_X_SESSION_CHECK="1")
        self.assertEqual(answer.json()["session"], {"idle_seconds": 1800, "ends_in_seconds": 43200 - 1000,
                                                    "idle_left_seconds": 800})
        self.assertEqual(self.me(1800, HTTP_X_SESSION_CHECK="1").status_code, 403)

    def test_twelve_hours_after_signing_in_it_ends_whatever_was_done(self):
        for minute in range(20, 12 * 60, 20):
            self.assertEqual(self.me(minute * 60).status_code, 200, minute)
        ended = self.me(12 * 3600)
        self.assertEqual((ended.status_code, ended["X-Session-Ended"]), (403, "absolute"))

    @override_settings(SESSION_IDLE_SECONDS=60)
    def test_the_idle_time_is_a_setting(self):
        self.assertEqual(self.me(59).status_code, 200)
        self.assertEqual(self.me(59 + 60).status_code, 403)


class WhatAPollSendsTests(TestCase):
    """Review F6: the polls that run with nobody at the screen say so, or a station PC stays signed in for 12 hours."""

    def test_the_station_pages_scale_poll_sends_the_check_header(self):
        import pathlib
        import re

        page = (pathlib.Path(__file__).resolve().parents[1] / "manufacturing" / "templates" / "manufacturing"
                / "station.html").read_text()
        self.assertIn('api("scale/", { check: true })', page)
        self.assertRegex(page, re.compile(r'if \(options\.check\) init\.headers\["X-Session-Check"\] = "1";'))

    def test_the_office_applications_clock_asks_with_it(self):
        import pathlib

        clock = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "shell" / "SessionClock.tsx")
        self.assertIn('request<{ session?: SessionTimes }>("/api/core/me/", { check: true })', clock.read_text())
