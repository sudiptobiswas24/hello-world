from rest_framework import serializers

from .models import (
    CustomerProfile,
    Delivery,
    DunningLevel,
    DunningNotice,
    DeliveryLine,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    PriceList,
    PriceListItem,
    Quotation,
    QuotationLine,
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
            "id", "invoice", "order_line", "credits_line", "item", "description",
            "quantity", "unit_price",
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
            "amount_paid", "amount_credited", "amount_due", "settlement_status", "sent_at",
        ]
        read_only_fields = [
            "number", "due_date", "exchange_rate", "credits", "journal_entry",
            "posted", "posted_at", "sent_at",
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


class PriceListItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceListItem
        fields = ["id", "price_list", "item", "min_quantity", "unit_price"]


class PriceListSerializer(serializers.ModelSerializer):
    entries = PriceListItemSerializer(many=True, read_only=True)

    class Meta:
        model = PriceList
        fields = [
            "id", "code", "name", "currency", "is_default",
            "valid_from", "valid_to", "is_active", "entries",
        ]


class CustomerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = ["id", "party", "price_list", "credit_limit"]


class QuotationLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = QuotationLine
        fields = [
            "id", "quotation", "item", "uom", "quantity", "unit_price",
            "discount_percent", "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class QuotationSerializer(serializers.ModelSerializer):
    lines = QuotationLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id", "number", "customer", "quotation_date", "valid_until", "reference",
            "status", "currency", "payment_terms", "billing_address", "shipping_address",
            "sales_order", "sent_at", "lines", "subtotal", "tax_total", "total",
        ]
        read_only_fields = ["number", "status", "sales_order", "sent_at"]


class DunningLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DunningLevel
        fields = ["id", "name", "days_overdue", "subject", "body", "is_active"]


class DunningNoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DunningNotice
        fields = ["id", "invoice", "level", "days_overdue", "amount_due", "sent_to", "sent_at"]
        read_only_fields = fields
