from rest_framework import serializers

from apps.accounting.serializers import MoneyLineSerializerMixin

from .models import (
    SuppliedItem,
    ThirdPartyRelease,
    ThirdPartyReleaseLine,
    CommissionPlan,
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
    RecurringInvoice,
    RecurringInvoiceLine,
    SalesOrder,
    SalesOrderLine,
    SalesRep,
)


class SalesOrderLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    # What the line is called: its description, else its charge or item.
    label = serializers.CharField(read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    customer_name = serializers.CharField(source="order.customer.name", read_only=True)
    quantity_shipped = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    quantity_invoiced = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    quantity_uninvoiced = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    quantity_open = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            "id", "order", "item", "uom", "quantity", "unit_price", "discount_percent",
            "revenue_account", "taxes", "quantity_shipped", "quantity_invoiced",
            "quantity_uninvoiced", "over_delivery_percent", "under_delivery_percent",
            "quantity_open", "closed_short_at", "closed_short_reason",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
            "charge", "description", "warehouse", "delivery_date", "label",
            "order_number", "customer_name",
        ]


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    # Named, so a list can say who without asking for every customer.
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_status = serializers.CharField(read_only=True)
    delivery_status = serializers.CharField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id", "number", "customer", "customer_name", "order_date", "reference", "status", "currency",
            "payment_terms", "billing_address", "shipping_address", "sales_rep",
            "lines", "subtotal", "tax_total", "total",
            "invoice_status", "delivery_status", "is_job_work", "supplied_items",
            "third_party_inspection",
            "invoice_policy",
        ]
        read_only_fields = ["number", "status"]

    supplied_items = serializers.SerializerMethodField()

    def get_supplied_items(self, obj):
        return [row.item_id for row in obj.supplied_items.all()]


class SuppliedItemSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    item_label = serializers.SerializerMethodField()

    def get_item_label(self, row):
        return f"{row.item.sku} · {row.item.name}"

    class Meta:
        model = SuppliedItem
        fields = ["id", "order", "item", "order_number", "item_label"]
        validators = []


class InvoiceLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    # What the line is called: its description, else its charge or item.
    label = serializers.CharField(read_only=True)
    class Meta:
        model = InvoiceLine
        fields = [
            "id", "invoice", "order_line", "credits_line", "item", "description",
            "quantity", "unit_price",
            "discount_percent", "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
            "charge", "label",
        ]
        # Left out, the charge's account or the company's default revenue
        # account (InvoiceLine.save).
        extra_kwargs = {"revenue_account": {"required": False}}


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
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
            "id", "number", "customer", "customer_name", "invoice_date", "due_date", "reference",
            "sales_order", "receivable_account", "currency", "exchange_rate",
            "payment_terms", "billing_address", "shipping_address", "sales_rep",
            "credits", "journal_entry", "posted", "posted_at",
            "lines", "subtotal", "tax_total", "total",
            "amount_paid", "amount_credited", "amount_due", "settlement_status", "sent_at",
            "is_down_payment", "is_opening_balance", "corrects_old_supply", "old_invoice_value",
            "party_gstin",
        ]
        read_only_fields = [
            "number", "due_date", "exchange_rate", "credits", "journal_entry",
            "posted", "posted_at", "sent_at", "is_down_payment", "is_opening_balance",
            "corrects_old_supply", "old_invoice_value", "party_gstin",
        ]
        # Left out, the company's default receivable account (Invoice.save).
        extra_kwargs = {"receivable_account": {"required": False}}


class InvoicePaymentSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    payment_number = serializers.CharField(source="payment.number", read_only=True)

    class Meta:
        model = InvoicePayment
        fields = ["id", "invoice", "invoice_number", "payment", "payment_number", "amount"]


class DeliveryLineSerializer(serializers.ModelSerializer):
    # What the line is, as the order line says it: a delivery line names
    # only the order line, and a screen needs to say "50 kg sacks".
    description = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryLine
        fields = ["id", "delivery", "order_line", "description", "warehouse", "quantity_shipped",
            "bin", "lot",
        ]

    def get_description(self, obj):
        return obj.order_line.description or obj.order_line.label()


class BackorderMixin(serializers.Serializer):
    pass


class DeliverySerializer(serializers.ModelSerializer):
    lines = DeliveryLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="sales_order.customer.name", read_only=True)
    order_number = serializers.CharField(source="sales_order.number", read_only=True)

    class Meta:
        model = Delivery
        fields = [
            "id", "number", "sales_order", "order_number", "customer_name", "delivery_date",
            "reference", "shipping_address", "reverses", "backorder_of", "posted", "posted_at", "lines",
            "returned_under_release",
        ]
        read_only_fields = ["number", "reverses", "backorder_of", "posted", "posted_at",
                            "returned_under_release"]


class PriceListItemSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()

    def get_item_label(self, row):
        return f"{row.item.sku} · {row.item.name}"

    class Meta:
        model = PriceListItem
        fields = ["id", "price_list", "item", "min_quantity", "unit_price", "item_label"]


class PriceListSerializer(serializers.ModelSerializer):
    entries = PriceListItemSerializer(many=True, read_only=True)

    class Meta:
        model = PriceList
        fields = [
            "id", "code", "name", "currency", "is_default",
            "valid_from", "valid_to", "is_active", "entries",
        ]


class CustomerProfileSerializer(serializers.ModelSerializer):
    sales_rep_name = serializers.CharField(source="sales_rep.name", read_only=True, default="")
    price_list_name = serializers.CharField(source="price_list.name", read_only=True, default="")

    class Meta:
        model = CustomerProfile
        fields = ["id", "party", "price_list", "price_list_name", "credit_limit", "virgin_only",
                  "max_filler_percent", "min_uv_percent", "third_party_inspection",
                  "release_covers_returns",
            "over_delivery_percent", "under_delivery_percent", "sales_rep", "sales_rep_name",
        ]


class QuotationLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    # What the line is called: its description, else its charge or item.
    label = serializers.CharField(read_only=True)
    class Meta:
        model = QuotationLine
        fields = [
            "id", "quotation", "item", "uom", "quantity", "unit_price",
            "discount_percent", "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
            "charge", "description", "label",
        ]


class QuotationSerializer(serializers.ModelSerializer):
    lines = QuotationLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id", "number", "customer", "customer_name", "quotation_date", "valid_until",
            "reference", "status", "currency", "payment_terms", "billing_address", "shipping_address",
            "sales_rep", "sales_order", "sent_at", "revision", "revision_of",
            "lines", "subtotal", "tax_total", "total",
        ]
        read_only_fields = [
            "number", "status", "sales_order", "sent_at", "revision", "revision_of",
        ]


class DunningLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DunningLevel
        fields = ["id", "name", "days_overdue", "subject", "body", "is_active"]


class DunningNoticeSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)

    class Meta:
        model = DunningNotice
        fields = ["id", "invoice", "level", "days_overdue", "amount_due", "sent_to", "sent_at",
                  "invoice_number", "customer_name", "level_name"]
        read_only_fields = fields


class CommissionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionPlan
        fields = ["id", "code", "name", "percent", "basis", "is_active"]


class SalesRepSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="party.name", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True, default="")

    class Meta:
        model = SalesRep
        fields = ["id", "party", "name", "plan", "is_active", "plan_name"]


class RecurringInvoiceLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = RecurringInvoiceLine
        fields = [
            "id", "schedule", "item", "description", "quantity", "unit_price",
            "discount_percent", "revenue_account", "taxes",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class RecurringInvoiceSerializer(serializers.ModelSerializer):
    lines = RecurringInvoiceLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = RecurringInvoice
        fields = [
            "id", "code", "customer", "receivable_account", "currency", "payment_terms",
            "sales_rep", "interval", "interval_count", "start_date", "end_date",
            "next_run_date", "auto_post", "is_active", "lines", "customer_name",
        ]
        read_only_fields = ["next_run_date"]


class ThirdPartyReleaseLineSerializer(serializers.ModelSerializer):
    lot_code = serializers.CharField(source="lot.code", read_only=True)

    class Meta:
        model = ThirdPartyReleaseLine
        fields = ["id", "lot", "quantity_offered", "quantity_released", "remarks", "lot_code"]


class ThirdPartyReleaseSerializer(serializers.ModelSerializer):
    lines = ThirdPartyReleaseLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    agency_name = serializers.CharField(source="agency.name", read_only=True, default="")
    order_number = serializers.CharField(source="sales_order.number", read_only=True, default="")

    class Meta:
        model = ThirdPartyRelease
        fields = ["id", "number", "customer", "sales_order", "agency", "inspector",
                  "their_reference", "inspected_on", "posted", "posted_at", "voided_at",
                  "voided_reason", "lines", "customer_name", "agency_name", "order_number"]
        read_only_fields = fields
