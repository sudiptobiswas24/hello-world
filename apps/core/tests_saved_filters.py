"""
A list kept under a name, each person's own.

  Asha keeps "Unpaid, Acme" on the invoices: the narrowing, not the page
  she was on. Ravi sees none of Asha's views, cannot remove them, and may
  keep his own under the same name. A second "Unpaid, Acme" of Asha's on
  the same list is refused in words, as is a nameless one, one kept for a
  place outside the application, and one that is not a narrowing.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .saved_filters import SavedFilter

SAVED = "/api/core/saved-filters/"
INVOICES = "/sales/invoices"


class SavedFilterTests(TestCase):
    def setUp(self):
        self.asha, self.ravi = User.objects.create_user("asha"), User.objects.create_user("ravi")
        self.as_asha, self.as_ravi = APIClient(), APIClient()
        self.as_asha.force_authenticate(self.asha)
        self.as_ravi.force_authenticate(self.ravi)

    def keep(self, client=None, **given):
        body = {"screen": INVOICES, "name": "Unpaid, Acme", "query": {"customer": "7", "open": "true", "page": "3"}, **given}
        return (client or self.as_asha).post(SAVED, body, format="json")

    def test_the_narrowing_is_kept_and_where_she_was_is_not(self):
        made = self.keep()
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(SavedFilter.objects.get().query, {"customer": "7", "open": "true"})
        rows = self.as_asha.get(SAVED, {"screen": INVOICES}).json()
        self.assertEqual([row["name"] for row in rows], ["Unpaid, Acme"])
        self.assertEqual(self.as_asha.get(SAVED, {"screen": "/sales/orders"}).json(), [])

    def test_each_persons_own(self):
        made = self.keep().json()
        self.assertEqual(self.as_ravi.get(SAVED, {"screen": INVOICES}).json(), [])
        self.assertEqual(self.as_ravi.delete(f"{SAVED}{made['id']}/").status_code, 404)
        self.assertEqual(self.keep(self.as_ravi).status_code, 201)
        self.assertEqual(self.as_asha.delete(f"{SAVED}{made['id']}/").status_code, 204)

    def test_what_is_refused_and_why(self):
        self.keep()
        twice = self.keep()
        self.assertEqual(twice.status_code, 400)
        self.assertIn("already", twice.json()["name"][0])
        for given, field in (({"name": "  "}, "name"), ({"screen": "https://elsewhere.example/"}, "screen"),
                             ({"name": "Other", "query": ["customer"]}, "query"),
                             ({"name": "Other", "query": {"customer": 7}}, "query")):
            with self.subTest(given=given):
                refused = self.keep(**given)
                self.assertEqual(refused.status_code, 400, refused.content)
                self.assertIn(field, refused.json())
        self.assertEqual(SavedFilter.objects.count(), 1)

    def test_nobody_signed_in_keeps_nothing(self):
        self.assertEqual(APIClient().post(SAVED, {"screen": INVOICES, "name": "x", "query": {}}, format="json").status_code,
                         403)
