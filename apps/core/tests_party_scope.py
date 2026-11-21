"""O130: scoped() keeps the rows that name no party; it narrows only the rows that name one."""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Address, Country, Party
from .scoping import PartyScope, _SCOPES, scoped


class OnlyAlpha(PartyScope):
    def visible(self, user):
        from django.db.models import Q

        return Q(code="ALPHA")


class ARowWithNoPartyStaysInViewTests(TestCase):
    def setUp(self):
        self.scope = OnlyAlpha()
        _SCOPES.append(self.scope)
        self.addCleanup(_SCOPES.remove, self.scope)
        india = Country.objects.create(code="IN", name="India")
        self.alpha = Party.objects.create(code="ALPHA", name="Alpha")
        self.beta = Party.objects.create(code="BETA", name="Beta")
        self.mine = Address.objects.create(party=self.alpha, line1="1 Mill Road", city="Howrah", country=india)
        self.theirs = Address.objects.create(party=self.beta, line1="2 Mill Road", city="Howrah", country=india)
        self.plants = Address.objects.create(party=None, line1="Plot 4, Uluberia", city="Howrah", country=india)
        self.user = User.objects.create_user("rep")

    def test_the_plants_own_address_stays_and_another_partys_goes(self):
        # Filtered by party__in alone, the plant's own address (no party) dropped out with Beta's.
        self.assertEqual(sorted(scoped(Address.objects.all(), self.user, "party").values_list("line1", flat=True)),
                         ["1 Mill Road", "Plot 4, Uluberia"])
