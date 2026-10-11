from rest_framework import serializers

from .models import (
    AdjustmentReason,
    Item,
    ItemUnit,
    ItemAttribute,
    ItemAttributeValue,
    ItemTemplate,
    Lot,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMovement,
    StockReservation,
    StockTransfer,
    StockTransferLine,
    StorageBin,
    Warehouse,
)
from apps.core.customfields import ExtensibleSerializerMixin


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        # Everything that decides how a warehouse behaves, not just its
        # name: quarantine, transit, consignment and binning all changed
        # what stock does here and none of them were reachable.
        fields = [
            "id", "code", "name", "address", "consignment_vendor",
            "is_quarantine", "is_transit", "requires_bins",
            "receipt_route", "input_warehouse", "quality_warehouse",
            "allow_negative_stock", "is_active",
            "held_for",
        ]


class ItemUnitSerializer(serializers.ModelSerializer):
    uom_code = serializers.CharField(source="uom.code", read_only=True)

    class Meta:
        model = ItemUnit
        fields = ["id", "item", "uom", "factor", "uom_code"]


class ItemSerializer(ExtensibleSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = [
            "id", "sku", "name", "description", "item_type", "stock_class", "uom",
            "track_inventory", "tracking", "costing_method", "standard_cost",
            "sale_price", "inventory_account", "cogs_account", "is_active",
            "hsn_code", "extra",
        ]
        # A standard cost cannot be assigned: changing it revalues the
        # stock on hand, which is a posting. set_standard_cost() does it.
        read_only_fields = ["standard_cost"]


class StockMovementSerializer(serializers.ModelSerializer):
    # Named, so the stock ledger reads without a call per row.
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")

    class Meta:
        model = StockMovement
        fields = [
            "id", "item", "item_sku", "item_name", "warehouse", "warehouse_code",
            "movement_type", "uom", "lot", "lot_code", "bin",
            "quantity", "document_quantity", "unit_cost", "value_adjustment",
            "adjusts", "reference", "occurred_at", "notes",
        ]
        # The ledger restates a movement into the item's stocking unit
        # when it is written; document_quantity is what the paperwork
        # said, and is not something a caller sets.
        read_only_fields = ["document_quantity"]


# -- the documents -----------------------------------------------------
#
# Everything below was built and had no way in: adjustments, counts,
# transfers, lots, bins and reservations were reachable only from a
# Python shell. Posting, voiding and cancelling are actions rather than
# writable fields, because a status somebody can PATCH is not a state
# machine.


class LotSerializer(serializers.ModelSerializer):
    on_hand = serializers.SerializerMethodField()
    has_expired = serializers.SerializerMethodField()
    item_label = serializers.SerializerMethodField()

    def get_item_label(self, lot):
        return f"{lot.item.sku} · {lot.item.name}"

    class Meta:
        model = Lot
        fields = [
            "id", "item", "code", "expires_on", "manufactured_on",
            "supplier_reference", "notes", "is_active", "on_hand", "has_expired", "item_label",
        ]

    def get_on_hand(self, lot):
        return lot.on_hand_at()

    def get_has_expired(self, lot):
        return lot.has_expired()


class StorageBinSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = StorageBin
        fields = [
            "id", "warehouse", "warehouse_name", "parent", "code", "name", "sequence",
            "is_pickable", "is_active",
        ]


class AdjustmentReasonSerializer(serializers.ModelSerializer):
    account_label = serializers.SerializerMethodField()

    class Meta:
        model = AdjustmentReason
        fields = ["id", "code", "name", "account", "account_label", "direction", "is_active"]

    def get_account_label(self, reason):
        return f"{reason.account.code} · {reason.account.name}" if reason.account_id else ""


class StockAdjustmentLineSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")

    def get_item_label(self, line):
        return f"{line.item.sku} · {line.item.name}"

    class Meta:
        model = StockAdjustmentLine
        fields = [
            "id", "adjustment", "item", "uom", "lot", "bin", "quantity",
            "revaluation", "unit_cost", "notes", "item_label", "lot_code",
        ]
        read_only_fields = ["unit_cost"]


class StockAdjustmentSerializer(serializers.ModelSerializer):
    lines = StockAdjustmentLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")
    reason_name = serializers.CharField(source="reason.name", read_only=True, default="")
    total_value = serializers.SerializerMethodField()
    voided = serializers.SerializerMethodField()

    class Meta:
        model = StockAdjustment
        fields = [
            "id", "number", "adjustment_date", "warehouse", "reason", "memo",
            "posted", "posted_at", "journal_entry", "voided_entry", "voided_at",
            "count", "lines", "total_value", "voided", "warehouse_name", "reason_name",
        ]
        read_only_fields = [
            "number", "posted", "posted_at", "journal_entry", "voided_entry",
            "voided_at", "count",
        ]

    def get_total_value(self, adjustment):
        return adjustment.total_value()

    def get_voided(self, adjustment):
        return adjustment.is_voided()


class StockCountLineSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")

    def get_item_label(self, line):
        return f"{line.item.sku} · {line.item.name}"

    variance = serializers.SerializerMethodField()

    class Meta:
        model = StockCountLine
        fields = [
            "id", "count", "item", "uom", "lot", "bin", "counted_quantity",
            "system_quantity", "variance", "notes", "item_label", "lot_code",
        ]
        read_only_fields = ["system_quantity"]

    def get_variance(self, line):
        return line.variance()


class StockCountSerializer(serializers.ModelSerializer):
    lines = StockCountLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")
    reason_name = serializers.CharField(source="reason.name", read_only=True, default="")
    lines_count = serializers.SerializerMethodField()

    def get_lines_count(self, count):
        return len(count.lines.all())

    class Meta:
        model = StockCount
        fields = [
            "id", "number", "count_date", "warehouse", "reason", "memo",
            "counted_by", "posted", "posted_at", "lines", "warehouse_name", "reason_name",
            "lines_count",
        ]
        read_only_fields = ["number", "posted", "posted_at"]


class StockTransferLineSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")

    def get_item_label(self, line):
        return f"{line.item.sku} · {line.item.name}"

    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = StockTransferLine
        fields = [
            "id", "transfer", "item", "uom", "lot", "from_bin", "to_bin",
            "quantity", "notes", "outstanding", "item_label", "lot_code",
        ]

    def get_outstanding(self, line):
        return line.quantity_outstanding()


class StockTransferSerializer(serializers.ModelSerializer):
    lines = StockTransferLineSerializer(many=True, read_only=True)
    from_name = serializers.CharField(source="from_warehouse.name", read_only=True, default="")
    to_name = serializers.CharField(source="to_warehouse.name", read_only=True, default="")

    class Meta:
        model = StockTransfer
        fields = [
            "id", "number", "transfer_date", "from_warehouse", "to_warehouse",
            "transit_warehouse", "status", "reference", "memo",
            "dispatched_at", "received_at", "cancelled_at", "lines", "from_name", "to_name",
        ]
        read_only_fields = [
            "number", "status", "dispatched_at", "received_at", "cancelled_at",
        ]


class StockReservationSerializer(serializers.ModelSerializer):
    remaining = serializers.SerializerMethodField()
    item_label = serializers.SerializerMethodField()
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")
    held_for = serializers.SerializerMethodField()

    def get_item_label(self, reservation):
        return f"{reservation.item.sku} · {reservation.item.name}"

    def get_held_for(self, reservation):
        return str(reservation.source) if reservation.source is not None else ""

    class Meta:
        model = StockReservation
        fields = [
            "id", "item", "warehouse", "quantity", "consumed", "remaining",
            "released_at", "released_reason", "item_label", "warehouse_name", "held_for",
        ]
        read_only_fields = fields

    def get_remaining(self, reservation):
        return reservation.remaining()


class ItemAttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemAttributeValue
        fields = ["id", "attribute", "code", "name", "sequence", "is_active"]


class ItemAttributeSerializer(serializers.ModelSerializer):
    values = ItemAttributeValueSerializer(many=True, read_only=True)

    class Meta:
        model = ItemAttribute
        fields = ["id", "code", "name", "sequence", "is_active", "values"]


class ItemTemplateSerializer(serializers.ModelSerializer):
    variant_count = serializers.SerializerMethodField()

    class Meta:
        model = ItemTemplate
        fields = [
            "id", "code", "name", "description", "uom", "item_type",
            "track_inventory", "tracking", "costing_method",
            "inventory_account", "cogs_account", "sale_price", "attributes",
            "is_active", "variant_count",
        ]

    def get_variant_count(self, template):
        return template.variants.count()
