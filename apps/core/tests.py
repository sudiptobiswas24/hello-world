from django.test import TestCase

from .models import Currency, Party, PartyRole, PartyRoleAssignment


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
