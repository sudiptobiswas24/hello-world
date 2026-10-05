from rest_framework import serializers

from .models import (
    BillPayment,
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
            "quantity_billed", "bom", "components", "work_order_operation", "warehouse",
            "expected_date",
            "charge", "description", "inspect_on_receipt",
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
            "shipping_note", "drop_ship_for", "subcontract_warehouse",
        ]
        # Status moves by confirm/cancel, which ask what they ask. Written
        # here, a clerk confirmed past the approval tiers. Sales always had
        # it read-only.
        read_only_fields = ["number", "status"]


class BillLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillLine
        fields = [
            "id", "bill", "order_line", "item", "description", "quantity", "unit_price",
            "discount_percent", "taxes", "expense_account",
            "charge",
        ]


class BillPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillPayment
        fields = ["id", "bill", "payment", "amount"]


class BillSerializer(serializers.ModelSerializer):
    lines = BillLineSerializer(many=True, read_only=True)
    # What is owed on it, as the invoice has always said: without these an
    # AP clerk could post a bill and not read back what it came to.
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_paid = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_debited = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_due = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    settlement_status = serializers.CharField(read_only=True)

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
            "exchange_rate",
            "subtotal",
            "tax_total",
            "total",
            "amount_paid",
            "amount_debited",
            "amount_due",
            "settlement_status",
            "is_prepayment",
        ]
        read_only_fields = [
            "number", "due_date", "debits", "journal_entry", "posted", "posted_at",
            "exchange_rate", "is_prepayment",
        ]
        # Left out, the company's default payable account (Bill.save).
        extra_kwargs = {"payable_account": {"required": False}}


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = GoodsReceiptLine
        fields = ["id", "receipt", "order_line", "warehouse", "quantity_received",
            "bin", "lot",
        ]


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
