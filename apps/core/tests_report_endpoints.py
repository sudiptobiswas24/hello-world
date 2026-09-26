"""
Reports somebody can actually ask for.

`manage.py audit_invariants` grew a check for the shape that has now
produced seven findings here: a helper built, tested, and called by
nothing. It found twelve — the financial statements, the AP aging, the
vendor scorecard, bad debt, the statement run, the leave summary and
the rest. All of them derived from documents and none of them reachable
by anything but a Python shell.

These tests are about the door existing. Each report's arithmetic has
its own tests; what is checked here is that asking for it returns
something.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounting.models import Account, AccountType, JournalEntry, JournalLine
from apps.core.models import (
    Company,
    Currency,
    Party,
    PartyRole,
    PartyRoleAssignment,
    UnitOfMeasure,
    UnitOfMeasureCategory,
)


class ReportEndpointTestCase(TestCase):
    def setUp(self):
        self.usd = Currency.objects.create(code="USD", name="USD", is_base=True)
        self.each = UnitOfMeasure.objects.create(
            code="ea", name="Each", category=UnitOfMeasureCategory.COUNT
        )
        acc = lambda c, n, t: Account.objects.create(code=c, name=n, account_type=t)
        self.bank = acc("1010", "Bank", AccountType.ASSET)
        self.capital = acc("3000", "Capital", AccountType.EQUITY)
        Company.objects.create(name="Test Co", base_currency=self.usd)
        entry = JournalEntry.objects.create(
            date=datetime.date(2026, 1, 1), memo="Opening"
        )
        JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal("1000"))
        JournalLine.objects.create(
            entry=entry, account=self.capital, credit=Decimal("1000")
        )
        entry.post()
        user = get_user_model().objects.create_superuser(
            username="reader", email="r@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user)


class FinancialStatementEndpointTests(ReportEndpointTestCase):
    def test_the_trial_balance_can_be_asked_for(self):
        # A double-entry system that cannot produce one on request can
        # record a year of trading and answer nothing about it.
        response = self.client.get(
            "/api/accounting/financial-statements/trial-balance/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["balanced"])
        self.assertEqual(Decimal(response.data["total_debit"]), Decimal("1000.00"))

    def test_the_profit_and_loss_can_be_asked_for(self):
        response = self.client.get(
            "/api/accounting/financial-statements/profit-and-loss/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("net_profit", response.data)

    def test_the_balance_sheet_can_be_asked_for(self):
        response = self.client.get(
            "/api/accounting/financial-statements/balance-sheet/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["balanced"])
        self.assertEqual(Decimal(response.data["asset_total"]), Decimal("1000.00"))

    def test_it_takes_a_date(self):
        response = self.client.get(
            "/api/accounting/financial-statements/balance-sheet/",
            {"as_of": "2025-12-31"},
        )
        self.assertEqual(Decimal(response.data["asset_total"]), Decimal("0.00"))


class PurchasingReportEndpointTests(ReportEndpointTestCase):
    def setUp(self):
        super().setUp()
        self.vendor = Party.objects.create(
            code="V-1", name="Supplier", default_currency=self.usd
        )
        PartyRoleAssignment.objects.create(party=self.vendor, role=PartyRole.VENDOR)

    def test_the_aging_can_be_asked_for(self):
        self.assertEqual(
            self.client.get("/api/purchasing/purchasing-reports/aging/").status_code,
            200,
        )

    def test_the_vendor_scorecard_can_be_asked_for(self):
        self.assertEqual(
            self.client.get(
                "/api/purchasing/purchasing-reports/vendor-performance/"
            ).status_code,
            200,
        )

    def test_the_payment_run_can_be_asked_for(self):
        self.assertEqual(
            self.client.get(
                "/api/purchasing/purchasing-reports/payment-run/"
            ).status_code,
            200,
        )

    def test_reorder_suggestions_can_be_asked_for(self):
        self.assertEqual(
            self.client.get("/api/purchasing/purchasing-reports/reorder/").status_code,
            200,
        )

    def test_consignment_on_hand_can_be_asked_for(self):
        self.assertEqual(
            self.client.get(
                "/api/purchasing/purchasing-reports/consignment/"
            ).status_code,
            200,
        )

    def test_a_vendors_balance_can_be_asked_for(self):
        response = self.client.get(
            "/api/purchasing/purchasing-reports/vendor-balance/",
            {"vendor": self.vendor.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.data["balance"]), Decimal("0.00"))

    def test_a_vendor_balance_needs_a_vendor(self):
        self.assertEqual(
            self.client.get(
                "/api/purchasing/purchasing-reports/vendor-balance/"
            ).status_code,
            400,
        )

    def test_drawing_consignment_needs_its_arguments(self):
        # A POST, because it buys goods: a GET that bought things would
        # buy again on every refresh.
        response = self.client.post(
            "/api/purchasing/purchasing-reports/draw-consignment/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("required", str(response.data))

    def test_raising_a_reorder_requisition_is_a_post(self):
        response = self.client.post(
            "/api/purchasing/purchasing-reports/raise-reorder-requisition/",
            {}, format="json",
        )
        self.assertIn(response.status_code, (200, 400))


class SalesReportEndpointTests(ReportEndpointTestCase):
    def test_bad_debt_can_be_asked_for(self):
        self.assertEqual(
            self.client.get("/api/sales/sales-reports/bad-debt/").status_code, 200
        )

    def test_the_statement_run_is_a_post(self):
        # It sends email. A report you refresh by reloading should not.
        self.assertEqual(
            self.client.get("/api/sales/sales-reports/statements/").status_code, 405
        )
        self.assertEqual(
            self.client.post(
                "/api/sales/sales-reports/statements/", {}, format="json"
            ).status_code,
            200,
        )


class HrReportEndpointTests(ReportEndpointTestCase):
    def test_an_employees_allowances_can_be_asked_for(self):
        from apps.hr.models import Employee

        party = Party.objects.create(code="E-1", name="Jane")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
        employee = Employee.objects.create(
            party=party, employee_number="E1", hire_date=datetime.date(2020, 1, 1)
        )
        response = self.client.get(f"/api/hr/employees/{employee.pk}/leave/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
