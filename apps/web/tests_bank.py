"""
The statement as at today: raw material of 500 widgets at 4.00 (the
fixture's) and 250 at 4.20 (brought in), 3,050.00, and no work in
progress; one bill of 800 owing; a 20,000 invoice dated 92 days ago
(Net 30, so 62 days overdue, within ninety) and a 5,000 one dated 153
days ago (123 days, beyond). Paid stock 3,050 - 800 = 2,250.00, less
the bank's 25% is 1,687.50; debtors within ninety 20,000 less 40% is
12,000.00; drawing power 13,687.50. At 100% margins it is nothing.

Dated from today because stock is valued by when it moved: a movement
made now is not on the shelf at a date before now, however it is dated.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Party, PartyRole, PartyRoleAssignment
from apps.imports.importer import run
from apps.imports.tests import GO_LIVE, ImportTestCase
from apps.inventory.models import AdjustmentReason

from .bank import stock_statement


class BankStatementTestCase(ImportTestCase):
    def setUp(self):
        super().setUp()
        AdjustmentReason.objects.create(code="OPENING", name="Opening stock", account=self.opening)
        self.item.stock_class = "raw_material"
        self.item.save()
        vendor = Party.objects.create(code="V-9", name="Granules", default_currency=self.usd)
        PartyRoleAssignment.objects.create(party=vendor, role=PartyRole.VENDOR)
        self.today = timezone.localdate()
        ago = lambda days: self.today - datetime.timedelta(days=days)  # noqa: E731
        for kind, text in (
            ("opening_stock", "sku,warehouse,quantity,unit_cost\nWDG-1,WH1,250,4.2\n"),
            ("open_invoices", "customer,reference,date,amount\n"
                              f"C-1,OLD/1,{ago(92)},20000\nC-1,OLD/2,{ago(153)},5000\n"),
            ("open_bills", f"vendor,reference,date,amount\nV-9,GH-1,{ago(29)},800\n"),
        ):
            report = run(kind, text, commit=True, date=GO_LIVE, reason="OPENING", against="3900")
            self.assertTrue(report.committed, report.errors)


class StatementTests(BankStatementTestCase):
    def test_stock_by_class_creditors_debtors_and_the_power_the_margins_leave(self):
        found = stock_statement(as_of=self.today)
        self.assertEqual({row["label"]: row["value"] for row in found["stock"]}["Raw material"], Decimal("3050.00"))
        self.assertEqual({row["label"]: row["value"] for row in found["stock"]}["Unclassified"], Decimal("0.00"))
        self.assertEqual((found["stock_total"], found["work_in_progress"], found["creditors"], found["paid_stock"]),
                         (Decimal("3050.00"), Decimal("0.00"), Decimal("800.00"), Decimal("2250.00")))
        self.assertEqual((found["debtors_within_90"], found["debtors_beyond_90"], found["debtors_total"]),
                         (Decimal("20000.00"), Decimal("5000.00"), Decimal("25000.00")))
        self.assertEqual((found["paid_stock_after_margin"], found["debtors_after_margin"], found["drawing_power"]),
                         (Decimal("1687.50"), Decimal("12000.00"), Decimal("13687.50")))

    def test_creditors_eat_into_paid_stock_and_full_margins_leave_nothing(self):
        report = run("open_bills", f"vendor,reference,date,amount\nV-9,GH-2,{self.today},900\n", commit=True,
                     against="3900")
        self.assertTrue(report.committed, report.errors)
        found = stock_statement(as_of=self.today)
        # 3,050 - 1,700 = 1,350.00, less 25% is 1,012.50, with the 12,000.00 of debtors.
        self.assertEqual((found["creditors"], found["paid_stock"], found["drawing_power"]),
                         (Decimal("1700.00"), Decimal("1350.00"), Decimal("13012.50")))
        # More owed than held leaves no paid stock at all, not a negative one.
        report = run("open_bills", f"vendor,reference,date,amount\nV-9,GH-3,{self.today},2000\n", commit=True,
                     against="3900")
        self.assertTrue(report.committed, report.errors)
        owed_more = stock_statement(as_of=self.today)
        self.assertEqual((owed_more["paid_stock"], owed_more["drawing_power"]), (Decimal("0.00"), Decimal("12000.00")))
        nothing = stock_statement(as_of=self.today, stock_margin=Decimal("100"),
                                  debtor_margin=Decimal("100"))
        self.assertEqual(nothing["drawing_power"], Decimal("0.00"))

    def test_before_the_invoices_fell_due_every_debtor_is_within_ninety(self):
        # Eighty days ago the first was not yet due and the second was 43 days over.
        found = stock_statement(as_of=self.today - datetime.timedelta(days=80))
        self.assertEqual((found["debtors_within_90"], found["debtors_beyond_90"]), (Decimal("25000.00"), Decimal("0.00")))


class StatementApiTests(BankStatementTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)

    def as_(self, role):
        user = User.objects.create_user(role.replace(" ", "_").lower())
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_the_controller_reads_it_as_exact_figures_and_the_stores_do_not(self):
        controller = self.as_("Controller")
        got = controller.get("/api/web/bank-stock-statement/", {"as_of": str(self.today)})
        self.assertEqual(got.status_code, 200, got.content)
        self.assertEqual((got.json()["drawing_power"], got.json()["stock_margin_percent"]), ("13687.50", "25"))
        halved = controller.get("/api/web/bank-stock-statement/",
                                {"as_of": str(self.today), "stock_margin": "50", "debtor_margin": "50"}).json()
        # 2,250 and 20,000 at half each: 1,125.00 and 10,000.00.
        self.assertEqual(halved["drawing_power"], "11125.00")
        self.assertEqual(controller.get("/api/web/bank-stock-statement/", {"stock_margin": "abc"}).status_code, 400)
        self.assertEqual(controller.get("/api/web/bank-stock-statement/", {"stock_margin": "120"}).status_code, 400)
        self.assertEqual(self.as_("Warehouse Staff").get("/api/web/bank-stock-statement/").status_code, 403)
