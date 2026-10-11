"""
The HR Admin in the browser makes a login for the new bookkeeper with a
first password and gives it its role from the page. Not holding that
role, HR only proposes it (O142, the owner's two-person rule); a
bookkeeper, signed in beside, finds it waiting for them and confirms it,
issuing asha's password with it: the one HR chose stops working (O157).
Later HR deactivates the login and the sign-in is refused.
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
        proposed = page.locator("section.related", has_text="Roles proposed")
        expect(proposed.locator("tbody")).to_contain_text("Bookkeeper")
        self.assertEqual(list(asha.groups.all()), [])

        books = self.new_page()
        self.sign_in(self.person("Bookkeeper"), "/app/settings/role-proposals", page=books)
        books.get_by_role("link", name="asha").click()
        books.wait_for_url(re.compile(r"/settings/role-proposals/\d+$"))
        books.get_by_role("button", name="Confirm").click()
        confirm = books.get_by_role("form", name="Confirm")
        confirm.get_by_label("Their password").fill("ledger-desk-2026")
        confirm.get_by_role("button", name="Confirm").click()
        expect(books.locator("main")).to_contain_text("Confirmed")
        self.assertEqual(list(asha.groups.values_list("name", flat=True)), ["Bookkeeper"])
        # O157: the first password the HR Admin chose stops working; the one the bookkeeper issued is asha's.
        self.assertFalse(Client().login(username="asha", password="loom-shed-2026"))
        self.assertTrue(Client().login(username="asha", password="ledger-desk-2026"))

        page.reload()
        roles = page.locator("section.related", has_text="Roles").first
        expect(roles.locator("tbody")).to_contain_text("Bookkeeper")
        page.get_by_role("button", name="Deactivate").click()
        expect(page.locator("main")).to_contain_text("Deactivated")
        asha.refresh_from_db()
        self.assertFalse(asha.is_active)
        self.assertFalse(Client().login(username="asha", password="ledger-desk-2026"))
        self.assertEqual(self.problems, [])
