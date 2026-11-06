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
    def test_a_taken_code_of_another_purpose_is_reported_and_nothing_wired_into_it(self):
        """Review F12: 2201 is the plant's Rent payable, 2260 an expense."""
        rent = Account.objects.create(code="2201", name="Rent payable", account_type=AccountType.LIABILITY)
        welfare = Account.objects.create(code="2260", name="Staff welfare", account_type=AccountType.EXPENSE)
        out = run()
        self.assertIn("collision, for the accountant: account 2201 is 'Rent payable' (liability), not Output CGST: "
                      "left as it is and nothing wired into it; Output CGST seeded as 2201-IN", out)
        self.assertEqual(Tax.objects.get(code="CGST2.5").collected_account.code, "2201-IN")
        self.assertEqual(TcsSection.objects.get(code="SCRAP").payable_account.code, "2260-IN")
        self.assertFalse(Tax.objects.filter(collected_account__in=[rent, welfare]).exists())
        rent.refresh_from_db()
        self.assertEqual(rent.name, "Rent payable")

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
        self.assertEqual(TcsSection.objects.get(code="SCRAP").payable_account.code, "2260")
        self.assertTrue(Account.objects.get(code="2250").name.startswith("TDS payable"))

    def test_seeded_is_listed_for_review_and_the_rate_is_not_confirmed(self):
        out = run()
        # Review F13: the section by date, both unconfirmed, and the rate question put to the accountant.
        self.assertEqual([(str(rate.applies_from), rate.section_code, rate.rate_percent, rate.confirmed_on)
                          for rate in TcsRate.objects.order_by("applies_from")],
                         [("2025-04-01", "206C(1)", D("1"), None), ("2026-04-01", "394(1)", D("2"), None)])
        rate = TcsRate.objects.get(section_code="394(1)")
        self.assertIn("1% or 2% under 394(1) from 1 April 2026?",
                      SeededDefault.objects.get(model="accounting.tcsrate", object_id=rate.pk).label)
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


class AFreshInstallIsReadyToPostTests(TestCase):
    """
    Seen on a first installation (2 November): no currency to choose, the year from January, and every default
    account blank, so nothing could post until seventeen accounts were picked by hand.
    """

    def test_a_fresh_install_gets_rupees_its_year_from_april_and_every_default_account(self):
        from apps.imports.management.commands.go_live_check import checks

        self.assertFalse(Company.objects.exists())
        out = run()
        company = Company.objects.get()
        self.assertEqual((company.base_currency.code, company.base_currency.is_base, company.fiscal_year_start_month),
                         ("INR", True, 4))
        wired = {field: getattr(company, field).code for field, _code, _words in
                 __import__("apps.accounting.management.commands.setup_indian_books",
                            fromlist=["DEFAULTS"]).DEFAULTS}
        self.assertEqual(wired, {
            "default_receivable_account": "1100", "default_payable_account": "2000", "default_bank_account": "1200",
            "default_revenue_account": "4000", "default_inventory_account": "1300", "default_cogs_account": "5010",
            "grni_account": "2150", "default_purchase_expense_account": "5000", "customer_deposit_account": "2300",
            "vendor_prepayment_account": "1410", "fx_gain_account": "4900", "fx_loss_account": "5910",
            "settlement_discount_account": "5310", "settlement_discount_received_account": "4310",
            "bad_debt_account": "5200", "purchase_price_variance_account": "5150", "net_pay_account": "2400",
            "claims_recoverable_account": "1360"})
        self.assertIn("year from April (was January)", out)
        from apps.core.models import PaymentTerms

        self.assertEqual(dict(PaymentTerms.objects.values_list("code", "net_days")),
                         {"IMMEDIATE": 0, "N15": 15, "N30": 30, "N45": 45, "N60": 60})
        self.assertTrue(SeededDefault.objects.filter(model="core.company", object_id=company.pk).exists())
        # What the go-live check asked of the accountant by hand is done; what is the plant's own choice is not.
        said = {what: level for level, what, _detail in checks()}
        self.assertNotIn("Base currency", said)
        self.assertEqual([what for what, level in said.items() if what.startswith("Account:")], [])
        self.assertEqual(said["Warehouses"], "FAIL")

    def test_what_the_company_already_says_is_kept_and_a_posted_year_is_not_moved(self):
        from apps.accounting.models import JournalEntry
        import datetime

        usd = Currency.objects.create(code="USD", name="Dollar", is_base=True)
        debtors = Account.objects.create(code="1105", name="Sundry debtors", account_type=AccountType.ASSET)
        Company.objects.create(name="Bengal Woven Sacks", base_currency=usd, default_receivable_account=debtors)
        JournalEntry.objects.create(date=datetime.date(2026, 4, 1), memo="Opening")
        out = run()
        company = Company.objects.get()
        self.assertEqual((company.base_currency.code, company.default_receivable_account.code,
                          company.fiscal_year_start_month), ("USD", "1105", 1))
        self.assertFalse(Currency.objects.get(code="INR").is_base)
        self.assertIn("company year from January: entries are posted, so it is not moved", out)
        self.assertEqual(company.default_payable_account.code, "2000")

    def test_an_account_of_the_right_type_named_for_something_else_is_not_wired(self):
        Account.objects.create(code="2150", name="Rent payable", account_type=AccountType.LIABILITY)
        out = run()
        self.assertIsNone(Company.objects.get().grni_account)
        self.assertIn("company grni account: account 2150 is 'Rent payable', not what it is for; left unset, "
                      "choose it on Settings > Company", out)


class TheExampleInItsOwnWordsRunsTests(TestCase):
    """The command's docstring shows a GSTIN to run it with; that one failed its check character (g22 month)."""

    def test_the_gstin_its_docstring_shows_is_taken(self):
        import re

        from .management.commands import setup_indian_books

        Company.objects.create(name="Bengal Woven Sacks", base_currency=Currency.objects.create(
            code="INR", name="Rupee", is_base=True))
        [shown] = re.findall(r"--gstin (\w{15})", setup_indian_books.__doc__)
        run(gstin=shown)
        self.assertEqual(GstSettings.objects.get().gstin, shown)
