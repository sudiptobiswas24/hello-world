"""
The consent to operate runs 1 April 2025 to 31 March 2026, renewed from
60 days before: due from 30 January, lapsed on 1 April unless the renewal
is recorded. Renewed, it leaves the calendar; the renewal taken off again,
it comes back.
"""

import datetime

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .licences import Licence, LicenceKind, licences_due

D = datetime.date


def consent(**extra):
    values = dict(kind=LicenceKind.CONSENT, licence_number="CTO/2025/118", issued_by="State Pollution Control Board",
                  valid_from=D(2025, 4, 1), valid_to=D(2026, 3, 31))
    values.update(extra)
    return Licence.objects.create(**values)


class RefusedTests(TestCase):
    def test_the_same_issue_entered_twice(self):
        consent()
        with self.assertRaises(IntegrityError), transaction.atomic():
            consent()
        # The factory licence keeps its number: the next year's issue is its own record.
        self.assertEqual(consent(valid_from=D(2026, 4, 1), valid_to=D(2027, 3, 31)).licence_number, "CTO/2025/118")

    def test_not_ending_before_it_starts(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            consent(valid_to=D(2025, 3, 31))

    def test_a_renewal_starts_after_the_licence_did(self):
        licence = consent()
        with self.assertRaisesMessage(ValidationError, "starts after 2025-04-01"):
            licence.renew("CTO/2026/044", "2025-04-01", "2027-03-31")
        self.assertIsNone(Licence.objects.get(pk=licence.pk).renewed_by)

    def test_renewed_once(self):
        licence = consent()
        renewal = licence.renew("CTO/2026/044", "2026-04-01", "2027-03-31")
        with self.assertRaisesMessage(ValidationError, "renew that one"):
            licence.renew("CTO/2026/045", "2026-04-01", "2027-03-31")
        self.assertEqual(Licence.objects.count(), 2)
        self.assertEqual(Licence.objects.get(pk=licence.pk).renewed_by, renewal)


class OnTheCalendarTests(TestCase):
    def test_due_from_its_reminder_and_lapsed_after_its_end(self):
        licence = consent()
        self.assertEqual(licence.renew_from(), D(2026, 1, 30))
        self.assertEqual((licence.status(D(2026, 1, 29)), licence.status(D(2026, 1, 30)),
                          licence.status(D(2026, 3, 31)), licence.status(D(2026, 4, 1))),
                         ("valid", "due", "due", "lapsed"))
        self.assertEqual(licences_due(D(2026, 1, 29)), [])
        self.assertEqual(licences_due(D(2026, 1, 30)), [licence])

    def test_renewed_it_leaves_and_the_renewal_carries_what_it_covers(self):
        licence = consent(covers="Unit 2", remind_days=90)
        renewal = licence.renew("CTO/2026/044", "2026-04-01", "2027-03-31")
        self.assertEqual((renewal.kind, renewal.covers, renewal.issued_by, renewal.remind_days),
                         (LicenceKind.CONSENT, "Unit 2", "State Pollution Control Board", 90))
        licence.refresh_from_db()
        self.assertEqual(licence.status(D(2026, 4, 1)), "renewed")
        self.assertEqual(licences_due(D(2026, 4, 1)), [])

    def test_the_renewal_taken_off_puts_it_back(self):
        licence = consent()
        licence.renew("CTO/2026/044", "2026-04-01", "2027-03-31").delete()
        licence.refresh_from_db()
        self.assertEqual(licence.status(D(2026, 4, 1)), "lapsed")
        self.assertEqual(licences_due(D(2026, 4, 1)), [licence])


class LicenceApiTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_kept_renewed_and_listed_when_due(self):
        today = timezone.localdate()
        hr = self.as_("HR Admin")
        made = hr.post("/api/core/licences/", {
            "kind": "fire", "licence_number": "FIRE/NOC/77", "issued_by": "Fire and Emergency Services",
            "valid_from": str(today - datetime.timedelta(days=300)),
            "valid_to": str(today + datetime.timedelta(days=30))}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["status"], "due")
        hr.post("/api/core/licences/", {
            "kind": "factory", "licence_number": "FL/2026/9", "valid_from": str(today),
            "valid_to": str(today + datetime.timedelta(days=365))}, format="json")
        self.assertEqual([row["licence_number"] for row in hr.get("/api/core/licences/due/").json()], ["FIRE/NOC/77"])

        url = f"/api/core/licences/{made.json()['id']}/renew/"
        self.assertEqual(hr.post(url, {"licence_number": "FIRE/NOC/91"}, format="json").status_code, 400)
        renewed = hr.post(url, {"licence_number": "FIRE/NOC/91", "valid_from": str(today + datetime.timedelta(days=31)),
                                "valid_to": str(today + datetime.timedelta(days=396))}, format="json")
        self.assertEqual(renewed.status_code, 201, renewed.content)
        self.assertEqual((renewed.json()["kind"], renewed.json()["renews"]), ("fire", made.json()["id"]))
        self.assertEqual(hr.get("/api/core/licences/due/").json(), [])
        self.assertEqual(self.as_("Controller").get("/api/core/licences/").status_code, 200)

    def test_not_the_stores(self):
        stores = self.as_("Warehouse Staff")
        self.assertEqual(stores.get("/api/core/licences/").status_code, 403)
        self.assertEqual(stores.get("/api/core/licences/due/").status_code, 403)
