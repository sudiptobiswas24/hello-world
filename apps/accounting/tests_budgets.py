"""
A quarter's budget read against the books on 30 April, thirty of its
ninety-one days gone:

  line                 budget      to date    posted (Apr)        used
  Power (whole)        9,100.00   3,000.00   1,200 LOOM + 300 PRINT = 1,500.00   16.5%
  Repairs / LOOM       3,000.00     989.01   450.50                               15.0%
  Sales (income)      50,000.00  16,483.52   nothing yet (20,000 on 5 May)         0.0%
  Repairs, other       —           —         99.99 under no centre: shown, not lost
  Freight              —           —         250.00, nothing budgeted: unbudgeted

A draft of 5,000 and 777 posted in March count for nothing.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .analytic import CostCentre
from .budgets import Budget, BudgetLine, budget_report
from .models import Account, AccountType, JournalEntry, JournalLine


class BudgetTestCase(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)  # noqa: E731
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        self.sales = acc("4000", "Sales", AccountType.INCOME)
        self.power = acc("5300", "Power", AccountType.EXPENSE)
        self.repairs = acc("5400", "Repairs", AccountType.EXPENSE)
        self.freight = acc("5500", "Freight", AccountType.EXPENSE)
        self.loom = CostCentre.objects.create(code="LOOM", name="Loom shed")
        self.print = CostCentre.objects.create(code="PRINT", name="Printing line")
        self.budget = Budget.objects.create(code="Q1FY27", name="April to June 2026",
                                            start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2026, 6, 30))
        BudgetLine.objects.create(budget=self.budget, account=self.power, amount=Decimal("9100"))
        BudgetLine.objects.create(budget=self.budget, account=self.repairs, cost_centre=self.loom, amount=Decimal("3000"))
        BudgetLine.objects.create(budget=self.budget, account=self.sales, amount=Decimal("50000"))

    def book(self, account, amount, day, centre=None, income=False, post=True):
        entry = JournalEntry.objects.create(date=day, memo="booked")
        if income:
            JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal(amount))
            JournalLine.objects.create(entry=entry, account=account, credit=Decimal(amount), cost_centre=centre)
        else:
            JournalLine.objects.create(entry=entry, account=account, debit=Decimal(amount), cost_centre=centre)
            JournalLine.objects.create(entry=entry, account=self.bank, credit=Decimal(amount))
        if post:
            entry.post()
        return entry

    def as_(self, role):
        user, _ = User.objects.get_or_create(username=role.replace(" ", "_"))
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client


class ReportTests(BudgetTestCase):
    def setUp(self):
        super().setUp()
        self.book(self.power, "1200", datetime.date(2026, 4, 10), self.loom)
        self.book(self.power, "300", datetime.date(2026, 4, 12), self.print)
        self.book(self.repairs, "450.50", datetime.date(2026, 4, 15), self.loom)
        self.book(self.repairs, "99.99", datetime.date(2026, 4, 20))
        self.book(self.freight, "250", datetime.date(2026, 4, 18))
        self.book(self.sales, "20000", datetime.date(2026, 5, 5), income=True)
        self.book(self.power, "5000", datetime.date(2026, 4, 25), post=False)
        self.book(self.power, "777", datetime.date(2026, 3, 31))

    def figures(self, row):
        return (row["account_code"], row["centre_code"], row["amount"], row["to_date"], row["actual"],
                row["remaining"], row["used_percent"], row["over"], row["unbudgeted"])

    def test_each_line_against_the_books_thirty_days_in(self):
        report = budget_report(self.budget, datetime.date(2026, 4, 30))
        self.assertEqual((report["days"], report["elapsed"], report["as_of"]), (91, 30, datetime.date(2026, 4, 30)))
        self.assertEqual([self.figures(row) for row in report["rows"]], [
            ("4000", "", Decimal("50000.00"), Decimal("16483.52"), Decimal("0.00"), Decimal("50000.00"), Decimal("0.0"), False, False),
            ("5300", "", Decimal("9100.00"), Decimal("3000.00"), Decimal("1500.00"), Decimal("7600.00"), Decimal("16.5"), False, False),
            ("5400", "LOOM", Decimal("3000.00"), Decimal("989.01"), Decimal("450.50"), Decimal("2549.50"), Decimal("15.0"), False, False),
            ("5400", "", Decimal("0.00"), Decimal("0.00"), Decimal("99.99"), Decimal("-99.99"), None, True, True),
            ("5500", "", Decimal("0.00"), Decimal("0.00"), Decimal("250.00"), Decimal("-250.00"), None, True, True),
        ])
        self.assertEqual(report["rows"][3]["centre_name"], "Other centres or none")
        self.assertEqual(report["totals"]["expense"], {
            "amount": Decimal("12100.00"), "to_date": Decimal("3989.01"), "actual": Decimal("1950.50"),
            "unbudgeted": Decimal("349.99")})
        self.assertEqual(report["totals"]["income"], {
            "amount": Decimal("50000.00"), "to_date": Decimal("16483.52"), "actual": Decimal("0.00"),
            "unbudgeted": Decimal("0.00")})

    def test_the_whole_span_and_before_it_starts(self):
        whole = budget_report(self.budget, datetime.date(2026, 6, 30))
        sales = whole["rows"][0]
        self.assertEqual((whole["elapsed"], sales["to_date"], sales["actual"], sales["used_percent"], sales["remaining"]),
                         (91, Decimal("50000.00"), Decimal("20000.00"), Decimal("40.0"), Decimal("30000.00")))
        # Asked past the end, the report stops at the end.
        self.assertEqual(budget_report(self.budget, datetime.date(2026, 9, 1))["as_of"], datetime.date(2026, 6, 30))
        before = budget_report(self.budget, datetime.date(2026, 3, 31))
        self.assertEqual((before["elapsed"], [(row["to_date"], row["actual"]) for row in before["rows"]]),
                         (0, [(Decimal("0.00"), Decimal("0.00"))] * 3))


class GuardTests(BudgetTestCase):
    def test_only_income_and_expense_accounts_are_budgeted(self):
        with self.assertRaisesMessage(ValidationError, "income and expense accounts"):
            BudgetLine.objects.create(budget=self.budget, account=self.bank, amount=Decimal("1"))

    def test_a_budget_cannot_end_before_it_starts(self):
        with self.assertRaisesMessage(ValidationError, "cannot end before it starts"):
            Budget.objects.create(code="X", name="Back to front", start_date=datetime.date(2026, 4, 1),
                                  end_date=datetime.date(2026, 3, 31))


class ApiTests(BudgetTestCase):
    def test_the_controller_sets_a_budget_and_reads_it_against_the_books(self):
        controller = self.as_("Controller")
        made = controller.post("/api/accounting/budgets/", {
            "code": "FY27", "name": "The year", "start_date": "2026-04-01", "end_date": "2027-03-31"}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        line = controller.post("/api/accounting/budget-lines/", {
            "budget": made.json()["id"], "account": self.power.pk, "amount": "36500.00"}, format="json")
        self.assertEqual(line.status_code, 201, line.content)
        self.assertEqual(line.json()["account_name"], "Power")
        # The same account twice, uncentred, is one line too many.
        again = controller.post("/api/accounting/budget-lines/", {
            "budget": made.json()["id"], "account": self.power.pk, "amount": "1.00"}, format="json")
        self.assertEqual(again.status_code, 400, again.content)
        self.book(self.power, "1200", datetime.date(2026, 4, 10), self.loom)
        report = controller.get(f"/api/accounting/budgets/{made.json()['id']}/report/", {"as_of": "2026-04-10"}).json()
        # Ten days of 365: 36,500 × 10 / 365 = 1,000.00; spent 1,200.
        row = report["rows"][0]
        self.assertEqual((row["amount"], row["to_date"], row["actual"], row["used_percent"], row["over"], row["ahead"]),
                         ("36500.00", "1000.00", "1200.00", "3.3", False, True))

    def test_who_may(self):
        books = self.as_("Bookkeeper")
        self.assertEqual(books.get("/api/accounting/budgets/").status_code, 200)
        self.assertEqual(books.get(f"/api/accounting/budgets/{self.budget.pk}/report/").status_code, 200)
        self.assertEqual(books.post("/api/accounting/budgets/", {"code": "B", "name": "B", "start_date": "2026-04-01",
                                                                  "end_date": "2026-04-30"}, format="json").status_code, 403)
        self.assertEqual(self.as_("Purchasing Clerk").get("/api/accounting/budgets/").status_code, 403)
