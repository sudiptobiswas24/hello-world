"""Review probes for g24: the TDS and TCS challans and the 2025 Act's names."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounting.models import TcsSection
from apps.core.models import Company, Party

from .models import TdsChallan
from .tests_tds import TdsTestCase

D = datetime.date


class ChallanNameProbes(TdsTestCase):
    def setUp(self):
        super().setUp()
        type(self.goods).objects.filter(pk=self.goods.pk).update(section_1961="194Q", section_2025="393(1)")
        self.goods.refresh_from_db()

    def test_p12_march_deductions_paid_in_april_are_one_acts_challan_throughout(self):
        # 3,000,000 + 3,000,000 on 10 March 2026 (before the 2025 Act): 1,000 deducted on the second.
        self.bill("3000000", day=D(2026, 3, 10))
        self.bill("3000000", day=D(2026, 3, 11)).deduct_tds()
        challan = TdsChallan.pay(self.goods, D(2026, 3, 1), D(2026, 4, 7), self.bank, "00042", "0510308")
        # the deductions are the 1961 Act's; its own source says ITNS 281 is for 1961-Act payments
        self.assertEqual((challan.section_code, challan.form), ("194Q", "Challan ITNS 281"))

    def test_p13_a_challan_in_the_2025_act_names_its_own_act_consistently(self):
        self.bill("3000000", day=D(2026, 6, 10))
        self.bill("3000000", day=D(2026, 6, 11)).deduct_tds()
        challan = TdsChallan.pay(self.goods, D(2026, 6, 1), D(2026, 7, 7), self.bank, "00043", "0510308")
        self.assertEqual((challan.section_code, challan.form), ("393(1)", "Challan ITNS 281N"))

    def test_p14_malformed_tan_over_the_company_api_is_a_400_and_a_lowercase_one_is_kept_upper(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        admin = User.objects.create_superuser("root-r", password="x")
        client = APIClient()
        client.force_authenticate(admin)
        company = Company.get()
        url = f"/api/core/company/{company.pk}/"
        bad = client.patch(url, {"tan": "CALA12345"}, format="json")
        self.assertEqual(bad.status_code, 400, bad.content)
        good = client.patch(url, {"tan": "cala54321b"}, format="json")
        self.assertEqual(good.status_code, 200, good.content)
        self.assertEqual(Company.get().tan, "CALA54321B")

    def test_p15_a_tcs_declaration_taken_before_april_2026_is_form_27c(self):
        from apps.accounting.models import TcsDeclaration

        section = TcsSection.objects.create(code="206CI", name="Scrap", form_code="6CE")
        party = Party.objects.create(code="B-1", name="Buyer")
        declaration = TcsDeclaration.objects.create(
            party=party, section=section, received_on=D(2026, 1, 15), valid_from=D(2026, 1, 15),
            valid_to=D(2026, 3, 31))
        self.assertIn("27C", declaration.form)

    def test_p16_a_return_for_a_quarter_without_a_tan_is_a_400_not_a_500(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        Company.objects.filter(pk=Company.get().pk).update(tan="")
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser("root-q", password="x"))
        for path in ("/api/purchasing/tds-deductions/quarter/", "/api/sales/tcs-collections/quarter/"):
            answer = client.get(path, {"from": "2026-04-01", "to": "2026-06-30"})
            self.assertEqual(answer.status_code, 400, (path, answer.content))
