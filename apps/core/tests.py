from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Currency, Party, PartyRole, PartyRoleAssignment, UnitOfMeasure


class BaseCurrencyTests(TestCase):
    def test_only_one_currency_can_be_base(self):
        Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Currency.objects.create(code="EUR", name="Euro", is_base=True)

    def test_multiple_non_base_currencies_are_allowed(self):
        Currency.objects.create(code="USD", name="US Dollar", is_base=True)
        Currency.objects.create(code="EUR", name="Euro")
        Currency.objects.create(code="GBP", name="British Pound")
        self.assertEqual(Currency.objects.filter(is_base=False).count(), 2)


class UnitOfMeasureConversionTests(TestCase):
    def test_unit_without_base_converts_to_itself(self):
        each = UnitOfMeasure.objects.create(code="each", name="Each")
        self.assertEqual(each.to_base_quantity(Decimal("5")), Decimal("5"))

    def test_derived_unit_converts_to_base_quantity(self):
        each = UnitOfMeasure.objects.create(code="each", name="Each")
        case = UnitOfMeasure.objects.create(
            code="case", name="Case of 12", base_unit=each, conversion_factor=Decimal("12")
        )
        self.assertEqual(case.to_base_quantity(Decimal("3")), Decimal("36"))

    def test_base_unit_must_share_category(self):
        each = UnitOfMeasure.objects.create(code="each", name="Each")
        kg = UnitOfMeasure.objects.create(code="kg", name="Kilogram", category="weight")
        case = UnitOfMeasure.objects.create(code="case", name="Case", base_unit=each)
        case.base_unit = kg
        with self.assertRaises(Exception):
            case.full_clean()


class PartyRoleTests(TestCase):
    def test_a_single_party_can_hold_multiple_roles(self):
        usd = Currency.objects.create(code="USD", name="US Dollar", symbol="$")
        party = Party.objects.create(code="P-0001", name="Acme Co", default_currency=usd)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.CUSTOMER)
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)

        roles = set(party.role_assignments.values_list("role", flat=True))
        self.assertEqual(roles, {PartyRole.CUSTOMER, PartyRole.VENDOR})

    def test_duplicate_role_assignment_is_rejected(self):
        party = Party.objects.create(code="P-0002", name="Beta LLC")
        PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
        with self.assertRaises(Exception):
            PartyRoleAssignment.objects.create(party=party, role=PartyRole.VENDOR)
