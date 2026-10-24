"""
The coating blend from the screen, as the process engineer: the first
polymer added laminates the sack and the last one taken off leaves it
plain. Separately, each refused the other, so a laminated sack could not
be made from the office at all.

Quantities are the blend's established figures (tests_coating): 15 GSM
on this sack is 18.9 g of coating, split 15.12 and 3.78 at 80:20.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from .tests_coating import CoatingTestCase
from .tests_woven import close
from .woven import BagSpecification


class CoatingApiTests(CoatingTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("engineer")
        user.groups.add(Group.objects.get(name="Process Engineer"))
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_laminated_by_its_blend_and_plain_without(self):
        sack = self.bag()
        self.assertFalse(sack.is_laminated)
        url = f"/api/manufacturing/bag-specifications/{sack.pk}/coating/"

        weightless = self.client.post(url, {"item": self.lam_pp.pk, "parts": "80"}, format="json")
        self.assertEqual(weightless.status_code, 400)
        self.assertIn("how heavy the coating is", weightless.content.decode())
        first = self.client.post(url, {"item": self.lam_pp.pk, "parts": "80", "lamination_gsm": "15"},
                                 format="json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(first.json()["is_laminated"])
        self.assertEqual(first.json()["coating"][0]["item_label"], "LAM-PP · LAM-PP")
        self.assertTrue(close(self.rows(BagSpecification.objects.get(pk=sack.pk))["LAM-PP"], "18.9"))

        self.client.post(url, {"item": self.ldpe.pk, "parts": "20"}, format="json")
        rows = self.rows(BagSpecification.objects.get(pk=sack.pk))
        self.assertTrue(close(rows["LAM-PP"], "15.12"))
        self.assertTrue(close(rows["LDPE-1070"], "3.78"))
        twice = self.client.post(url, {"item": self.ldpe.pk, "parts": "5"}, format="json")
        self.assertEqual(twice.status_code, 400)
        self.assertIn("in the coating already", twice.content.decode())

        self.client.delete(f"{url}?item={self.ldpe.pk}")
        self.client.delete(f"{url}?item={self.lam_pp.pk}")
        sack = BagSpecification.objects.get(pk=sack.pk)
        self.assertEqual((sack.is_laminated, sack.lamination_gsm), (False, Decimal("0")))
        self.assertEqual(sack.coating_lines.count(), 0)
        self.assertNotIn("LAM-PP", self.rows(sack))
        absent = self.client.delete(f"{url}?item={self.lam_pp.pk}")
        self.assertEqual(absent.status_code, 400)

    def test_a_share_is_more_than_nothing(self):
        sack = self.bag()
        refused = self.client.post(f"/api/manufacturing/bag-specifications/{sack.pk}/coating/",
                                   {"item": self.lam_pp.pk, "parts": "0", "lamination_gsm": "15"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertFalse(BagSpecification.objects.get(pk=sack.pk).is_laminated)
