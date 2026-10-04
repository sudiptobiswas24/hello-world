"""
Every number the API sends as the exact decimal it is.

DRF wrote a Decimal that was not a serializer DecimalField as a JSON
float; 70 views did it, the statements among them. A float cannot hold
most paisa amounts exactly, and a reader adding them up drifts.
"""

import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.accounting.tests_reports import ReportTestCase
from apps.core.renderers import ExactJSONRenderer


class EncoderTests(SimpleTestCase):
    def render(self, data):
        return json.loads(ExactJSONRenderer().render(data))

    def test_a_decimal_is_its_exact_string(self):
        self.assertEqual(self.render({"a": Decimal("0.1") + Decimal("0.2")}), {"a": "0.3"})

    def test_never_in_exponent_form(self):
        self.assertEqual(self.render([Decimal("1E+2"), Decimal("-0.00")]), ["100", "-0.00"])

    def test_other_numbers_are_left_as_they_are(self):
        self.assertEqual(self.render({"count": 3, "rate": 0.5}), {"count": 3, "rate": 0.5})


class StatementsSendStringsTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        call_command("setup_roles", verbosity=0)
        user = User.objects.create_user("controller")
        user.groups.add(Group.objects.get(name="Controller"))
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_the_trial_balance_and_balance_sheet(self):
        self.capitalise("100000.10")
        trial = self.client.get("/api/accounting/financial-statements/trial-balance/")
        self.assertEqual(trial.status_code, 200, trial.content)
        bank = next(row for row in trial.json()["rows"] if row["account"] == "1010")
        self.assertIsInstance(bank["balance"], str)
        self.assertEqual(Decimal(bank["balance"]), Decimal("100000.10"))
        self.assertIsInstance(trial.json()["total_debit"], str)

        sheet = self.client.get("/api/accounting/financial-statements/balance-sheet/")
        self.assertEqual(sheet.status_code, 200, sheet.content)
        self.assertEqual(Decimal(sheet.json()["asset_total"]), Decimal("100000.10"))
        self.assertIsInstance(sheet.json()["asset_total"], str)

    def test_a_period_that_ends_before_it_starts_is_a_400(self):
        response = self.client.get("/api/accounting/financial-statements/trial-balance/",
                                   {"start": "2026-03-01", "as_of": "2026-02-01"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("after it ends", str(response.json()))
