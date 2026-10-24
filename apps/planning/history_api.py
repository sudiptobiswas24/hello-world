"""Shipment history through the API: what the old system shipped, a month at a time, kept by the planner."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .history import ShipmentHistory


class ShipmentHistorySerializer(serializers.ModelSerializer):
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)

    class Meta:
        model = ShipmentHistory
        fields = ["id", "item", "item_sku", "item_name", "warehouse", "warehouse_code", "month", "quantity", "note"]


class ShipmentHistoryViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ShipmentHistory.objects.select_related("item", "warehouse")
    serializer_class = ShipmentHistorySerializer
    filter_fields = ["item", "warehouse"]
    search_fields = ["item__sku", "item__name", "note"]
    date_field = "month"
    ordering_fields = ["month", "item__sku"]
