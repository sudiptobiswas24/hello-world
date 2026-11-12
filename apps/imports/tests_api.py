"""
The import tool from the office: the controller lists the kinds, takes a
blank file, checks a file and keeps it; a bookkeeper is refused; the
opening position follows the books' setting and says so.
"""

from decimal import Decimal

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

    def test_the_day_and_the_account_are_the_books_setting_not_asked_of_the_person(self):
        """O531, as the controller: no date and no account typed; the setting decides, and says so when unset."""
        controller = self.as_("Controller")
        files = (("opening_balances", "account,debit,credit\n1010,5,\n3900,,5\n", 0),
                 ("fixed_assets", "category,name,cost,acquisition_date\nNONE,Loom,100,2020-04-01\n", 2),
                 ("open_invoices", "customer,reference,date,amount\nC-1,OLD/0412,2026-08-20,11800\n", 2))
        for kind, text, row in files:
            with self.subTest(kind=kind, books="unset"):
                unset = controller.post(f"{RECORDS}run/", {"kind": kind, "text": text}, format="json")
                self.assertEqual(unset.status_code, 200, unset.content)
                self.assertEqual([error[:2] for error in unset.json()["errors"]], [[row, "--date"]])
                self.assertIn("et the day the books opened (Settings, Books opening) first", unset.json()["errors"][0][2])
        self.open_books()
        for kind, text, _row in (files[0], files[2]):
            with self.subTest(kind=kind, books="open"):
                checked = controller.post(f"{RECORDS}run/", {"kind": kind, "text": text}, format="json")
                self.assertEqual((checked.status_code, checked.json()["errors"], checked.json()["committed"]),
                                 (200, [], False), checked.content)
        bad = controller.post(f"{RECORDS}run/", {"kind": "opening_stock", "text": "sku\n", "date": "10 Oct"}, format="json")
        # Beside the date box, as a form reads it (O543): it was a bare list.
        self.assertEqual((bad.status_code, bad.json()),
                         (400, {"date": ["'10 Oct' is not a date. Write it as 2026-03-31."]}))
        self.assertEqual(controller.post(f"{RECORDS}run/", {"kind": "nothing", "text": "x\n"}, format="json").status_code, 400)
        self.assertEqual(controller.post(f"{RECORDS}run/", {"kind": "parties", "text": " "}, format="json").status_code, 400)

    def test_stock_from_the_screen_keeps_to_the_day_the_books_opened(self):
        """O529, as the controller: the 10th is refused in words, the day the books opened is kept."""
        from apps.inventory.adjustments import AdjustmentReason, StockAdjustment

        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        controller = self.as_("Controller")
        file = {"kind": "opening_stock", "text": "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n",
                "commit": True, "reason": "OPENING"}
        unset = controller.post(f"{RECORDS}run/", {**file, "date": "2026-10-01"}, format="json")
        self.assertEqual((unset.status_code, unset.json()["committed"]), (200, False), unset.content)
        self.assertEqual(unset.json()["errors"], [[0, "--date", "Set the day the books opened (Settings, Books "
                                                   "opening) first: opening stock is what was counted on it."]])
        self.open_books()
        late = controller.post(f"{RECORDS}run/", {**file, "date": "2026-10-10"}, format="json")
        self.assertEqual((late.status_code, late.json()["committed"]), (200, False), late.content)
        self.assertEqual(late.json()["errors"], [[0, "--date", "2026-10-10 is not the day the books opened "
                                                  "(2026-10-01); the books setting says when, and the import follows it."]])
        self.assertFalse(StockAdjustment.objects.exists())
        kept = controller.post(f"{RECORDS}run/", {**file, "date": "2026-10-01"}, format="json")
        self.assertEqual((kept.json()["committed"], kept.json()["errors"]), (True, []))
        self.assertEqual(str(StockAdjustment.objects.get().adjustment_date), "2026-10-01")

    def test_an_open_bill_from_the_screen_stands_against_the_books_opening_account(self):
        """O530, as the controller: another account is refused in words; the set one is kept."""
        from apps.accounting.models import Account, AccountType
        from apps.core.models import PartyRole, PartyRoleAssignment
        from apps.purchasing.models import Bill

        Account.objects.create(code="3950", name="Share capital", account_type=AccountType.EQUITY)
        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        self.open_books()
        controller = self.as_("Controller")
        file = {"kind": "open_bills", "text": "vendor,reference,date,amount\nV-9,GH-77,2026-09-02,5000\n",
                "commit": True}
        other = controller.post(f"{RECORDS}run/", {**file, "against": "3950"}, format="json")
        self.assertEqual((other.status_code, other.json()["committed"]), (200, False), other.content)
        self.assertEqual(other.json()["errors"], [[
            2, "--against", "3950 is not the opening-balance account (3900) the books opened against "
            "(Settings, Books opening): leave --against out, or give that one."]])
        self.assertFalse(Bill.objects.exists())
        kept = controller.post(f"{RECORDS}run/", {**file, "against": "3900"}, format="json")
        self.assertEqual((kept.json()["committed"], kept.json()["errors"]), (True, []))
        self.assertEqual((self.balance(self.payable), self.balance(self.opening)),
                         (Decimal("-5000.00"), Decimal("5000.00")))

    def test_an_invoice_raised_after_the_books_opened_is_refused_from_the_screen(self):
        """O541, as the controller: the 20th is refused naming both days; the opening day itself is kept."""
        from apps.sales.models import Invoice

        self.open_books()
        controller = self.as_("Controller")
        file = {"kind": "open_invoices", "commit": True}
        late = controller.post(f"{RECORDS}run/", {
            **file, "text": "customer,reference,date,amount\nC-1,NEW/0001,2026-10-20,590\n"}, format="json")
        self.assertEqual((late.status_code, late.json()["committed"]), (200, False), late.content)
        self.assertEqual(late.json()["errors"], [[
            2, "date", "2026-10-20 is after the day the books opened (2026-10-01): an invoice raised since is a "
            "sale in the new books, not part of the opening position. Enter it as an invoice (Sales, Invoices), "
            "or correct the date."]])
        self.assertFalse(Invoice.objects.exists())
        kept = controller.post(f"{RECORDS}run/", {
            **file, "text": "customer,reference,date,amount\nC-1,OLD/0500,2026-10-01,2360\n"}, format="json")
        self.assertEqual((kept.json()["committed"], kept.json()["errors"]), (True, []), kept.content)
        self.assertEqual((self.balance(self.ar), self.balance(self.opening)),
                         (Decimal("2360.00"), Decimal("-2360.00")))

    def test_who_may(self):
        self.assertEqual(self.as_("Bookkeeper").get(RECORDS).status_code, 403)
        self.assertEqual(self.as_("HR Admin").post(f"{RECORDS}run/", {"kind": "parties", "text": "code\n"},
                                                  format="json").status_code, 403)


class TheImportGivesNoRolesTests(ImportTestCase):
    """O86, through the screen: a Controller holding only the import right gave their own login HR Admin."""

    def test_the_controller_gives_themselves_nothing_through_the_import(self):
        call_command("setup_roles", verbosity=0)
        controller = User.objects.create_user("ctl")
        controller.groups.add(Group.objects.get(name="Controller"))
        client = APIClient()
        client.force_authenticate(controller)
        text = "employee_number,name,hire_date,username,roles\nE-9,Controller,2024-04-01,ctl,HR Admin\n"
        response = client.post(f"{RECORDS}run/", {"kind": "employees", "text": text, "commit": True}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["committed"], [row[:2] for row in response.json()["errors"]]),
                         (False, [[2, "roles"]]))
        self.assertEqual(list(controller.groups.values_list("name", flat=True)), ["Controller"])
