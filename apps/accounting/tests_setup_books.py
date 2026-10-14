"""
setup_indian_books: a new company's Indian books, every figure seeded a
default for the accountant to review, nothing that exists overwritten.
"""

from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Company, Currency

from .gst import GstSettings
from .models import Account, AccountType, FiscalPosition, SeededDefault, Tax, TcsRate, TcsSection


def run(**options):
    out = StringIO()
    call_command("setup_indian_books", stdout=out, **options)
    return out.getvalue()


class SetupIndianBooksTests(TestCase):
    def setUp(self):
        inr = Currency.objects.create(code="INR", name="Rupee", is_base=True)
        Company.objects.create(name="Bengal Woven Sacks", base_currency=inr)

    def test_the_slabs_the_positions_and_the_tax_accounts(self):
        run()
        self.assertEqual({tax.code: (tax.rate, tax.gst_head) for tax in Tax.objects.filter(code__endswith="9")},
                         {"CGST9": (D("9"), "cgst"), "SGST9": (D("9"), "sgst")})
        self.assertEqual(Tax.objects.get(code="IGST18").group.code, "IGST18")
        self.assertEqual(Tax.objects.get(code="CGST2.5").group.code, "GST5")
        self.assertEqual(sorted(Tax.objects.values_list("code", flat=True)), sorted(
            ["CGST0", "SGST0", "IGST0", "CGST2.5", "SGST2.5", "IGST5", "CGST9", "SGST9", "IGST18",
             "CGST20", "SGST20", "IGST40"]))
        inter = FiscalPosition.objects.get(code="INTER")
        self.assertEqual(inter.map_tax(Tax.objects.get(code="CGST9")).code, "IGST18")
        self.assertIsNone(inter.map_tax(Tax.objects.get(code="SGST9")))
        self.assertEqual(FiscalPosition.objects.get(code="EXPORT-LUT").map_tax(Tax.objects.get(code="CGST9")).code,
                         "IGST0")
        self.assertTrue(FiscalPosition.objects.filter(code="INTRA").exists())
        self.assertEqual(Account.objects.get(code="2260").account_type, AccountType.LIABILITY)
        self.assertEqual(TcsSection.objects.get(code="206C(1)").payable_account.code, "2260")
        self.assertTrue(Account.objects.get(code="2250").name.startswith("TDS payable"))

    def test_seeded_is_listed_for_review_and_the_rate_is_not_confirmed(self):
        out = run()
        (rate,) = TcsRate.objects.all()
        self.assertEqual((rate.rate_percent, rate.confirmed_on), (D("1"), None))
        self.assertEqual(SeededDefault.objects.filter(reviewed_on__isnull=True).count(),
                         SeededDefault.objects.count())
        self.assertTrue(SeededDefault.objects.filter(model="accounting.tcsrate", object_id=rate.pk).exists())
        self.assertIn("seeded for the accountant to review", out)
        self.assertIn("unconfirmed", out)

    def test_it_never_overwrites_and_a_second_run_adds_nothing(self):
        Account.objects.create(code="2260", name="TCS collected on scrap", account_type=AccountType.LIABILITY)
        out = Account.objects.create(code="2299", name="Old output", account_type=AccountType.LIABILITY)
        into = Account.objects.create(code="1399", name="Old input", account_type=AccountType.ASSET)
        Tax.objects.create(code="CGST9", name="Central 9", rate=D("9"), gst_head="cgst", collected_account=out,
                           paid_account=into)
        first = run()
        self.assertEqual(Account.objects.get(code="2260").name, "TCS collected on scrap")
        self.assertEqual(Tax.objects.get(code="CGST9").name, "Central 9")
        self.assertIn("kept as it was: account 2260", first)
        seeded = SeededDefault.objects.count()
        counts = (Account.objects.count(), Tax.objects.count(), TcsRate.objects.count())
        second = run()
        self.assertEqual((SeededDefault.objects.count(), Account.objects.count(), Tax.objects.count(),
                          TcsRate.objects.count()), (seeded, *counts))
        self.assertIn("0 seeded for the accountant to review", second)

    def test_gst_settings_only_where_there_are_none(self):
        from apps.gst.tests import gstin

        number = gstin("19AABCB1234C1Z")
        run(gstin=number)
        settings = GstSettings.objects.get()
        self.assertEqual((settings.gstin, settings.state, settings.interstate_position.code), (number, "19", "INTER"))
        self.assertIn("kept as it was: GST settings", run(gstin=gstin("19AABCB9999C1Z")))
        self.assertEqual(GstSettings.objects.get().gstin, number)
