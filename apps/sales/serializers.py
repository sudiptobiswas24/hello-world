from rest_framework import serializers

from .models import Invoice, InvoiceLine, InvoicePayment, SalesOrder, SalesOrderLine


class MoneyLineSerializerMixin(serializers.Serializer):
    gross_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    net_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)


class SalesOrderLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = SalesOrderLine
        fields = [
            "id", "order", "item", "uom", "quantity", "unit_price", "discount_percent",
            "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id", "number", "customer", "order_date", "reference", "status", "currency",
            "payment_terms", "billing_address", "shipping_address",
            "lines", "subtotal", "tax_total", "total",
        ]
        read_only_fields = ["number", "status"]


class InvoiceLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id", "invoice", "item", "description", "quantity", "unit_price",
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
