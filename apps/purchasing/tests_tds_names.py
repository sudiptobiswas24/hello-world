"""
TAN on the company, required before a TDS challan or return is made and
kept on the challan; and the Income-tax Act 2025's names for periods from
1 April 2026, the 1961 Act's before (g24 brief fix_statutory, decisions 3
and 4). 194Q at 0.1% on what a year passes 50 lakh by: two bills of 30 lakh
in June 2026 deduct 1,000 on the second.
"""

import datetime

from django.core.exceptions import ValidationError

from apps.accounting.statutory_names import heading, name_on
from apps.core.models import Company

from .models import TdsChallan
from .tests_tds import TdsTestCase

D = datetime.date


class TanAndTheActsNamesTests(TdsTestCase):
    def setUp(self):
        super().setUp()
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        type(self.goods).objects.filter(pk=self.goods.pk).update(section_1961="194Q", section_2025="393(1)")
        self.goods.refresh_from_db()

    def as_(self, role):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        user = User.objects.create_user(f"{role.replace(' ', '_').lower()}-{User.objects.count()}")
        user.groups.add(Group.objects.get(name=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def set_tan(self, tan):
        company = Company.get()
        company.tan = tan
        company.save()

    def deducted_in_june(self):
        self.bill("3000000")
        self.bill("3000000").deduct_tds()

    def test_a_tan_is_four_letters_five_digits_and_a_letter(self):
        self.set_tan(" cala12345b ")
        self.assertEqual(Company.get().tan, "CALA12345B")
        for wrong in ("CAL12345B", "CALA1234BB", "CALA123456"):
            with self.assertRaisesMessage(ValidationError, "is not a TAN: four letters, five digits and a letter"):
                self.set_tan(wrong)

    def test_no_challan_and_no_return_without_a_tan(self):
        self.set_tan("")
        self.deducted_in_june()
        with self.assertRaisesMessage(ValidationError, "The company has no TAN, and a TDS challan carries it"):
            TdsChallan.pay(self.goods, D(2026, 6, 1), D(2026, 7, 7), self.bank, "00042", "0510308")
        refused = self.as_("Controller").get("/api/purchasing/tds-deductions/quarter/",
                                             {"from": "2026-04-01", "to": "2026-06-30"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("has no TAN, and a TDS return carries it", refused.content.decode())

    def test_the_challan_keeps_the_tan_and_the_2025_acts_names_it_was_paid_under(self):
        self.set_tan("CALA12345B")
        self.deducted_in_june()
        challan = TdsChallan.pay(self.goods, D(2026, 6, 1), D(2026, 7, 7), self.bank, "00042", "0510308")
        self.assertEqual((challan.tan, challan.form, challan.section_code),
                         ("CALA12345B", "Challan ITNS 281N", "393(1)"))
        self.set_tan("CALB54321C")
        challan.refresh_from_db()
        self.assertEqual(challan.tan, "CALA12345B")
        [listed] = self.as_("AP Manager").get("/api/purchasing/tds-challans/").json()
        self.assertEqual((listed["tan"], listed["form"], listed["section_code"]),
                         ("CALA12345B", "Challan ITNS 281N", "393(1)"))

    def test_the_return_names_the_form_of_its_period(self):
        self.set_tan("CALA12345B")
        self.deducted_in_june()
        controller = self.as_("Controller")
        q1 = controller.get("/api/purchasing/tds-deductions/quarter/", {"from": "2026-04-01", "to": "2026-06-30"})
        self.assertEqual(q1.status_code, 200, q1.content)
        body = q1.json()
        self.assertEqual((body["tan"], body["form"], body["act"], body["challan_form"]),
                         ("CALA12345B", "Form 140", "Income-tax Act 2025", "Challan ITNS 281N"))
        self.assertEqual([(row["section"], row["amount"]) for row in body["rows"]], [("393(1)", "1000.00")])
        q4 = controller.get("/api/purchasing/tds-deductions/quarter/", {"from": "2026-01-01", "to": "2026-03-31"})
        self.assertEqual((q4.json()["form"], q4.json()["act"], q4.json()["challan_form"], q4.json()["rows"]),
                         ("Form 26Q", "Income-tax Act 1961", "Challan ITNS 281", []))

    def test_each_name_by_the_day_before_and_the_day_the_2025_act_applies(self):
        before, after = D(2026, 3, 31), D(2026, 4, 1)
        # Only the documents the ERP builds: the 27Q/144, 24Q/138 and Form 16/130 names wait for them (review p15).
        expected = {"tds_return": ("Form 26Q", "Form 140"), "tcs_return": ("Form 27EQ", "Form 143"),
                    "tcs_declaration": ("Form 27C", "Form 127"),
                    "tds_challan": ("Challan ITNS 281", "Challan ITNS 281N"),
                    "tcs_challan": ("Challan ITNS 281", "Challan ITNS 281N")}
        self.assertEqual({key: (name_on(key, before), name_on(key, after)) for key in expected}, expected)
        self.set_tan("CALA12345B")
        self.assertEqual(heading("tcs", D(2026, 4, 1), D(2026, 6, 30))["form"], "Form 143")
