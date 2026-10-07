"""
The morning after: an invoice 62 days overdue, a fire NOC thirty days
from lapsing, and a draft bill three days old. The controller's inbox
holds the invoice, the licence and the draft; the stores' holds none of
them. The morning mail goes once to whoever has something waiting and an
address, says nothing to the rest, and prints instead where no mail
server is set up.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.licences import Licence, LicenceKind
from apps.imports.importer import run
from apps.imports.tests import ImportTestCase
from apps.purchasing.models import Bill

from .checks import inbox


class ChecksTestCase(ImportTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        self.today = timezone.localdate()
        ago = lambda days: self.today - datetime.timedelta(days=days)  # noqa: E731
        report = run("open_invoices", f"customer,reference,date,amount\nC-1,OLD/1,{ago(92)},20000\n",
                     commit=True, against="3900")
        self.assertTrue(report.committed, report.errors)
        Licence.objects.create(kind=LicenceKind.FIRE, licence_number="FIRE/NOC/77", valid_from=ago(335),
                               valid_to=self.today + datetime.timedelta(days=30))
        from apps.core.models import Party, PartyRole, PartyRoleAssignment

        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        Bill.objects.create(vendor=vendor, bill_date=ago(3), currency=self.usd, payable_account=self.payable)

    def person(self, role, email=""):
        user = User.objects.create_user(role.replace(" ", "_").lower(), email=email)
        user.groups.add(Group.objects.get(name=role))
        return user

    def keys(self, user):
        return {row["key"]: row["count"] for row in inbox(user)}


class InboxTests(ChecksTestCase):
    def test_each_login_sees_what_it_may_act_on(self):
        controller = self.keys(self.person("Controller"))
        self.assertEqual((controller.get("invoices_overdue"), controller.get("licences_due"), controller.get("bills_draft")),
                         (1, 1, 1))
        stores = self.keys(self.person("Warehouse Staff"))
        self.assertNotIn("invoices_overdue", stores)
        self.assertNotIn("licences_due", stores)
        self.assertNotIn("bills_draft", stores)

    def test_what_recurs_and_is_due_is_in_the_inbox_of_whoever_runs_it(self):
        from apps.accounting.recurring import RecurringJournal
        from apps.sales.models import RecurringInvoice

        RecurringJournal.objects.create(code="RENT", memo="Rent", start_date=self.today - datetime.timedelta(days=3))
        RecurringJournal.objects.create(code="LATER", memo="Not yet", start_date=self.today + datetime.timedelta(days=3))
        RecurringInvoice.objects.create(code="AMC", customer=self.customer, receivable_account=self.ar,
                                        interval="monthly", start_date=self.today)
        controller = self.keys(self.person("Controller"))
        self.assertEqual(controller.get("recurring_journals_due"), 1)
        self.assertNotIn("recurring_invoices_due", controller)
        self.assertEqual(self.keys(self.person("AR Manager")).get("recurring_invoices_due"), 1)
        self.assertNotIn("recurring_journals_due", self.keys(self.person("Warehouse Staff")))

    def test_the_api_answers_the_login_itself(self):
        client = APIClient()
        client.force_authenticate(self.person("Controller"))
        got = client.get("/api/web/inbox/")
        self.assertEqual(got.status_code, 200, got.content)
        rows = {row["key"]: row for row in got.json()["rows"]}
        self.assertEqual((rows["invoices_overdue"]["count"], rows["invoices_overdue"]["href"]), (1, "/sales/aging"))
        self.assertEqual(APIClient().get("/api/web/inbox/").status_code in (401, 403), True)


class MorningMailTests(ChecksTestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_one_mail_to_whoever_has_something_waiting(self):
        self.person("Controller", email="controller@example.com")
        self.person("Warehouse Staff", email="stores@example.com")
        self.person("AR Manager")  # no address: printed, not mailed
        call_command("morning_checks", verbosity=0)
        self.assertEqual([message.to for message in mail.outbox], [["controller@example.com"]])
        self.assertIn("Invoices past due: 1", mail.outbox[0].body)
        self.assertIn("Licences to renew: 1", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="apps.core.mail.NotConfiguredBackend")
    def test_without_a_mail_server_it_prints_and_says_so(self):
        from io import StringIO

        self.person("Controller", email="controller@example.com")
        out = StringIO()
        call_command("morning_checks", stdout=out)
        self.assertIn("Email is not set up", out.getvalue())
        self.assertIn("Invoices past due: 1", out.getvalue())
        self.assertIn("0 mailed, 1 printed", out.getvalue())
