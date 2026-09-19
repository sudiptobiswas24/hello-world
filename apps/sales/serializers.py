from rest_framework import serializers

from .models import (
    Delivery,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    SalesOrder,
    SalesOrderLine,
)


class MoneyLineSerializerMixin(serializers.Serializer):
    gross_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    net_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)


class SalesOrderLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    quantity_shipped = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    quantity_invoiced = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    quantity_uninvoiced = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            "id", "order", "item", "uom", "quantity", "unit_price", "discount_percent",
            "revenue_account", "taxes", "quantity_shipped", "quantity_invoiced",
            "quantity_uninvoiced",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    invoice_status = serializers.CharField(read_only=True)
    delivery_status = serializers.CharField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id", "number", "customer", "order_date", "reference", "status", "currency",
            "payment_terms", "billing_address", "shipping_address",
            "lines", "subtotal", "tax_total", "total",
            "invoice_status", "delivery_status",
        ]
        read_only_fields = ["number", "status"]


class InvoiceLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id", "invoice", "order_line", "item", "description", "quantity", "unit_price",
            "discount_percent", "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_paid = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_credited = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_due = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    settlement_status = serializers.CharField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "customer", "invoice_date", "due_date", "reference",
            "sales_order", "receivable_account", "currency", "exchange_rate",
            "payment_terms", "billing_address", "shipping_address",
            "credits", "journal_entry", "posted", "posted_at",
            "lines", "subtotal", "tax_total", "total",
            "amount_paid", "amount_credited", "amount_due", "settlement_status",
        ]
        read_only_fields = [
            "number", "due_date", "exchange_rate", "credits", "journal_entry",
            "posted", "posted_at",
        ]


class InvoicePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoicePayment
        fields = ["id", "invoice", "payment", "amount"]


class DeliveryLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryLine
        fields = ["id", "delivery", "order_line", "warehouse", "quantity_shipped"]


class DeliverySerializer(serializers.ModelSerializer):
    lines = DeliveryLineSerializer(many=True, read_only=True)

    class Meta:
        model = Delivery
        fields = [
            "id", "number", "sales_order", "delivery_date", "reference",
            "shipping_address", "reverses", "posted", "posted_at", "lines",
        ]
        read_only_fields = ["number", "reverses", "posted", "posted_at"]
