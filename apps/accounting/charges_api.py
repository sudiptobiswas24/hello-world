"""Charge types: freight, handling, a rush surcharge. A line on a document that is not stock."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import record_or_404
from apps.core.audit import AuditableViewSetMixin

from .models import ChargeType, Tax


class ChargeTypeSerializer(serializers.ModelSerializer):
    tax_rows = serializers.SerializerMethodField()

    class Meta:
        model = ChargeType
        fields = ["id", "code", "name", "revenue_account", "expense_account", "taxes", "tax_rows",
                  "capitalise_into_inventory", "hsn_code", "is_active"]

    def get_tax_rows(self, obj):
        return [{"id": tax.pk, "name": str(tax)} for tax in obj.taxes.all()]


class ChargeTypeViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ChargeType.objects.select_related("revenue_account", "expense_account").prefetch_related("taxes")
    serializer_class = ChargeTypeSerializer
    filter_fields = ["is_active", "capitalise_into_inventory"]
    search_fields = ["code", "name", "hsn_code"]
    action_permission_map = {"taxes": "accounting.change_chargetype"}

    @action(detail=True, methods=["post", "delete"])
    def taxes(self, request, pk=None):
        """A tax the charge carries by default added ({"tax"}), or taken off (DELETE ?tax=)."""
        charge = self.get_object()
        given = request.data.get("tax") if request.method == "POST" else request.query_params.get("tax")
        tax = record_or_404(Tax, given, "tax")
        held = charge.taxes.filter(pk=tax.pk).exists()
        if request.method == "POST":
            if held:
                raise DRFValidationError({"tax": [f"{charge} carries {tax} already."]})
            charge.taxes.add(tax)
        else:
            if not held:
                raise DRFValidationError({"tax": [f"{charge} does not carry {tax}."]})
            charge.taxes.remove(tax)
        return Response(self.get_serializer(ChargeType.objects.get(pk=charge.pk)).data)
