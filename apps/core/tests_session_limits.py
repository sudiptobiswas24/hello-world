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


class SignedInCase(TestCase):
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

    def page(self, seconds):
        with self.at(seconds):
            return self.web.get("/accounts/password_change/")



class SessionLimitTests(SignedInCase):
    def test_idle_for_the_setting_it_ends(self):
        self.assertEqual(self.me(1799).status_code, 200)
        self.assertEqual(self.me(1799 + 1799).status_code, 200)
        ended = self.me(1799 + 1799 + 1800)
        self.assertEqual((ended.status_code, ended["X-Session-Ended"]), (403, "idle"))
        # And stays ended: the session was flushed, not paused.
        self.assertEqual(self.me(1799 + 1799 + 1801).status_code, 403)

    def test_the_pages_end_with_it(self):
        self.assertEqual(self.page(1000).status_code, 200)
        ended = self.page(1000 + 1800)
        self.assertEqual((ended.status_code, ended["X-Session-Ended"]), (302, "idle"))
        self.assertTrue(ended["Location"].startswith("/accounts/login/"))

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


class TheIdleClockIsACookieNotTheSessionTests(SignedInCase):
    """
    The sweep's 400s, and a lost update on PostgreSQL: the idle clock was
    written into the session on ordinary requests. Now nothing saves the
    session on one; the clock is a signed cookie bound to the session.
    """

    def row(self):
        from django.contrib.sessions.models import Session

        found = Session.objects.get(session_key=self.web.session.session_key)
        return found.session_data, found.expire_date

    def test_an_ordinary_request_and_a_poll_leave_the_session_row_as_it_was(self):
        self.me(30)
        before = self.row()
        self.assertEqual(self.me(60).status_code, 200)
        self.assertEqual(self.me(90, HTTP_X_SESSION_CHECK="1").status_code, 200)
        self.assertEqual(self.page(120).status_code, 200)
        self.assertEqual(self.row(), before)

    def test_the_cookie_carries_the_session_cookies_flags(self):
        cookie = self.me(30).cookies["erp_idle"]
        self.assertEqual((cookie["path"], bool(cookie["httponly"]), cookie["samesite"]), ("/", True, "Lax"))
        self.assertNotIn(self.web.session.session_key, cookie.value)

    def keep_it_fresh_then(self, cookie):
        """Requests every 25 minutes for 50 minutes, then one more with the cookie as `cookie(now's)` gives it."""
        for seconds in (1500, 3000):
            self.assertEqual(self.me(seconds).status_code, 200)
        cookie_value = cookie(self.web.cookies["erp_idle"].value)
        if cookie_value is None:
            del self.web.cookies["erp_idle"]
        else:
            self.web.cookies["erp_idle"] = cookie_value
        return self.me(3060)

    def test_a_fresh_cookie_within_the_limit_keeps_it_open(self):
        self.assertEqual(self.keep_it_fresh_then(lambda now: now).status_code, 200)

    def test_a_missing_cookie_is_no_request_seen_since_signing_in(self):
        self.assertEqual(self.keep_it_fresh_then(lambda now: None)["X-Session-Ended"], "idle")

    def test_a_tampered_cookie_is_none(self):
        forged = (lambda now: now[:-2] + ("AA" if now[-2:] != "AA" else "BB"))
        self.assertEqual(self.keep_it_fresh_then(forged)["X-Session-Ended"], "idle")

    def test_another_sessions_cookie_is_none(self):
        other = Client()
        with self.at(2990):
            other.post("/accounts/login/", {"username": "stores", "password": PW})
            other.get(ME)
        theirs = other.cookies["erp_idle"].value
        self.assertEqual(self.keep_it_fresh_then(lambda now: theirs)["X-Session-Ended"], "idle")

    def test_a_stale_cookie_ends_it_over_the_api_and_the_pages(self):
        self.assertEqual(self.me(1000).status_code, 200)
        stale = self.web.cookies["erp_idle"].value
        self.assertEqual(self.me(2799).status_code, 200)  # the cookie moves on with it...
        self.web.cookies["erp_idle"] = stale  # ...but one left from 1000 is idle at 2800
        self.assertEqual(self.me(2800)["X-Session-Ended"], "idle")


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
