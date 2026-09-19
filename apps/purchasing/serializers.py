from rest_framework import serializers

from .models import Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    quantity_received = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "order", "item", "uom", "quantity", "unit_price", "quantity_received"]

    def get_quantity_received(self, obj):
        return obj.quantity_received()


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "number", "vendor", "order_date", "reference", "status",
            "currency", "lines",
        ]


class BillLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillLine
        fields = ["id", "bill", "item", "description", "quantity", "unit_price", "expense_account"]


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
