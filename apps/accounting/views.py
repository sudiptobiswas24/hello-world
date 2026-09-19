from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from decimal import Decimal, InvalidOperation

from .models import (
    Account,
    FiscalPosition,
    FiscalPositionTaxMapping,
    JournalEntry,
    JournalLine,
    PartyTaxProfile,
    Tax,
    TaxGroup,
    compute_taxes,
)
from .serializers import (
    AccountSerializer,
    FiscalPositionSerializer,
    FiscalPositionTaxMappingSerializer,
    JournalEntrySerializer,
    JournalLineSerializer,
    PartyTaxProfileSerializer,
    TaxGroupSerializer,
    TaxSerializer,
)


class AccountViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class JournalEntryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalEntry.objects.prefetch_related("lines")
    serializer_class = JournalEntrySerializer
    action_permission_map = {
        "post_entry": "accounting.post_journalentry",
        "reverse": "accounting.post_journalentry",
    }

    @action(detail=True, methods=["post"])
    def post_entry(self, request, pk=None):
        entry = self.get_object()
        try:
            entry.post()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        entry = self.get_object()
        try:
            reversal = entry.create_reversal(memo=request.data.get("memo", ""))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(reversal).data)


class JournalLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JournalLine.objects.all()
    serializer_class = JournalLineSerializer


class TaxGroupViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = TaxGroup.objects.all()
    serializer_class = TaxGroupSerializer


class TaxViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Tax.objects.select_related("group", "collected_account", "paid_account")
    serializer_class = TaxSerializer

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        Compute tax on an amount without creating a document:
        {"amount": "100.00", "quantity": "1", "tax_ids": [1, 2], "party_id": 5}
        """
        try:
            amount = Decimal(str(request.data.get("amount")))
            quantity = Decimal(str(request.data.get("quantity", "1")))
        except (InvalidOperation, TypeError):
            raise DRFValidationError("amount and quantity must be numbers.")

        taxes = list(Tax.objects.filter(pk__in=request.data.get("tax_ids", [])))

        party_id = request.data.get("party_id")
        if party_id:
            profile = PartyTaxProfile.objects.filter(party_id=party_id).first()
            if profile:
                taxes = profile.applicable_taxes(taxes)

        base, lines, total = compute_taxes(taxes, amount, quantity)
        return Response({
            "base": base,
            "taxes": [
                {"id": tax.pk, "code": tax.code, "name": tax.name, "amount": tax_amount}
                for tax, tax_amount in lines
            ],
            "total": total,
        })


class FiscalPositionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FiscalPosition.objects.prefetch_related("tax_mappings")
    serializer_class = FiscalPositionSerializer


class FiscalPositionTaxMappingViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = FiscalPositionTaxMapping.objects.select_related("source_tax", "target_tax")
    serializer_class = FiscalPositionTaxMappingSerializer


class PartyTaxProfileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PartyTaxProfile.objects.select_related("party", "fiscal_position")
    serializer_class = PartyTaxProfileSerializer
