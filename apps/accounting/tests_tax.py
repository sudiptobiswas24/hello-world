from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.models import Country, Party

from .models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
    TaxComputation,
    TaxGroup,
    TaxScope,
    compute_taxes,
)


class TaxTestCase(TestCase):
    def setUp(self):
        self.tax_payable = Account.objects.create(
            code="2100", name="Sales Tax Payable", account_type=AccountType.LIABILITY
        )
        self.tax_receivable = Account.objects.create(
            code="1300", name="Input Tax Receivable", account_type=AccountType.ASSET
        )

    def make_tax(self, code="VAT20", rate="20", **kwargs):
        kwargs.setdefault("collected_account", self.tax_payable)
        kwargs.setdefault("paid_account", self.tax_receivable)
        return Tax.objects.create(code=code, name=f"Tax {code}", rate=Decimal(rate), **kwargs)


class TaxComputationTests(TaxTestCase):
    def test_simple_percentage(self):
        vat = self.make_tax(rate="20")
        self.assertEqual(vat.compute(Decimal("100")), Decimal("20.00"))

    def test_percentage_rounds_to_cents(self):
        tax = self.make_tax(code="T7", rate="7.5")
        self.assertEqual(tax.compute(Decimal("33.33")), Decimal("2.50"))

    def test_fixed_amount_per_unit_scales_with_quantity(self):
        levy = self.make_tax(code="ECO", rate="1.50", computation=TaxComputation.FIXED)
        self.assertEqual(levy.compute(Decimal("100"), quantity=Decimal("4")), Decimal("6.00"))

    def test_fixed_tax_ignores_the_base_amount(self):
        levy = self.make_tax(code="ECO2", rate="2", computation=TaxComputation.FIXED)
        self.assertEqual(levy.compute(Decimal("9999"), quantity=Decimal("1")), Decimal("2.00"))


class ComputeTaxesTests(TaxTestCase):
    def test_single_tax_on_exclusive_price(self):
        vat = self.make_tax(rate="20")
        base, lines, total = compute_taxes([vat], Decimal("100"))
        self.assertEqual(base, Decimal("100.00"))
        self.assertEqual(lines[0][1], Decimal("20.00"))
        self.assertEqual(total, Decimal("120.00"))

    def test_price_included_tax_is_extracted_from_the_amount(self):
        vat = self.make_tax(rate="20", price_included=True)
        base, lines, total = compute_taxes([vat], Decimal("120"))
        self.assertEqual(base, Decimal("100.00"))
        self.assertEqual(lines[0][1], Decimal("20.00"))
        self.assertEqual(total, Decimal("120.00"))

    def test_two_independent_taxes_both_apply_to_the_same_base(self):
        state = self.make_tax(code="ST", rate="6")
        city = self.make_tax(code="CT", rate="2")
        base, lines, total = compute_taxes([state, city], Decimal("200"))
        amounts = {tax.code: amount for tax, amount in lines}
        self.assertEqual(base, Decimal("200.00"))
        self.assertEqual(amounts["ST"], Decimal("12.00"))
        self.assertEqual(amounts["CT"], Decimal("4.00"))
        self.assertEqual(total, Decimal("216.00"))

    def test_compound_tax_applies_to_base_plus_earlier_tax(self):
        first = self.make_tax(code="GST", rate="5", sequence=1, include_base_amount=True)
        second = self.make_tax(code="PST", rate="10", sequence=2)
        base, lines, total = compute_taxes([first, second], Decimal("100"))
        amounts = {tax.code: amount for tax, amount in lines}
        self.assertEqual(amounts["GST"], Decimal("5.00"))
        # 10% of (100 + 5), not of 100
        self.assertEqual(amounts["PST"], Decimal("10.50"))
        self.assertEqual(total, Decimal("115.50"))

    def test_sequence_controls_compounding_order(self):
        low = self.make_tax(code="A", rate="10", sequence=1, include_base_amount=True)
        high = self.make_tax(code="B", rate="10", sequence=2)
        _, lines, _ = compute_taxes([high, low], Decimal("100"))
        self.assertEqual([tax.code for tax, _ in lines], ["A", "B"])

    def test_fixed_and_percentage_taxes_combine(self):
        levy = self.make_tax(code="ECO", rate="5", computation=TaxComputation.FIXED, sequence=1)
        vat = self.make_tax(code="VAT", rate="20", sequence=2)
        base, lines, total = compute_taxes([levy, vat], Decimal("100"), quantity=Decimal("2"))
        amounts = {tax.code: amount for tax, amount in lines}
        self.assertEqual(amounts["ECO"], Decimal("10.00"))  # 5 per unit x 2
        self.assertEqual(amounts["VAT"], Decimal("20.00"))  # on the 100 base, not the levy
        self.assertEqual(total, Decimal("130.00"))

    def test_no_taxes_leaves_the_amount_untouched(self):
        base, lines, total = compute_taxes([], Decimal("75.40"))
        self.assertEqual(base, Decimal("75.40"))
        self.assertEqual(lines, [])
        self.assertEqual(total, Decimal("75.40"))

    def test_included_tax_round_trips_back_to_the_original_price(self):
        vat = self.make_tax(rate="19", price_included=True)
        base, lines, total = compute_taxes([vat], Decimal("11.90"))
        self.assertEqual(total, Decimal("11.90"))
        self.assertEqual(base + lines[0][1], Decimal("11.90"))


class TaxConfigurationTests(TaxTestCase):
    def test_sales_tax_requires_a_collected_account(self):
        tax = Tax(
            code="BAD", name="Bad", rate=Decimal("20"), scope=TaxScope.SALES,
            paid_account=self.tax_receivable,
        )
        with self.assertRaises(ValidationError):
            tax.full_clean()

    def test_purchase_tax_requires_a_paid_account(self):
        tax = Tax(
            code="BAD2", name="Bad", rate=Decimal("20"), scope=TaxScope.PURCHASE,
            collected_account=self.tax_payable,
        )
        with self.assertRaises(ValidationError):
            tax.full_clean()

    def test_account_selected_by_document_side(self):
        vat = self.make_tax()
        self.assertEqual(vat.account_for(is_sale=True), self.tax_payable)
        self.assertEqual(vat.account_for(is_sale=False), self.tax_receivable)

    def test_scope_controls_where_a_tax_is_offered(self):
        sales_only = self.make_tax(code="S", rate="5", scope=TaxScope.SALES)
        self.assertTrue(sales_only.applies_to_sales())
        self.assertFalse(sales_only.applies_to_purchases())

    def test_negative_rate_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tax.objects.create(
                    code="NEG", name="Negative", rate=Decimal("-5"),
                    collected_account=self.tax_payable, paid_account=self.tax_receivable,
                )

    def test_taxes_can_be_grouped_for_reporting(self):
        group = TaxGroup.objects.create(code="VAT", name="Value Added Tax")
        tax = self.make_tax(group=group)
        self.assertEqual(list(group.taxes.all()), [tax])


class FiscalPositionTests(TaxTestCase):
    def setUp(self):
        super().setUp()
        self.domestic_vat = self.make_tax(code="VAT20", rate="20")
        self.zero_vat = self.make_tax(code="VAT0", rate="0")
        self.export = FiscalPosition.objects.create(code="EXPORT", name="Export (zero-rated)")

    def test_unmapped_tax_passes_through_unchanged(self):
        self.assertEqual(self.export.map_tax(self.domestic_vat), self.domestic_vat)

    def test_tax_can_be_substituted(self):
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.export, source_tax=self.domestic_vat, target_tax=self.zero_vat
        )
        self.assertEqual(self.export.map_tax(self.domestic_vat), self.zero_vat)

    def test_tax_can_be_removed_entirely(self):
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.export, source_tax=self.domestic_vat, target_tax=None
        )
        self.assertIsNone(self.export.map_tax(self.domestic_vat))
        self.assertEqual(self.export.map_taxes([self.domestic_vat]), [])

    def test_mapping_a_tax_to_itself_is_rejected(self):
        mapping = FiscalPositionTaxMapping(
            fiscal_position=self.export, source_tax=self.domestic_vat, target_tax=self.domestic_vat
        )
        with self.assertRaises(ValidationError):
            mapping.full_clean()

    def test_one_mapping_per_source_tax_per_position(self):
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.export, source_tax=self.domestic_vat, target_tax=self.zero_vat
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FiscalPositionTaxMapping.objects.create(
                    fiscal_position=self.export, source_tax=self.domestic_vat, target_tax=None
                )

    def test_fiscal_position_can_be_scoped_to_a_country(self):
        france = Country.objects.create(code="FR", name="France")
        position = FiscalPosition.objects.create(code="FR", name="France", country=france)
        self.assertEqual(list(france.fiscal_positions.all()), [position])


class PartyTaxProfileTests(TaxTestCase):
    def setUp(self):
        super().setUp()
        self.vat = self.make_tax(code="VAT20", rate="20")
        self.zero = self.make_tax(code="VAT0", rate="0")
        self.party = Party.objects.create(code="C-1", name="Acme")

    def test_party_without_a_profile_gets_the_default_taxes(self):
        profile = PartyTaxProfile.objects.create(party=self.party)
        self.assertEqual(profile.applicable_taxes([self.vat]), [self.vat])

    def test_exempt_party_gets_no_tax(self):
        profile = PartyTaxProfile.objects.create(
            party=self.party, tax_exempt=True, exemption_reference="EX-123"
        )
        self.assertEqual(profile.applicable_taxes([self.vat]), [])

    def test_exemption_requires_a_reference_on_file(self):
        profile = PartyTaxProfile(party=self.party, tax_exempt=True)
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_fiscal_position_applies_to_the_party(self):
        position = FiscalPosition.objects.create(code="EU", name="EU reverse charge")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=position, source_tax=self.vat, target_tax=self.zero
        )
        profile = PartyTaxProfile.objects.create(party=self.party, fiscal_position=position)
        self.assertEqual(profile.applicable_taxes([self.vat]), [self.zero])

    def test_exemption_beats_the_fiscal_position(self):
        position = FiscalPosition.objects.create(code="EU2", name="EU")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=position, source_tax=self.vat, target_tax=self.zero
        )
        profile = PartyTaxProfile.objects.create(
            party=self.party, fiscal_position=position, tax_exempt=True,
            exemption_reference="EX-9",
        )
        self.assertEqual(profile.applicable_taxes([self.vat]), [])

    def test_one_profile_per_party(self):
        PartyTaxProfile.objects.create(party=self.party)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PartyTaxProfile.objects.create(party=self.party)
