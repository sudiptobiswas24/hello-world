from rest_framework import serializers

from .models import Item, StockMovement, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["id", "code", "name", "address", "is_active"]


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = [
            "id",
            "sku",
            "name",
            "description",
            "item_type",
            "uom",
            "track_inventory",
            "is_active",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = [
            "id",
            "item",
            "warehouse",
            "movement_type",
            "quantity",
            "reference",
            "occurred_at",
            "notes",
        ]
