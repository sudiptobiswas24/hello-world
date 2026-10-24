"""A sack's packing recipe through the API: what a bale of it takes, kept by whoever keeps how things are made."""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .packing import PackingLine


class PackingLineSerializer(serializers.ModelSerializer):
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    packing_item_sku = serializers.CharField(source="packing_item.sku", read_only=True)
    packing_item_name = serializers.CharField(source="packing_item.name", read_only=True)
    uom = serializers.CharField(source="packing_item.uom.code", read_only=True)

    class Meta:
        model = PackingLine
        fields = ["id", "item", "item_sku", "packing_item", "packing_item_sku", "packing_item_name", "quantity", "uom"]


class PackingLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PackingLine.objects.select_related("item", "packing_item__uom")
    serializer_class = PackingLineSerializer
    filter_fields = ["item", "packing_item"]
    search_fields = ["item__sku", "packing_item__sku", "packing_item__name"]
    ordering_fields = ["item__sku"]
