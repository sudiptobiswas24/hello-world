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
from django.test import Client, TestCase
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.core.api import _field_at
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.tests_base import SalesTestCase
from apps.core.tests_second_factor import for_tests, staff_session
from apps.web.tests_browser import BrowserMixin

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
        staff_session(self.client, self.person("AR Manager"))
        for path in ("/app/", "/app/sales/invoices", "/app/no/such/screen"):
            response = self.page(path)
            self.assertEqual((response.status_code, response.content), (200, PAGE), path)

    def test_it_may_load_and_talk_to_this_server_only(self):
        staff_session(self.client, self.person("AR Manager"))
        response = self.page()
        policy = response["Content-Security-Policy"]
        for rule in ("default-src 'self'", "script-src 'self'", "connect-src 'self'",
                     "frame-ancestors 'none'", "object-src 'none'", "form-action 'self'"):
            self.assertIn(rule, policy)
        self.assertNotIn("unsafe", policy)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_it_is_never_cached_and_hands_over_the_csrf_cookie(self):
        staff_session(self.client, self.person("AR Manager"))
        response = self.page()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("csrftoken", response.cookies)
        self.assertFalse(response.cookies["csrftoken"]["httponly"])

    def test_not_built_says_so_rather_than_a_blank_page(self):
        staff_session(self.client, self.person("AR Manager"))
        response = self.page(found=False)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"npm run build", response.content)

    def test_the_root_opens_the_application(self):
        response = self.client.get("/")
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"))


class SignInTests(ShellTestCase):
    def test_signing_in_from_the_address_bar_lands_in_the_application(self):
        import pyotp

        manager = self.person("AR Manager")
        factor = for_tests(Client(), manager)
        response = self.client.post("/accounts/login/", {"username": "AR_Manager", "password": "right-horse-7"})
        # An AR Manager posts payments, so gives a code next (L12), and lands where the address bar asked.
        self.assertEqual((response.status_code, response["Location"]), (302, "/accounts/second-factor/?next=/app/"))
        response = self.client.post(response["Location"], {"code": pyotp.TOTP(factor.secret).now()})
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"))

    def test_the_page_speaks_to_whoever_is_signing_in(self):
        office = self.client.get("/accounts/login/?next=/app/").content.decode()
        station = self.client.get("/accounts/login/?next=/station/LX1/").content.decode()
        self.assertIn("Use your own account", office)
        self.assertNotIn("PIN", office)
        self.assertIn("PIN", station)

    def test_signing_out_needs_the_form_and_ends_the_session(self):
        staff_session(self.client, self.person("AR Manager"))
        self.assertEqual(self.client.get("/api/core/me/").status_code, 200)
        response = self.client.post("/accounts/logout/", {"next": "/accounts/login/?next=/app/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/core/me/").status_code, 403)

    def test_a_write_without_the_csrf_token_is_refused(self):
        client = APIClient(enforce_csrf_checks=True)
        manager = self.person("AR Manager")
        client.login(username=manager.username, password="right-horse-7")
        for_tests(client, manager)
        response = client.post("/api/core/parties/", {"code": "X", "name": "X"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", str(response.json()))


class MeTests(ShellTestCase):
    def test_who_is_signed_in_and_what_they_may_do(self):
        staff_session(self.client, self.person("Sales Rep"))
        me = self.client.get("/api/core/me/").json()
        self.assertEqual((me["name"], me["roles"], me["is_superuser"]), ("Asha Rao", ["Sales Rep"], False))
        self.assertIn("sales.view_invoice", me["permissions"])
        self.assertNotIn("accounting.view_journalentry", me["permissions"])
        self.assertNotIn("hr.view_payslip", me["permissions"])

    def test_the_screens_are_told_the_plants_time_zone(self):
        # What "today" means on every screen: the server's, which production sets to the plant's.
        from django.test import override_settings

        staff_session(self.client, self.person("Sales Rep"))
        with override_settings(TIME_ZONE="America/Chicago"):
            self.assertEqual(self.client.get("/api/core/me/").json()["time_zone"], "America/Chicago")

    def test_a_login_with_no_role_may_do_nothing(self):
        staff_session(self.client, self.person())
        self.assertEqual(self.client.get("/api/core/me/").json()["permissions"], [])

    def test_nobody_signed_in_is_nobody(self):
        self.assertEqual(self.client.get("/api/core/me/").status_code, 403)

    def test_it_cannot_be_written(self):
        staff_session(self.client, self.person("AR Manager"))
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


class _ABrowserTestThatKeepsItsOwnMethod:
    """Made a BrowserMixin below, where it is importable for pickling by name."""

    def note(self):
        pass


class AFailedBrowserSubtestTravelsTests(TestCase):
    """
    A failed subtest goes back to the parallel runner pickled, its test with it. An attribute that
    refers back to the test pickled the test again inside its own __getstate__, which tried every
    attribute again: two such attributes and the worker never answered, and the lane printed nothing.
    """

    def test_what_refers_back_to_the_test_stays_behind_and_the_rest_travels(self):
        import pickle
        import threading

        test = _HeldBrowserTest()
        test.__dict__.update({"kept": 1, "note_failure": test.note, "also": test.note})
        travelled = {}
        thread = threading.Thread(target=lambda: travelled.update(
            state=sorted(pickle.loads(pickle.dumps(test)).__dict__)), daemon=True)
        thread.start()
        thread.join(10)
        self.assertEqual(travelled.get("state"), ["kept"])


class EveryAdderSendsWhatItsListRequiresTests(TestCase):
    """
    A screen's "Add a line" posts what its body names, and the list refuses a field it requires
    and was not sent. The adjustment and transfer screens never sent a line's unit, which the
    model fills from the item but the serializer required: no line could be added from either,
    with every model and API test green (the g22 month). Each adder that creates is asked here.
    """

    def test_every_adder_sends_each_field_its_list_requires(self):
        import pathlib
        import re

        from django.urls import resolve

        source = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "modules"
        asked, wrong = 0, []
        for path in sorted(source.rglob("*.tsx")):
            text = path.read_text()
            for match in re.finditer(r'url: \(\) => "(/api/[a-z-]+/[a-z-]+/)",\s*fields: (?:\([^)]*\) => )?\[(.*?)\],'
                                     r'\s*body: \([^)]*\) => (\(\{.*?\}\)|values)', text, re.S):
                url, fields, body = match.groups()
                func = resolve(url).func
                if getattr(func, "actions", {}).get("post") != "create":
                    continue
                given = set(re.findall(r'key: "(\w+)"', fields))
                sent = given if body == "values" else set(re.findall(r"(\w+):", body))
                if "...values" in body:
                    sent |= given
                required = {name for name, field in func.cls.serializer_class().fields.items()
                            if field.required and not field.read_only}
                asked += 1
                wrong += [f"{path.name}: {url} never sends {name}" for name in sorted(required - sent)]
        self.assertGreater(asked, 25)
        self.assertEqual(wrong, [])


class EverySmartButtonNarrowsItsListTests(TestCase):
    """
    A count on a record's header is its list asked with a narrowing; a
    narrowing the list does not take is refused, and the count reads "—"
    on every page it is on. Each one named in the office application must
    be one its list takes, and its screen must be a screen.
    """

    def test_every_count_asks_its_list_what_it_can_answer(self):
        import pathlib
        import re

        from django.urls import resolve

        from apps.core.api import FieldFilter

        source = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
        found = []
        for path in source.rglob("*.ts*"):
            for endpoint, query, screen in re.findall(
                    r'endpoint: "(/api/[^"]+)", query: \{([^}]*)\},[^}]*?screen: "([^"]+)"', path.read_text(), re.S):
                keys = {part.split(":")[0].strip() for part in query.split(",") if part.strip()}
                found.append((path.name, endpoint, keys, screen))
        self.assertGreater(len(found), 15)
        screens = pathlib.Path(source / "app" / "registry.ts").read_text()
        wrong = []
        for name, endpoint, keys, screen in found:
            view = resolve(endpoint).func.cls
            allowed = set(FieldFilter.ALWAYS) | set(getattr(view, "filter_fields", ())) | set(getattr(view, "extra_params", ()))
            wrong += [f"{name}: {endpoint} by {key}" for key in sorted(keys - allowed)]
            module, _, screen_path = screen.strip("/").partition("/")
            if f'key: "{module}"' not in screens or f'path: "{screen_path}"' not in screens:
                wrong.append(f"{name}: no screen {screen}")
        self.assertEqual(wrong, [])


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
        # L12: an administrator keeps them through the API's own pages for now (deploy/scale_bridge/RUNBOOK.md);
        # a screen of their own is reported, not built (reports/fix_sec3.md).
        "/api/core/integrations/": "Made by an administrator at the API's own page until its screen is built.",
        "/api/core/api-keys/": "Issued and revoked by an administrator at the API's own page until its screen is built.",
        "/api/manufacturing/work-order-operations/": (
            "A run's steps, written by releasing it and shown on its page."
        ),
        # g24 reverse charge: built to the API and the health lines; screens reported, not built (reports/fix_rcm.md).
        "/api/gst/self-invoices/": "Issued by the GST officer through the API until its screen is built.",
        "/api/gst/payment-vouchers/": "Issued by the GST officer through the API until its screen is built.",
        "/api/gst/cash-payments/": "Paid by the AP manager through the API until its screen is built.",
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


class _HeldBrowserTest(BrowserMixin, _ABrowserTestThatKeepsItsOwnMethod):
    pass
