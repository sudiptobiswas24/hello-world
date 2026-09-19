from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from .audit import AuditableViewSetMixin
from .models import (
    Address,
    Company,
    Contact,
    Country,
    Currency,
    ExchangeRate,
    Party,
    PartyBankAccount,
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
    queryset = Party.objects.prefetch_related("role_assignments", "addresses", "contacts", "tags")
    serializer_class = PartySerializer


class PartyRoleAssignmentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyRoleAssignment.objects.all()
    serializer_class = PartyRoleAssignmentSerializer


class AddressViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Address.objects.select_related("country", "party")
    serializer_class = AddressSerializer


class ContactViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("party")
    serializer_class = ContactSerializer


class PartyBankAccountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
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
