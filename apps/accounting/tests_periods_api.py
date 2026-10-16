"""
Closing April 2026 from the office: the controller closes it with a
note, nothing posts into it, its dates cannot move and it cannot be
deleted while closed; reopened, it can. The bookkeeper sees the periods
and closes none.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import AccountingPeriod, JournalEntry, JournalLine
from .tests import AccountingTestCase

PERIODS = "/api/accounting/periods/"


class PeriodsApiTests(AccountingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def entry(self, day):
        entry = JournalEntry.objects.create(date=day, memo="late")
        JournalLine.objects.create(entry=entry, account=self.cash, debit=Decimal("10"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("10"))
        return entry

    def test_closed_from_the_office_and_reopened_there(self):
        controller, books = self.as_("Controller"), self.as_("Bookkeeper")
        made = controller.post(PERIODS, {"name": "Apr 2026", "start_date": "2026-04-01", "end_date": "2026-04-30"},
                               format="json")
        self.assertEqual(made.status_code, 201, made.content)
        pk = made.json()["id"]
        self.assertEqual(books.get(PERIODS).json()[0]["name"], "Apr 2026")
        self.assertEqual(books.post(f"{PERIODS}{pk}/close/", {}, format="json").status_code, 403)

        closed = controller.post(f"{PERIODS}{pk}/close/", {"note": "Signed off by the auditors"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual((closed.json()["closed"], closed.json()["closed_by_name"], closed.json()["note"]),
                         (True, "Controller", "Signed off by the auditors"))
        self.assertEqual(controller.post(f"/api/accounting/journal-entries/{self.entry(datetime.date(2026, 4, 15)).pk}/post_entry/",
                                         {}, format="json").status_code, 400)
        self.assertEqual(controller.post(f"/api/accounting/journal-entries/{self.entry(datetime.date(2026, 5, 1)).pk}/post_entry/",
                                         {}, format="json").status_code, 200)
        moved = controller.patch(f"{PERIODS}{pk}/", {"end_date": "2026-05-31"}, format="json")
        self.assertEqual(moved.status_code, 400, moved.content)
        self.assertEqual(controller.delete(f"{PERIODS}{pk}/").status_code, 400)
        self.assertTrue(AccountingPeriod.objects.filter(pk=pk, closed=True).exists())

        reopened = controller.post(f"{PERIODS}{pk}/reopen/", {"note": "A late invoice to post"}, format="json")
        self.assertEqual((reopened.status_code, reopened.json()["closed"]), (200, False))
        self.assertEqual(controller.delete(f"{PERIODS}{pk}/").status_code, 204)

    def test_who_may(self):
        self.assertEqual(self.as_("Purchasing Clerk").get(PERIODS).status_code, 403)
        self.assertEqual(self.as_("Bookkeeper").post(PERIODS, {"name": "X", "start_date": "2026-04-01",
                                                                "end_date": "2026-04-30"}, format="json").status_code, 403)
