"""
Cost centres: a second dimension on the ledger line, stamped when the
line posts, read by who incurred a cost, footing to the profit and loss
account; what has no centre is unallocated, not missing.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .analytic import CostCentre, costs_by_centre
from .models import Account, AccountType, JournalEntry, JournalLine


class CostCentreTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.bank = Account.objects.create(code="1010", name="Bank", account_type=AccountType.ASSET, holds_money=True)
        self.power = Account.objects.create(code="5300", name="Power", account_type=AccountType.EXPENSE)
        self.repairs = Account.objects.create(code="5400", name="Repairs", account_type=AccountType.EXPENSE)
        self.loom = CostCentre.objects.create(code="LOOM", name="Loom shed")
        self.print = CostCentre.objects.create(code="PRINT", name="Printing line")

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def spend(self, account, amount, centre=None, day=datetime.date(2026, 4, 10)):
        entry = JournalEntry.objects.create(date=day, memo="spent")
        JournalLine.objects.create(entry=entry, account=account, debit=Decimal(amount), cost_centre=centre)
        JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal(amount))
        entry.post()
        return entry


class ReportTests(CostCentreTestCase):
    def test_expenses_read_by_centre_and_foot_to_the_profit_and_loss(self):
        self.spend(self.power, "1200.00", self.loom)
        self.spend(self.power, "300.00", self.print)
        self.spend(self.repairs, "450.50", self.loom)
        self.spend(self.repairs, "99.99")  # nobody's: unallocated, shown as such
        self.spend(self.power, "5000.00", self.loom, day=datetime.date(2026, 3, 31))  # before the window
        report = costs_by_centre("2026-04-01", "2026-04-30")
        self.assertEqual([(row["code"], row["name"], row["amount"]) for row in report["rows"]],
                         [("LOOM", "Loom shed", Decimal("1650.50")), ("PRINT", "Printing line", Decimal("300.00")),
                          ("", "Unallocated", Decimal("99.99"))])
        self.assertEqual((report["total"], report["statement_expenses"], report["foots"]),
                         (Decimal("2050.49"), Decimal("2050.49"), True))

    def test_a_reversal_carries_the_centre_so_the_report_nets_to_nothing(self):
        entry = self.spend(self.power, "1200.00", self.loom)
        entry.create_reversal(entry_date=datetime.date(2026, 4, 11))
        report = costs_by_centre("2026-04-01", "2026-04-30")
        self.assertEqual([(row["code"], row["amount"]) for row in report["rows"]], [("LOOM", Decimal("0.00"))])
        self.assertTrue(report["foots"])


class ApiTests(CostCentreTestCase):
    def test_the_bookkeeper_keeps_the_centres_and_a_hand_journal_names_one(self):
        books = self.as_("Bookkeeper")
        made = books.post("/api/accounting/cost-centres/", {"code": "OFFICE", "name": "Office"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        entry = books.post("/api/accounting/journal-entries/", {"date": "2026-04-12", "memo": "stationery"}, format="json").json()
        line = books.post("/api/accounting/journal-lines/", {"entry": entry["id"], "account": self.repairs.pk, "debit": "80.00",
                                                             "cost_centre": made.json()["id"]}, format="json")
        self.assertEqual((line.status_code, line.json()["cost_centre_name"]), (201, "Office"), line.content)
        books.post("/api/accounting/journal-lines/", {"entry": entry["id"], "account": self.bank.pk, "credit": "80.00"}, format="json")
        # The bookkeeper keys the journal in; posting it is the controller's.
        self.assertEqual(books.post(f"/api/accounting/journal-entries/{entry['id']}/post_entry/").status_code, 403)
        self.assertEqual(self.as_("Controller").post(f"/api/accounting/journal-entries/{entry['id']}/post_entry/").status_code, 200)
        report = books.get("/api/accounting/cost-centres/report/", {"from": "2026-04-01", "to": "2026-04-30"}).json()
        self.assertEqual([(row["code"], row["amount"]) for row in report["rows"]], [("OFFICE", "80.00")])
        self.assertTrue(report["foots"])

    def test_who_may(self):
        self.assertEqual(self.as_("Purchasing Clerk").get("/api/accounting/cost-centres/").status_code, 200)
        self.assertEqual(self.as_("Purchasing Clerk").post("/api/accounting/cost-centres/", {"code": "X", "name": "X"},
                                                            format="json").status_code, 403)
        self.assertEqual(self.as_("Purchasing Clerk").get("/api/accounting/cost-centres/report/").status_code, 403)
        self.assertEqual(self.as_("Controller").get("/api/accounting/cost-centres/report/").status_code, 200)
