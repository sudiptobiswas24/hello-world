from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import AuditableViewSetMixin
from .scoping import created, refuse_change, refuse_create, scoped, visible_parties
from .models import (
    Address,
    Company,
    Contact,
    Country,
    Currency,
    ExchangeRate,
    Party,
    PartyBankAccount,
    PartyRole,
    PartyRoleAssignment,
    PartyTag,
    PaymentTerms,
    UnitOfMeasure,
)
from .serializers import (
    AddressSerializer,
    CompanySerializer,
    ContactSerializer,
    CountrySerializer,
    CurrencySerializer,
    ExchangeRateSerializer,
    PartyBankAccountSerializer,
    PartyRoleAssignmentSerializer,
    PartySerializer,
    PartyTagSerializer,
    PaymentTermsSerializer,
    UnitOfMeasureSerializer,
)


class CountryViewSet(viewsets.ModelViewSet):
    queryset = Country.objects.all()
    serializer_class = CountrySerializer


class CurrencyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer

    @action(detail=True, methods=["get"])
    def rate(self, request, pk=None):
        """Effective rate against the base currency, optionally ?on=YYYY-MM-DD."""
        currency = self.get_object()
        on_date = request.query_params.get("on")
        try:
            rate = currency.rate_on(on_date or None)
        except (DjangoValidationError, ValueError) as exc:
            raise DRFValidationError(getattr(exc, "messages", [str(exc)]))
        return Response({"currency": currency.code, "on": on_date, "rate": rate})


class ExchangeRateViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ExchangeRate.objects.select_related("currency")
    serializer_class = ExchangeRateSerializer


class UnitOfMeasureViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = UnitOfMeasure.objects.all()
    serializer_class = UnitOfMeasureSerializer


class PartyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["code", "name", "legal_name", "email", "phone", "tax_id"]
    filter_fields = ["is_active", "role_assignments__role"]
    ordering_fields = ["code", "name"]

    queryset = Party.objects.prefetch_related("role_assignments", "addresses", "contacts", "tags")
    serializer_class = PartySerializer

    # Given with a new party, the role it is made in, made with it or not
    # at all: a customer saved without its role would be in no customer
    # list. Trading roles only; making someone an employee is not a
    # clerk's to do from a sales screen.
    TRADING_ROLES = {PartyRole.CUSTOMER, PartyRole.VENDOR}

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.user)

    def perform_create(self, serializer):
        role = self.request.data.get("role")
        if role is not None and role not in self.TRADING_ROLES:
            raise DRFValidationError({"role": [f"A new party can be made a customer or a vendor here, not {role!r}."]})
        user = self.request.user
        said = refuse_create(user, role)
        if said:
            raise DRFValidationError([said])
        with transaction.atomic():
            # Who made it, as AuditableViewSetMixin would have said: this
            # override had dropped it for every party made in the office.
            party = serializer.save(created_by=user, updated_by=user)
            if role is not None:
                PartyRoleAssignment.objects.create(party=party, role=role, created_by=user, updated_by=user)
            created(party, role, user)

    def perform_update(self, serializer):
        said = refuse_change(self.request.user, serializer.instance)
        if said:
            raise DRFValidationError([said])
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        said = refuse_change(self.request.user, instance)
        if said:
            raise DRFValidationError([said])
        super().perform_destroy(instance)


class PartyScopedMixin:
    """
    A record that belongs to a party (an address, a contact): read only
    where the party may be seen, and made or moved only onto one that may
    be changed.
    """

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.user, "party")

    def _check_party(self, serializer):
        party = serializer.validated_data.get("party") or getattr(serializer.instance, "party", None)
        if party is None:
            return
        limit = visible_parties(self.request.user)
        if limit is not None and not Party.objects.filter(limit, pk=party.pk).exists():
            raise DRFValidationError({"party": ["Not one of the parties you can see."]})
        said = refuse_change(self.request.user, party)
        if said:
            raise DRFValidationError({"party": [said]})

    def perform_create(self, serializer):
        self._check_party(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_party(serializer)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        said = refuse_change(self.request.user, instance.party)
        if said:
            raise DRFValidationError([said])
        super().perform_destroy(instance)


class PartyRoleAssignmentViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyRoleAssignment.objects.all()
    serializer_class = PartyRoleAssignmentSerializer


class AddressViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Address.objects.select_related("country", "party")
    serializer_class = AddressSerializer


class ContactViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("party")
    serializer_class = ContactSerializer


class PartyBankAccountViewSet(PartyScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyBankAccount.objects.select_related("party", "currency")
    serializer_class = PartyBankAccountSerializer


class PartyTagViewSet(viewsets.ModelViewSet):
    queryset = PartyTag.objects.all()
    serializer_class = PartyTagSerializer


class PaymentTermsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PaymentTerms.objects.all()
    serializer_class = PaymentTermsSerializer


class CompanyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer


# What other modules add to /me/: hr says which employee the login is.
# Registered by them (their apps.py), so core imports none of them.
ME_EXTRAS = []


def register_me_extra(provider):
    if provider not in ME_EXTRAS:
        ME_EXTRAS.append(provider)


class MeView(APIView):
    """
    Who is signed in, and what they may do: what the office application
    reads to decide which screens and buttons to offer.

    Offering is all it decides. Every request is still checked against
    the same permissions by the view that answers it; a screen hidden
    here is a convenience, never the lock.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        answer = {
            "id": user.pk,
            "username": user.get_username(),
            "name": user.get_full_name() or user.get_username(),
            "is_superuser": user.is_superuser,
            "roles": sorted(user.groups.values_list("name", flat=True)),
            "permissions": sorted(user.get_all_permissions()),
            "company": Company.get().name,
        }
        for provider in ME_EXTRAS:
            answer.update(provider(user))
        return Response(answer)
