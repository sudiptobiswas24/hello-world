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
