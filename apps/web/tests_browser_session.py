"""
Session time-outs in the browser (L12), with the idle time set to nine
seconds: a Quality Inspector types the start of an alert, is warned,
signed out for want of use, sent to sign in by the next click, and given
back what was typed.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.test import override_settings

from .tests_browser import PASSWORD, BrowserTestCase


@override_settings(SESSION_IDLE_SECONDS=9)
class ASessionEndsInTheBrowserTests(BrowserTestCase):
    def test_warned_signed_out_and_given_back_what_was_typed(self):
        inspector = self.person("Quality Inspector")
        page = self.sign_in(inspector, "/app/quality/alerts/new")
        page.get_by_label("What was found").fill("GSM low on loom 3")
        warning = page.locator(".session-warning")
        expect(warning).to_contain_text("You will be signed out in", timeout=12000)
        expect(warning).to_contain_text("You were signed out for want of use", timeout=12000)
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/login/"))
        page.fill("#id_username", inspector.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(re.compile(r"/app/quality/alerts/new$"))
        expect(page.get_by_label("What was found")).to_have_value("")
        page.get_by_role("button", name="Put it back").click()
        expect(page.get_by_label("What was found")).to_have_value("GSM low on loom 3")
        # Only the refusal that ended it, and the question that told the session from a lack of right.
        self.assertTrue(all("403" in problem for problem in self.problems), self.problems)

    def test_stay_signed_in_keeps_it_open(self):
        page = self.sign_in(self.person("Quality Inspector"), "/app/quality/alerts/new")
        warning = page.locator(".session-warning")
        expect(warning).to_contain_text("You will be signed out in", timeout=12000)
        page.get_by_role("button", name="Stay signed in").click()
        expect(warning).to_have_count(0)
        page.wait_for_timeout(5000)
        page.get_by_label("What was found").fill("Still here")
        expect(page.get_by_label("What was found")).to_have_value("Still here")
        self.assertEqual(self.problems, [])
