"""
The auditor opens the Edit log and reads a change made in the shell to a currency, its old and new value; the
record's own History panel shows both too. A rep is offered no Edit log.
"""

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover - the base class skips, saying why
    expect = None

from django.core.management import call_command

from apps.core.models import Currency

from .tests_browser import BrowserTestCase


class EditLogInTheBrowserTests(BrowserTestCase):
    def test_the_auditor_reads_a_shell_change_with_its_old_and_new_value(self):
        call_command("setup_roles", verbosity=0)
        currency = Currency.objects.create(code="AED", name="Dirham")
        call_command("shell", command=f"from apps.core.models import Currency; c = Currency.objects.get(pk={currency.pk}); "
                                      "c.name = 'UAE dirham'; c.save()")
        page = self.sign_in(self.person("Auditor"), "/app/settings/edit-log")
        row = page.locator("tr", has_text="UAE dirham").first
        expect(row).to_contain_text("shell")
        expect(row).to_contain_text("Dirham → UAE dirham")
        self.assertEqual(self.problems, [])

    def test_a_rep_is_not_offered_it(self):
        call_command("setup_roles", verbosity=0)
        page = self.sign_in(self.person("Sales Rep"), "/app/settings")
        expect(page.get_by_role("link", name="Edit log")).to_have_count(0)
