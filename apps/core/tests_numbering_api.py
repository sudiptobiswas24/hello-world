"""
Document numbering through the API, kept by the controller. Every change
it refuses would put one number on two documents.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .models import DocumentSequence

URL = "/api/core/document-sequences/"


class NumberingApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.controller = self.as_("Controller")

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def sequence(self, **fields):
        return DocumentSequence.objects.create(code="sales.invoice", name="Invoices", prefix="INV-", **fields)

    def test_set_up_before_first_use_and_shown_what_comes_next(self):
        made = self.controller.post(URL, {"code": "sales.invoice", "name": "Invoices", "prefix": "DP/",
                                          "padding": 4, "include_year": False, "reset_yearly": False,
                                          "next_number": 1201}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["next_value"], "DP/1201")
        self.assertEqual(DocumentSequence.next_for("sales.invoice"), "DP/1201")

    def test_the_count_goes_up_and_never_back(self):
        sequence = self.sequence(reset_yearly=False, next_number=50)
        raised = self.controller.patch(f"{URL}{sequence.pk}/", {"next_number": 60}, format="json")
        self.assertEqual(raised.status_code, 200, raised.content)
        lowered = self.controller.patch(f"{URL}{sequence.pk}/", {"next_number": 10}, format="json")
        self.assertEqual(lowered.status_code, 400)
        self.assertIn("cannot go back", lowered.content.decode())

    def test_once_counted_by_year_the_sequence_stays_so(self):
        sequence = self.sequence()
        sequence.next_value(datetime.date(2026, 4, 1))
        for change, words in (({"next_number": 900}, "no longer read"),
                              ({"reset_yearly": False}, "counted by year"),
                              ({"code": "sales.bill"}, "fresh sequence"),
                              ({"include_year": False}, "show the year")):
            refused = self.controller.patch(f"{URL}{sequence.pk}/", change, format="json")
            self.assertEqual(refused.status_code, 400, change)
            self.assertIn(words, refused.content.decode())
        self.assertEqual(self.controller.delete(f"{URL}{sequence.pk}/").status_code, 405)
        self.assertEqual(sequence.next_value(datetime.date(2026, 4, 1)), "INV-2026-00002")

    def test_the_bookkeeper_reads_and_does_not_renumber(self):
        sequence = self.sequence()
        bookkeeper = self.as_("Bookkeeper")
        self.assertEqual(bookkeeper.patch(f"{URL}{sequence.pk}/", {"prefix": "X-"}, format="json").status_code, 403)
