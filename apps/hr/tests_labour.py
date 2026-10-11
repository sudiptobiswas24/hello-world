"""
Statutory bonus and gratuity, figures worked by hand:

  bonus, 8.33% of basic and special capped at 7,000, for those whose basic,
  special and HRA come to 21,000 or less:
    A: 12,000 + 3,000 = 15,000 -> 7,000 -> 583.10;  covered at 19,800
    B: covered at 28,000? no -> nothing
    paid out in October: the bonus payable clears to nothing
  gratuity provision, 4.81% of basic: A 577.20, B 962.00
  gratuity owed on 30 Jun 2026, 15/26 of basic a counted year:
    hired 1 Jan 2020: 6 y 5 m -> 6 years; A 41,538.46, B 69,230.77
    hired 15 Dec 2019: 6 y 6 m 15 d -> 7 years -> 48,461.54
    hired 1 Jan 2023: 3 years, owed 20,769.23 but not yet payable
"""

import datetime
from decimal import Decimal

from apps.accounting.models import Account, AccountType

from .gratuity import counted_years, gratuity_due
from .payroll import ComponentBasis, ComponentKind, PayComponent, Rounding
from .tests_statutory import JUNE, StatutoryTestCase


class BonusAndGratuityTests(StatutoryTestCase):
    def setUp(self):
        super().setUp()
        self.bonus_payable = Account.objects.create(code="2340", name="Bonus payable",
                                                    account_type=AccountType.LIABILITY)
        self.gratuity_provision = Account.objects.create(code="2350", name="Gratuity provision",
                                                         account_type=AccountType.LIABILITY)
        self.bonus = self.percent("BONUS", 85, ComponentKind.EMPLOYER_COST, self.bonus_payable,
                                  base=[self.basic, self.special], ceiling="7000", rounding=Rounding.PAISA,
                                  coverage=[self.basic, self.special, self.hra], coverage_ceiling="21000")
        self.bonus.coverage_period_months = 1
        self.bonus.save()
        self.gratuity = self.percent("GRAT", 86, ComponentKind.EMPLOYER_COST, self.gratuity_provision,
                                     base=[self.basic], rounding=Rounding.PAISA)
        for person in (self.a, self.b):
            self.paid(person, self.bonus, "8.33")
            self.paid(person, self.gratuity, "4.81")

    def test_bonus_and_gratuity_accrue_each_month(self):
        run = self.run_for(*JUNE)
        a, b = self.lines(run, self.a), self.lines(run, self.b)
        self.assertEqual((a["BONUS"], a["GRAT"]), (Decimal("583.10"), Decimal("577.20")))
        self.assertNotIn("BONUS", b)
        self.assertEqual(b["GRAT"], Decimal("962.00"))
        self.assertEqual(self.balance(self.bonus_payable), Decimal("-583.10"))

    def test_the_bonus_paid_out_clears_what_was_accrued(self):
        self.run_for(*JUNE)
        payout = PayComponent.objects.create(code="BONUS-PAID", name="Bonus paid", kind=ComponentKind.EARNING,
                                             basis=ComponentBasis.FIXED, expense_account=self.bonus_payable,
                                             sequence=95)
        self.paid(self.a, payout, "583.10", since=datetime.date(2026, 10, 1))
        for component in (self.bonus, self.gratuity):
            component.is_active = False
            component.save()
        self.run_for(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        self.assertEqual(self.balance(self.bonus_payable), Decimal("0"))

    def test_gratuity_owed_set_against_the_provision(self):
        self.run_for(*JUNE)
        report = gratuity_due(datetime.date(2026, 6, 30), [self.basic], self.gratuity_provision)
        owed = {row["number"]: (row["years"], row["owed"], row["payable"]) for row in report["rows"]}
        self.assertEqual(owed["A"], (6, Decimal("41538.46"), True))
        self.assertEqual(owed["B"], (6, Decimal("69230.77"), True))
        self.assertEqual(report["provided"], Decimal("1539.20"))
        self.assertEqual(report["short"], report["total"] - Decimal("1539.20"))

    def test_years_counted(self):
        on = datetime.date(2026, 6, 30)
        self.assertEqual(counted_years(datetime.date(2020, 1, 1), on), 6)
        self.assertEqual(counted_years(datetime.date(2019, 12, 15), on), 7)
        self.assertEqual(counted_years(datetime.date(2019, 12, 30), on), 6)
        self.assertEqual(counted_years(datetime.date(2019, 12, 31), on), 6)
        late = self.person("C", basic="12000")
        late.hire_date = datetime.date(2023, 1, 1)
        late.save()
        row = [r for r in gratuity_due(on, [self.basic])["rows"] if r["number"] == "C"][0]
        self.assertEqual((row["years"], row["owed"], row["payable"]), (3, Decimal("20769.23"), False))

    def test_the_payroll_officer_reads_it_and_a_line_manager_does_not(self):
        from django.contrib.auth.models import Group, User
        from django.core.management import call_command
        from rest_framework.test import APIClient

        call_command("setup_roles", verbosity=0)
        self.run_for(*JUNE)

        def as_(role):
            user = User.objects.create_user("reader-" + role.replace(" ", "-").lower())
            user.groups.add(Group.objects.get(name=role))
            client = APIClient()
            client.force_authenticate(user)
            return client

        query = {"as_of": "2026-06-30", "components": "BASIC", "provision": self.gratuity_provision.pk}
        officer = as_("Payroll Officer")
        report = officer.get("/api/hr/gratuity/", query)
        self.assertEqual(report.status_code, 200, report.content)
        self.assertEqual((report.json()["total"], report.json()["provided"]), ("110769.23", "1539.20"))
        self.assertEqual(as_("Line Manager").get("/api/hr/gratuity/", query).status_code, 403)
        # A code the plant does not use counts nothing and is said, not refused: the screen opens.
        unknown = officer.get("/api/hr/gratuity/", {**query, "components": "BASIC,NOPE"})
        self.assertEqual(unknown.status_code, 200, unknown.content)
        self.assertIn("No pay component NOPE", unknown.json()["note"])
        self.assertEqual(unknown.json()["total"], "110769.23")
