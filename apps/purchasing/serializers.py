from rest_framework import serializers

from .models import (
    Bill,
    BillLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
)


class SubcontractComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubcontractComponent
        fields = ["id", "order_line", "item", "quantity_per", "is_computed"]
        read_only_fields = ["is_computed"]


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    quantity_received = serializers.SerializerMethodField()
    quantity_billed = serializers.SerializerMethodField()
    components = SubcontractComponentSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id", "order", "item", "uom", "quantity", "unit_price",
            "discount_percent", "taxes", "expense_account", "quantity_received",
            "quantity_billed", "bom", "components", "work_order_operation",
        ]

    def get_quantity_received(self, obj):
        return obj.quantity_received()

    def get_quantity_billed(self, obj):
        return obj.quantity_billed()


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "number", "vendor", "order_date", "reference", "status",
            "currency", "bill_policy", "lines",
        ]


class BillLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillLine
        fields = [
            "id", "bill", "order_line", "item", "description", "quantity", "unit_price",
            "discount_percent", "taxes", "expense_account",
        ]


class BillSerializer(serializers.ModelSerializer):
    lines = BillLineSerializer(many=True, read_only=True)

    class Meta:
        model = Bill
        fields = [
            "id",
            "number",
            "vendor",
            "bill_date",
            "due_date",
            "currency",
            "payment_terms",
            "reference",
            "purchase_order",
            "payable_account",
            "debits",
            "journal_entry",
            "posted",
            "posted_at",
            "lines",
        ]
        read_only_fields = [
            "number", "due_date", "debits", "journal_entry", "posted", "posted_at",
        ]


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoodsReceiptLine
        fields = ["id", "receipt", "order_line", "warehouse", "quantity_received"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "number",
            "purchase_order",
            "receipt_date",
            "reference",
            "reverses",
            "posted",
            "posted_at",
            "lines",
        ]
        read_only_fields = ["reverses", "posted", "posted_at"]
