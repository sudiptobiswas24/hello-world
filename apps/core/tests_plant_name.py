"""
The plant's own name where people meet the application: the sign-in page, the authenticator app, the tab.

Seen on a first installation (2 November): a fresh install's sign-in page said "Deccan Polysacks", the demo's
company, and so would every employee's authenticator app and browser tab. The demo's name had been written into
the page, the second factor and the shell.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

from apps.core.models import Company
from apps.core.second_factor import issuer, plant_name

DEMO = "Deccan"


class ThePlantsOwnNameTests(TestCase):
    def test_the_sign_in_page_names_the_company_and_never_the_demos(self):
        Company.objects.create(name="Bengal Woven Sacks")
        page = self.client.get("/accounts/login/").content.decode()
        self.assertIn("Bengal Woven Sacks. Use your own account", page)
        self.assertNotIn(DEMO, page)

    def test_before_there_is_a_company_the_page_names_none_and_makes_none(self):
        page = self.client.get("/accounts/login/").content.decode()
        self.assertIn("<p>Use your own account", page)
        self.assertNotIn(DEMO, page)
        self.assertFalse(Company.objects.exists())

    def test_the_authenticator_lists_the_code_under_the_plants_name(self):
        self.assertEqual((plant_name(), issuer()), ("", "Plant ERP"))
        Company.objects.create(name="Bengal Woven Sacks")
        self.assertEqual(issuer(), "Bengal Woven Sacks ERP")


class NoLiveSourceNamesTheDemoTests(TestCase):
    """Only the demo and the tests may name the demo's company: anywhere else, every plant would read it."""

    def test_no_page_template_or_screen_names_the_demo_company(self):
        root = Path(settings.BASE_DIR)
        sources = [root / "frontend" / "index.html", *(root / "frontend" / "src").rglob("*.*")]
        for pattern in ("*.py", "*.html", "*.txt"):
            sources += (root / "apps").rglob(pattern)
        sources += (root / "config").rglob("*.py")
        named = sorted(
            str(path.relative_to(root)) for path in sources
            if path.is_file() and "tests" not in path.name and path.name != "load_demo.py"
            and "static" not in path.parts and "migrations" not in path.parts
            and DEMO in path.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(named, [])
