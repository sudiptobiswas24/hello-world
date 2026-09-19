from rest_framework import serializers

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine


class SalesOrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesOrderLine
        fields = ["id", "order", "item", "uom", "quantity", "unit_price"]


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = SalesOrder
        fields = ["id", "customer", "order_date", "reference", "status", "currency", "lines"]


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "invoice", "item", "description", "quantity", "unit_price", "revenue_account"]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "customer",
            "invoice_date",
            "reference",
            "sales_order",
            "receivable_account",
            "credits",
            "journal_entry",
            "posted",
            "posted_at",
            "lines",
        ]
        read_only_fields = ["credits", "journal_entry", "posted", "posted_at"]
