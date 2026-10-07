"""
The import tool from the office: the controller lists the kinds, takes a
blank file, checks a file and keeps it; a bookkeeper is refused; a kind
that needs the go-live date says so.
"""

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import Party

from .tests import ImportTestCase

RECORDS = "/api/imports/records/"


class ImportApiTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_brings_records_in_from_the_screen(self):
        controller = self.as_("Controller")
        kinds = controller.get(RECORDS)
        self.assertEqual(kinds.status_code, 200, kinds.content)
        by_kind = {row["kind"]: row for row in kinds.json()}
        self.assertEqual(by_kind["opening_stock"]["needs"], ["date", "reason", "memo"])
        self.assertTrue(by_kind["bag_specs"]["columns"][:2] == ["code", "name"])
        blank = controller.get(f"{RECORDS}template/", {"kind": "parties"})
        self.assertEqual((blank.status_code, blank["Content-Type"]), (200, "text/csv; charset=utf-8"))
        self.assertTrue(blank.content.decode().startswith("code,name,roles,"))
        text = "code,name,roles,currency\nC-500,Shree Cement,customer,USD\n"
        checked = controller.post(f"{RECORDS}run/", {"kind": "parties", "text": text}, format="json")
        self.assertEqual(checked.status_code, 200, checked.content)
        self.assertEqual((checked.json()["created"], checked.json()["committed"], Party.objects.filter(code="C-500").exists()),
                         (1, False, False))
        kept = controller.post(f"{RECORDS}run/", {"kind": "parties", "text": text, "commit": True}, format="json")
        self.assertEqual((kept.json()["committed"], Party.objects.filter(code="C-500").exists()), (True, True))
        self.assertEqual(Party.objects.get(code="C-500").created_by.username, "controller")
        bad = controller.post(f"{RECORDS}run/", {"kind": "parties", "text": text, "commit": True}, format="json")
        self.assertEqual(bad.json()["errors"], [[2, "code", "'C-500' already exists."]])

    def test_what_a_kind_needs_is_asked_for(self):
        controller = self.as_("Controller")
        stock = controller.post(f"{RECORDS}run/", {"kind": "opening_stock", "text": "sku\n"}, format="json")
        self.assertEqual((stock.status_code, list(stock.json())), (400, ["date"]))
        bills = controller.post(f"{RECORDS}run/", {"kind": "open_bills", "text": "vendor\n"}, format="json")
        self.assertEqual((bills.status_code, list(bills.json())), (400, ["against"]))
        self.assertEqual(controller.post(f"{RECORDS}run/", {"kind": "nothing", "text": "x\n"}, format="json").status_code, 400)
        self.assertEqual(controller.post(f"{RECORDS}run/", {"kind": "parties", "text": " "}, format="json").status_code, 400)

    def test_who_may(self):
        self.assertEqual(self.as_("Bookkeeper").get(RECORDS).status_code, 403)
        self.assertEqual(self.as_("HR Admin").post(f"{RECORDS}run/", {"kind": "parties", "text": "code\n"},
                                                  format="json").status_code, 403)
