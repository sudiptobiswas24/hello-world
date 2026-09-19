from rest_framework import serializers

from .models import Bill, BillLine, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "order", "item", "uom", "quantity", "unit_price"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "vendor", "order_date", "reference", "status", "currency", "lines"]


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
            "vendor",
            "bill_date",
            "reference",
            "purchase_order",
            "payable_account",
            "debits",
            "journal_entry",
            "posted",
            "posted_at",
            "lines",
        ]
        read_only_fields = ["debits", "journal_entry", "posted", "posted_at"]
