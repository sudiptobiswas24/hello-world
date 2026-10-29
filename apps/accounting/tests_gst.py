from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import Party

from .gst import GstSettings, gst_taxes, gstin_check_character, validate_gstin, validate_hsn
from .models import (
    Account,
    AccountType,
    FiscalPosition,
    FiscalPositionTaxMapping,
    PartyTaxProfile,
    Tax,
)

# Published registrations, used to prove the check character against
# numbers nobody here computed.
PUBLISHED = ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "07AAGFF2194N1Z1"]

MAHARASHTRA = "27AABCD1234E1Z8"
KARNATAKA = "29AABCE5678F1ZD"


class GstinTests(TestCase):
    def test_published_registrations_pass(self):
        for gstin in PUBLISHED:
            with self.subTest(gstin=gstin):
                self.assertEqual(validate_gstin(gstin), gstin)
                self.assertEqual(gstin_check_character(gstin), gstin[14])

    def test_any_one_mistyped_character_is_caught(self):
        """Every position, including the ones that are not doubled: a
        check that weighted all characters alike would miss swaps."""
        gstin = PUBLISHED[0]
        for position in range(13):  # 13 is the fixed Z
            original = gstin[position]
            typo = "7" if original != "7" else "8"
            if position in (2, 3, 4, 5, 6, 11):
                typo = "B" if original != "B" else "C"
            mistyped = gstin[:position] + typo + gstin[position + 1:]
            with self.subTest(position=position), self.assertRaises(ValidationError):
                validate_gstin(mistyped)

    def test_two_adjacent_characters_swapped_is_caught(self):
        # 0939 → 9039 inside the PAN's digits.
        with self.assertRaises(ValidationError):
            validate_gstin("27AAPFU9039F1ZV")

    def test_case_and_surrounding_space_are_forgiven(self):
        self.assertEqual(validate_gstin(" 27aapfu0939f1zv "), "27AAPFU0939F1ZV")

    def test_the_wrong_shape_is_refused(self):
        for bad in ["", "27AAPFU0939F1Z", "27AAPFU0939F1XV", "2AAAPFU0939F1ZV",
                    "27AAPFU0939F0ZV"]:
            with self.subTest(gstin=bad), self.assertRaises(ValidationError):
                validate_gstin(bad)

    def test_a_state_that_does_not_exist_is_refused(self):
        """25 was Daman and Diu, merged into 26 in 2020. The check
        character is right, so only the state list can refuse it."""
        self.assertEqual(gstin_check_character("25AABCD1234E1ZC"), "C")
        with self.assertRaisesMessage(ValidationError, "no state"):
            validate_gstin("25AABCD1234E1ZC")


class HsnTests(TestCase):
    def test_four_six_or_eight_digits(self):
        for code in ["6305", "630533", "63053300", ""]:
            self.assertEqual(validate_hsn(code), code)

    def test_anything_else_is_refused(self):
        for code in ["630", "63053", "6305330", "630533001", "6305A"]:
            with self.subTest(code=code), self.assertRaises(ValidationError):
                validate_hsn(code)


class GstTestCase(TestCase):
    def setUp(self):
        payable = Account.objects.create(
            code="2100", name="Output GST", account_type=AccountType.LIABILITY
        )

        def tax(code, rate):
            return Tax.objects.create(
                code=code, name=code, rate=Decimal(rate),
                collected_account=payable, paid_account=payable,
            )

        self.cgst = tax("CGST9", "9")
        self.sgst = tax("SGST9", "9")
        self.igst = tax("IGST18", "18")
        self.zero = tax("GST0", "0")
        self.interstate = FiscalPosition.objects.create(code="INTER", name="Inter-state")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.interstate, source_tax=self.cgst, target_tax=self.igst
        )
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.interstate, source_tax=self.sgst, target_tax=None
        )
        self.sez = FiscalPosition.objects.create(code="SEZ", name="SEZ, under LUT")
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.sez, source_tax=self.cgst, target_tax=self.zero
        )
        FiscalPositionTaxMapping.objects.create(
            fiscal_position=self.sez, source_tax=self.sgst, target_tax=None
        )
        self.intra = [self.cgst, self.sgst]

    def register(self, gstin=MAHARASHTRA):
        return GstSettings.objects.create(gstin=gstin, interstate_position=self.interstate)

    def profile(self, code="P-1", **kwargs):
        party = Party.objects.create(code=code, name=code)
        return PartyTaxProfile.objects.create(party=party, **kwargs)


class GstSettingsTests(GstTestCase):
    def test_the_state_is_read_off_the_gstin(self):
        settings = self.register(KARNATAKA.lower())
        self.assertEqual(settings.gstin, KARNATAKA)
        self.assertEqual(settings.state, "29")

    def test_a_bad_gstin_is_refused(self):
        with self.assertRaises(ValidationError):
            self.register("27AABCD1234E1Z9")

    def test_there_is_one_registration(self):
        first = self.register()
        with self.assertRaisesMessage(ValidationError, "one GST registration"):
            self.register(KARNATAKA)
        first.save()  # saving the one that exists is not a second one

    def test_inactive_settings_are_not_in_force(self):
        self.register()
        GstSettings.objects.update(is_active=False)
        self.assertIsNone(GstSettings.active())


class PartyRegistrationTests(GstTestCase):
    def test_the_state_comes_from_the_gstin(self):
        profile = self.profile(gstin=KARNATAKA)
        self.assertEqual(profile.gst_state, "29")
        self.assertEqual(profile.place_of_supply(), "29")
        self.assertEqual(profile.gst_registration, "regular")

    def test_a_state_that_contradicts_the_gstin_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "registered in Karnataka, not Maharashtra"):
            self.profile(gstin=KARNATAKA, gst_state="27")

    def test_a_state_that_agrees_is_accepted(self):
        self.assertEqual(self.profile(gstin=KARNATAKA, gst_state="29").gst_state, "29")

    def test_a_bad_gstin_is_refused_on_save(self):
        """Profiles are made in code; clean() never runs for them."""
        with self.assertRaises(ValidationError):
            self.profile(gstin="29AABCE5678F1ZE")

    def test_a_composition_dealer_stays_one(self):
        profile = self.profile(gstin=KARNATAKA, gst_registration="composition")
        self.assertEqual(profile.gst_registration, "composition")

    def test_an_unregistered_party_types_its_state(self):
        profile = self.profile(gst_state="29", gst_registration="unregistered")
        self.assertEqual(profile.place_of_supply(), "29")

    def test_an_unregistered_party_in_no_state_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "not a GST state code"):
            self.profile(gst_state="25")

    def test_an_exemption_needs_its_reference_even_when_made_in_code(self):
        with self.assertRaisesMessage(ValidationError, "exemption reference"):
            self.profile(tax_exempt=True)
        self.assertTrue(
            self.profile(code="P-2", tax_exempt=True, exemption_reference="EX-1").tax_exempt
        )

    def test_clean_checks_the_same_things(self):
        profile = PartyTaxProfile(gstin="29AABCE5678F1ZE")
        with self.assertRaises(ValidationError):
            profile.clean()


class StateLineTests(GstTestCase):
    def test_inside_the_state_central_and_state_tax(self):
        self.register()
        profile = self.profile(gstin="27AABCD1234E2Z7")
        self.assertEqual(profile.applicable_taxes(self.intra), self.intra)

    def test_across_a_state_line_integrated_tax(self):
        self.register()
        profile = self.profile(gstin=KARNATAKA)
        self.assertEqual(profile.applicable_taxes(self.intra), [self.igst])

    def test_the_company_state_decides_not_a_fixed_home(self):
        """The same Karnataka party is intra-state to a Karnataka plant."""
        self.register(KARNATAKA)
        profile = self.profile(gstin=KARNATAKA)
        self.assertEqual(profile.applicable_taxes(self.intra), self.intra)

    def test_an_unregistered_party_is_placed_by_its_state(self):
        self.register()
        profile = self.profile(gst_state="29", gst_registration="unregistered")
        self.assertEqual(profile.applicable_taxes(self.intra), [self.igst])

    def test_a_party_fiscal_position_outranks_the_state_line(self):
        """An SEZ unit in the same state is still zero-rated."""
        self.register()
        profile = self.profile(gstin="27AABCD1234E2Z7", fiscal_position=self.sez)
        self.assertEqual(profile.applicable_taxes(self.intra), [self.zero])

    def test_exempt_is_exempt_wherever_it_is(self):
        self.register()
        profile = self.profile(gstin=KARNATAKA, tax_exempt=True, exemption_reference="EX-1")
        self.assertEqual(profile.applicable_taxes(self.intra), [])

    def test_a_party_in_no_known_state_is_refused_under_gst(self):
        self.register()
        profile = self.profile()
        with self.assertRaisesMessage(ValidationError, "no state on its tax profile"):
            profile.applicable_taxes(self.intra)

    def test_without_gst_nothing_changes(self):
        profile = self.profile(gstin=KARNATAKA)
        self.assertEqual(profile.applicable_taxes(self.intra), self.intra)
        self.assertEqual(self.profile(code="P-2").applicable_taxes(self.intra), self.intra)

    def test_inactive_gst_changes_nothing(self):
        self.register()
        GstSettings.objects.update(is_active=False)
        profile = self.profile(gstin=KARNATAKA)
        self.assertEqual(gst_taxes(profile, self.intra), self.intra)
