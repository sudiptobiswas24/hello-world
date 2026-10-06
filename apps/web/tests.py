"""
The office application's connection to the server: who gets the page,
what it may load, who it says is signed in, and that every list field a
view declares exists.
"""

import datetime
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import FieldDoesNotExist
from django.core.management import call_command
from django.test import TestCase
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.core.api import _field_at
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase

PAGE = b'<!doctype html><html><head><script type="module" src="/static/web/assets/app.js"></script></head></html>'


class ShellTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.built = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
        self.built.write(PAGE)
        self.built.close()
        self.addCleanup(os.unlink, self.built.name)

    def person(self, *roles, password="right-horse-7"):
        user = User.objects.create_user("-".join(roles).replace(" ", "_") or "nobody", password=password,
                                        first_name="Asha", last_name="Rao")
        user.groups.add(*Group.objects.filter(name__in=roles))
        return user

    def page(self, path="/app/", found=True):
        with patch("apps.web.views.finders.find", return_value=self.built.name if found else None):
            return self.client.get(path)


class ThePageTests(ShellTestCase):
    def test_signed_out_goes_to_sign_in_and_comes_back(self):
        response = self.page("/app/sales/invoices?q=bolt")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/login/?next=/app/sales/invoices%3Fq%3Dbolt")

    def test_any_address_under_the_application_is_the_same_page(self):
        self.client.force_login(self.person("AR Manager"))
        for path in ("/app/", "/app/sales/invoices", "/app/no/such/screen"):
            response = self.page(path)
            self.assertEqual((response.status_code, response.content), (200, PAGE), path)

    def test_it_may_load_and_talk_to_this_server_only(self):
        self.client.force_login(self.person("AR Manager"))
        response = self.page()
        policy = response["Content-Security-Policy"]
        for rule in ("default-src 'self'", "script-src 'self'", "connect-src 'self'",
                     "frame-ancestors 'none'", "object-src 'none'", "form-action 'self'"):
            self.assertIn(rule, policy)
        self.assertNotIn("unsafe", policy)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_it_is_never_cached_and_hands_over_the_csrf_cookie(self):
        self.client.force_login(self.person("AR Manager"))
        response = self.page()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("csrftoken", response.cookies)
        self.assertFalse(response.cookies["csrftoken"]["httponly"])

    def test_not_built_says_so_rather_than_a_blank_page(self):
        self.client.force_login(self.person("AR Manager"))
        response = self.page(found=False)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"npm run build", response.content)

    def test_the_root_opens_the_application(self):
        response = self.client.get("/")
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"))


class SignInTests(ShellTestCase):
    def test_signing_in_from_the_address_bar_lands_in_the_application(self):
        self.person("AR Manager")
        response = self.client.post("/accounts/login/", {"username": "AR_Manager", "password": "right-horse-7"})
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"))

    def test_the_page_speaks_to_whoever_is_signing_in(self):
        office = self.client.get("/accounts/login/?next=/app/").content.decode()
        station = self.client.get("/accounts/login/?next=/station/LX1/").content.decode()
        self.assertIn("Use your own account", office)
        self.assertNotIn("PIN", office)
        self.assertIn("PIN", station)

    def test_signing_out_needs_the_form_and_ends_the_session(self):
        self.client.force_login(self.person("AR Manager"))
        self.assertEqual(self.client.get("/api/core/me/").status_code, 200)
        response = self.client.post("/accounts/logout/", {"next": "/accounts/login/?next=/app/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/core/me/").status_code, 403)

    def test_a_write_without_the_csrf_token_is_refused(self):
        client = APIClient(enforce_csrf_checks=True)
        client.login(username=self.person("AR Manager").username, password="right-horse-7")
        response = client.post("/api/core/parties/", {"code": "X", "name": "X"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", str(response.json()))


class MeTests(ShellTestCase):
    def test_who_is_signed_in_and_what_they_may_do(self):
        self.client.force_login(self.person("Sales Rep"))
        me = self.client.get("/api/core/me/").json()
        self.assertEqual((me["name"], me["roles"], me["is_superuser"]), ("Asha Rao", ["Sales Rep"], False))
        self.assertIn("sales.view_invoice", me["permissions"])
        self.assertNotIn("accounting.view_journalentry", me["permissions"])
        self.assertNotIn("hr.view_payslip", me["permissions"])

    def test_a_login_with_no_role_may_do_nothing(self):
        self.client.force_login(self.person())
        self.assertEqual(self.client.get("/api/core/me/").json()["permissions"], [])

    def test_nobody_signed_in_is_nobody(self):
        self.assertEqual(self.client.get("/api/core/me/").status_code, 403)

    def test_it_cannot_be_written(self):
        self.client.force_login(self.person("AR Manager"))
        self.assertEqual(self.client.post("/api/core/me/", {}).status_code, 405)


class DeclaredListFieldsTests(TestCase):
    """
    A field a list declares that does not exist is a 500 the first time
    someone narrows by it. Every one, on every view, resolved here.
    """

    def views(self):
        found = {}

        def walk(patterns):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):
                    walk(pattern.url_patterns)
                    continue
                cls = getattr(pattern.callback, "cls", None)
                if cls is not None and getattr(cls, "queryset", None) is not None:
                    found[cls] = cls.queryset.model

        walk(get_resolver().url_patterns)
        return found

    def test_every_search_filter_date_and_ordering_field_exists(self):
        broken = []
        for view, model in self.views().items():
            names = [*getattr(view, "filter_fields", ()), *(getattr(view, "ordering_fields", None) or ())]
            names += [name.lstrip("^=@$") for name in getattr(view, "search_fields", None) or ()]
            if getattr(view, "date_field", None):
                names.append(view.date_field)
            for name in names:
                try:
                    _field_at(model, name.removesuffix("__isnull"))
                except (FieldDoesNotExist, AttributeError):
                    broken.append(f"{view.__name__}: {name}")
        self.assertEqual(broken, [])

    def test_the_views_were_found(self):
        self.assertGreater(len(self.views()), 100)


class EveryAddressAScreenAsksForExistsTests(TestCase):
    """
    A screen that asks for an address the server does not have shows an
    error panel, and only on the day someone opens it: bins were asked
    for at storage-bins/ until the sweep happened to open the list.
    """

    def test_every_api_path_in_the_office_application_resolves(self):
        import pathlib
        import re

        from django.urls import Resolver404, resolve

        source = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
        found = set()
        for path in source.rglob("*.ts*"):
            found.update(re.findall(r'"(/api/[a-z-]+/[a-z-]+/(?:[a-z-]+/)?)', path.read_text()))
        self.assertGreater(len(found), 50)
        missing = []
        for url in sorted(found):
            try:
                resolve(url)
            except Resolver404:
                missing.append(url)
        self.assertEqual(missing, [])


class EveryCollectionIsKeptOnAScreenTests(TestCase):
    """
    The mirror of the test above. Fifty collections had an API and no
    screen until the S5 and S6 batches; nothing said so, and the only
    way to keep their rows was the Django admin. A collection named only
    as a picker's source is chosen from, not kept, so it does not count.
    """

    # Every collection no screen keeps, and why it needs none.
    NO_SCREEN = {
        "/api/core/party-roles/": (
            "Read only. A party is made in its role, from the customer or vendor list or by HR, "
            "and a second trading role is given on the party page (parties/<id>/roles/)."
        ),
        "/api/core/roles/": (
            "Read to choose a role an approval tier names. Roles are made by setup_roles "
            "and given to people in the admin."
        ),
        "/api/manufacturing/scale-readings/": "Posted by a scale's bridge; nobody lists it.",
        "/api/manufacturing/work-order-operations/": (
            "A run's steps, written by releasing it and shown on its page."
        ),
    }

    def test_every_collection_is_kept_on_a_screen_or_says_why_not(self):
        import pathlib
        import re

        from django.urls import get_resolver, reverse

        source = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
        text = "".join(path.read_text() for path in source.rglob("*.ts*"))
        picker = re.compile(r'(?:FieldDef\["(?:ref|pick)"\]\s*=\s*\{|\b(?:ref|pick):\s*\{)\s*endpoint:\s*"[^"]+"')
        kept = picker.sub("", text)

        def names(patterns):
            for pattern in patterns:
                if hasattr(pattern, "url_patterns"):
                    yield from names(pattern.url_patterns)
                elif pattern.name and pattern.name.endswith("-list"):
                    yield pattern.name

        collections = {reverse(name) for name in names(get_resolver().url_patterns)}
        self.assertGreater(len(collections), 150)
        self.assertEqual(sorted(url for url in collections if url not in kept and url not in self.NO_SCREEN), [])
        # And a reason outlives nothing: a collection given a screen, or
        # gone, comes off the list.
        self.assertEqual(sorted(url for url in self.NO_SCREEN if url in kept or url not in collections), [])


class HasOneFilterTests(SalesTestCase):
    def test_credit_notes_are_the_invoices_that_credit_one(self):
        call_command("setup_roles", verbosity=0)
        invoice = Invoice.objects.create(customer=self.customer, invoice_date=datetime.date(2026, 3, 1),
                                         receivable_account=self.ar, currency=self.usd)
        line = InvoiceLine.objects.create(invoice=invoice, item=self.item, quantity=Decimal("2"),
                                          unit_price=Decimal("10"), revenue_account=self.revenue)
        invoice.post()
        note = invoice.create_credit_note(quantities={line: Decimal("1")})
        user = User.objects.create_user("ar")
        user.groups.add(Group.objects.get(name="AR Manager"))
        client = APIClient()
        client.force_authenticate(user)
        ids = lambda value: [row["id"] for row in client.get(  # noqa: E731
            "/api/sales/invoices/", {"credits__isnull": value}).json()]
        self.assertEqual((ids("false"), ids("true")), ([note.pk], [invoice.pk]))
        self.assertEqual(client.get("/api/sales/invoices/", {"credits__isnull": "perhaps"}).status_code, 400)
