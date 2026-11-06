"""
The demo plant: loaded into an empty database through the application's
own posting paths, its books agree with themselves, its logins work and
see what their roles allow, and a second load is refused, so it can
never mix invented records into real books.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.web.health import integrity


def load(**options):
    """
    load_demo, with its migration guard told the schema is complete: a test
    database built without running the migrations (TEST MIGRATE False) reads
    to it as one still being built. The guard has its own test below.
    """
    with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
        executor.return_value.migration_plan.return_value = []
        call_command("load_demo", **options)


class LoadDemoTests(TestCase):
    def test_a_whole_plant_loads_and_its_books_agree(self):
        out = StringIO()
        load(password="Trial-2026", stdout=out, stderr=StringIO())
        self.assertIn("The demo plant is loaded.", out.getvalue())
        self.assertEqual([finding["label"] for finding in integrity(fresh=True)["findings"] if finding["ok"] is False], [])
        self.assert_the_agings_foot_with_a_foreign_document_open()

        # A rep signs in and sees only the customers they carry.
        self.assertTrue(self.client.login(username="imran", password="Trial-2026"))
        rows = self.client.get("/api/sales/sales-orders/", {"page_size": 100}).json()
        self.assertEqual({row["customer_name"] for row in rows},
                         {"Sahyadri Cement Works", "Konkan Agro Foods", "Malwa Seeds and Grains"})

        # Loaded once, the database holds records, and a second load touches nothing.
        parties = Party.objects.count()
        with self.assertRaisesMessage(CommandError, "already holds records"):
            load(stdout=StringIO())
        self.assertEqual(Party.objects.count(), parties)

    def assert_the_agings_foot_with_a_foreign_document_open(self):
        """
        O416: each aging, in rupees, is what its control accounts hold, with a dollar document open on each
        side: the export of 6,000 sacks at USD 0.19 and the second container of filler, 16,000 kg at USD 1.25.
        """
        from django.db.models import Sum
        from django.utils import timezone

        from apps.accounting.models import JournalLine
        from apps.purchasing.models import ap_aging, payable_accounts
        from apps.sales.models import ar_aging, receivable_accounts

        def held(accounts):
            rows = JournalLine.objects.filter(account__in=accounts, entry__posted=True).aggregate(
                debit=Sum("debit"), credit=Sum("credit"))
            return (rows["debit"] or 0) - (rows["credit"] or 0)

        today = timezone.localdate()
        for report, accounts, rows, sign in ((ar_aging(as_of=today), receivable_accounts(), "invoices", 1),
                                             (ap_aging(as_of=today), payable_accounts(), "bills", -1)):
            foreign = [(row["currency"], row["due_in_currency"]) for bucket in report.values() for row in bucket[rows]
                       if row["currency"] == "USD"]
            self.assertTrue(foreign, rows)
            self.assertEqual(sum(bucket["total"] for bucket in report.values()), sign * held(accounts), rows)

    def test_a_database_with_anything_in_it_is_refused(self):
        customer = Party.objects.create(code="C-1", name="A real customer")
        PartyRoleAssignment.objects.create(party=customer, role=PartyRole.CUSTOMER)
        with self.assertRaisesMessage(CommandError, "The demo is for an empty installation only"):
            load(stdout=StringIO())
        self.assertEqual(list(Party.objects.values_list("code", flat=True)), ["C-1"])

    def test_a_database_still_being_built_is_told_to_wait(self):
        """Run during the first start, before the migrations finish: refused in words, nothing made."""
        with mock.patch("apps.web.management.commands.load_demo.MigrationExecutor") as executor:
            executor.return_value.migration_plan.return_value = [("hr.0018_people", False)]
            with self.assertRaisesMessage(CommandError, "still being built: 1 migration(s) to go"):
                call_command("load_demo", stdout=StringIO())
        self.assertFalse(Party.objects.exists())
