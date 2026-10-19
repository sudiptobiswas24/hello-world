"""
Browser probes for the review of fix_sec3 (g22 review_sec): the screens as
the people in their roles, and the draft kept in the browser. Screenshots
go to $REVIEW_SEC_SHOTS when it is set.

Run: PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium (after `npm run build`)
"""

import os
import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

import datetime

import pyotp
from django.contrib.auth.models import Group, User
from django.test import override_settings
from django.utils import timezone

from .tests_browser import PASSWORD, BrowserTestCase

SHOTS = os.environ.get("REVIEW_SEC_SHOTS")


def shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=True)


@override_settings(SESSION_IDLE_SECONDS=9)
class DraftProbes(BrowserTestCase):
    def second_inspector(self):
        user = User.objects.create_user("inspector_b", password=PASSWORD, first_name="Ben")
        user.groups.add(Group.objects.get(name="Quality Inspector"))
        return user

    def kept_keys(self, page):
        return page.evaluate("() => Object.keys(window.localStorage).filter((k) => k.startsWith('erp.kept-draft:'))")

    def test_b01_what_one_person_typed_is_not_offered_to_the_next_person_at_the_computer(self):
        a = self.person("Quality Inspector")
        b = self.second_inspector()
        page = self.sign_in(a, "/app/quality/alerts/new")
        page.get_by_label("What was found").fill("A's note: GSM low on loom 3")
        warning = page.locator(".session-warning")
        expect(warning).to_contain_text("You will be signed out in", timeout=12000)
        shot(page, "05_timeout_warning")
        expect(warning).to_contain_text("You were signed out for want of use", timeout=12000)
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/login/"))
        # A walks away. The next person signs in at this computer, as themselves.
        page.fill("#id_username", b.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(re.compile(r"/app/quality/alerts/new$"))
        expect(page.get_by_label("What was found")).to_be_visible()
        shot(page, "06_draft_offered_to_the_next_person")
        offered = page.get_by_role("button", name="Put it back").count()
        if offered:
            page.get_by_role("button", name="Put it back").click()
        seen_by_b = page.get_by_label("What was found").input_value()
        self.assertEqual((offered, seen_by_b), (0, ""),
                         f"B was offered A's draft ({offered} offer) and putting it back showed B: {seen_by_b!r}")

    def test_b02_a_kept_draft_does_not_outlive_the_sign_out_of_the_next_person(self):
        a = self.person("Quality Inspector")
        b = self.second_inspector()
        page = self.sign_in(a, "/app/quality/alerts/new")
        page.get_by_label("What was found").fill("A's note")
        expect(page.locator(".session-warning")).to_contain_text("You were signed out for want of use", timeout=25000)
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/accounts/login/"))
        self.assertTrue(self.kept_keys(page), "control: the draft is kept in the browser when the session ends")
        page.fill("#id_username", b.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(re.compile(r"/app/quality/alerts/new$"))
        page.locator("button.avatar").click()
        page.get_by_role("menuitem", name="Sign out").click()
        expect(page.locator("#id_username")).to_be_visible()
        self.assertEqual(self.kept_keys(page), [], "A's typed text is still in this browser after B signed out")


class ScreenProbes(BrowserTestCase):
    """The new screens, as the roles, with a picture of each."""

    def test_b10_the_controller_enrols_signs_in_with_a_code_is_refused_a_wrong_one(self):
        controller = self.person("Controller")
        page = self.page
        page.goto(f"{self.live_server_url}/app/")
        page.fill("#id_username", controller.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        expect(page.get_by_role("heading", name="Set up two-factor sign-in")).to_be_visible()
        shot(page, "01_enrol")
        # A wrong code at enrolment is refused and counted
        page.fill("#id_code", "000000")
        page.get_by_role("button", name="Confirm").click()
        expect(page.locator(".error")).to_be_visible()
        shot(page, "01b_enrol_wrong")
        totp = pyotp.TOTP(page.locator("#secret").inner_text())
        page.fill("#id_code", totp.now())
        page.get_by_role("button", name="Confirm").click()
        expect(page.locator("#recovery-codes li")).to_have_count(10)
        shot(page, "02_recovery_codes")
        page.get_by_role("link", name="I have kept them; continue").click()
        expect(page.get_by_role("navigation", name="Modules")).to_be_visible()
        page.locator("button.avatar").click()
        shot(page, "03_user_menu_controller")
        page.get_by_role("menuitem", name="Sign out").click()
        page.fill("#id_username", controller.username)
        page.fill("#id_password", PASSWORD)
        page.click("button[type=submit]")
        expect(page.get_by_role("heading", name="Sign-in code")).to_be_visible()
        shot(page, "04_code_step")
        # the password-only session reaches nothing: the shell is not served data
        page.goto(f"{self.live_server_url}/api/accounting/payments/")
        self.assertIn(page.locator("body").inner_text().lower().count("results"), (0,))

    def test_b11_an_optional_login_is_offered_the_setup_and_hr_sees_the_reset_button(self):
        stores = self.person("Stores Manager")
        page = self.sign_in(stores, "/app/")
        page.locator("button.avatar").click()
        expect(page.get_by_role("menuitem", name="Set up two-factor sign-in")).to_be_visible()
        shot(page, "07_user_menu_optional")
        page.get_by_role("menuitem", name="Sign out").click()
        # HR resets a factor on the Logins screen
        from apps.core.tests_second_factor import factor_for_tests

        factor_for_tests(stores)
        hr = self.new_page()
        self.sign_in(self.person("HR Admin"), f"/app/settings/logins/{stores.pk}", page=hr)
        expect(hr.get_by_role("button", name="Reset second factor")).to_be_visible()
        shot(hr, "08_logins_reset_button")
        hr.get_by_role("button", name="Reset second factor").click()
        shot(hr, "09_logins_reset_clicked")
