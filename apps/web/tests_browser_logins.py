"""
The HR Admin in the browser makes a login for the new bookkeeper with a
first password, gives it its role from the page, and the person signs
in; later deactivates it and the sign-in is refused.
"""

import re

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.contrib.auth.models import User
from django.test import Client

from .tests_browser import BrowserTestCase


class LoginsInTheBrowserTests(BrowserTestCase):
    def test_made_given_a_role_and_let_go(self):
        page = self.sign_in(self.person("HR Admin"), "/app/settings/logins/new")
        page.get_by_label("Login", exact=True).fill("asha")
        page.get_by_label("First name").fill("Asha")
        page.get_by_label("First password").fill("loom-shed-2026")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_url(re.compile(r"/settings/logins/\d+$"))
        asha = User.objects.get(username="asha")
        self.assertTrue(Client().login(username="asha", password="loom-shed-2026"))

        page.get_by_role("button", name="Give a role").click()
        form = page.get_by_role("form", name="Give a role")
        form.get_by_label("Role").select_option(label="Bookkeeper")
        form.get_by_role("button", name="Give a role").click()
        roles = page.locator("section.related", has_text="Roles")
        expect(roles.locator("tbody")).to_contain_text("Bookkeeper")
        self.assertEqual(list(asha.groups.values_list("name", flat=True)), ["Bookkeeper"])

        page.get_by_role("button", name="Deactivate").click()
        expect(page.locator("main")).to_contain_text("Deactivated")
        asha.refresh_from_db()
        self.assertFalse(asha.is_active)
        self.assertFalse(Client().login(username="asha", password="loom-shed-2026"))
        self.assertEqual(self.problems, [])
