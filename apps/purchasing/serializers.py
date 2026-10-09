from django.db import transaction
from rest_framework import serializers

from apps.accounting.serializers import MoneyLineSerializerMixin
from apps.inventory.tracking import Lot

from .models import (
    Bill,
    BillLine,
    BillPayment,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SubcontractComponent,
)
from apps.core.customfields import ExtensibleSerializerMixin


class SubcontractComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubcontractComponent
        fields = ["id", "order_line", "item", "quantity_per", "is_computed"]
        read_only_fields = ["is_computed"]


class PurchaseOrderLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    quantity_received = serializers.SerializerMethodField()
    quantity_billed = serializers.SerializerMethodField()
    quantity_open = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    # What the line is called: its description, else its charge or item.
    label = serializers.CharField(read_only=True)
    components = SubcontractComponentSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id", "order", "item", "uom", "quantity", "unit_price",
            "discount_percent", "taxes", "expense_account", "quantity_received",
            "quantity_billed", "bom", "components", "work_order_operation", "warehouse",
            "expected_date",
            "charge", "description", "inspect_on_receipt", "quantity_open", "label",
            "closed_short_at", "closed_short_reason",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]

    def get_quantity_received(self, obj):
        return obj.quantity_received()

    def get_quantity_billed(self, obj):
        return obj.quantity_billed()


class PurchaseOrderSerializer(ExtensibleSerializerMixin, serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    # Named, so a list can say who without asking for every vendor.
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    receipt_status = serializers.CharField(read_only=True)
    bill_status = serializers.CharField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "number", "vendor", "vendor_name", "order_date", "reference", "status",
            "currency", "bill_policy", "lines",
            "payment_terms", "freight_terms", "incoterm", "port_of_loading",
            "shipping_note", "drop_ship_for", "subcontract_warehouse",
            "receipt_status", "bill_status", "subtotal", "tax_total", "total", "extra",
        ]
        # Status moves by confirm/cancel, which ask what they ask. Written
        # here, a clerk confirmed past the approval tiers. Sales always had
        # it read-only.
        read_only_fields = ["number", "status"]


class BillLineSerializer(MoneyLineSerializerMixin, serializers.ModelSerializer):
    # What the line is called: its description, else its charge or item.
    label = serializers.CharField(read_only=True)
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")

    class Meta:
        model = BillLine
        fields = [
            "id", "bill", "order_line", "item", "description", "quantity", "unit_price",
            "discount_percent", "taxes", "expense_account", "cost_centre", "cost_centre_name",
            "charge", "label",
            "gross_amount", "discount_amount", "net_amount", "tax_total", "total",
        ]


class BillPaymentSerializer(serializers.ModelSerializer):
    bill_number = serializers.CharField(source="bill.number", read_only=True)
    payment_number = serializers.CharField(source="payment.number", read_only=True)

    class Meta:
        model = BillPayment
        fields = ["id", "bill", "bill_number", "payment", "payment_number", "amount", "date"]


class BillSerializer(ExtensibleSerializerMixin, serializers.ModelSerializer):
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
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    amount_tds = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    carried = serializers.SerializerMethodField()

    def get_carried(self, bill):
        return [{"id": row.pk, "delivery": row.delivery_id, "number": row.delivery.number,
                 "date": row.delivery.delivery_date, "lr_number": row.delivery.lr_number}
                for row in bill.carried.all()]

    class Meta:
        model = Bill
        fields = [
            "id",
            "number",
            "amount_tds",
            "carried",
            "vendor",
            "vendor_name",
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
            "is_opening_balance",
            "corrects_old_supply",
            "old_bill_value", "extra",
        ]
        read_only_fields = [
            "number", "due_date", "debits", "journal_entry", "posted", "posted_at",
            "exchange_rate", "is_prepayment", "is_opening_balance", "corrects_old_supply",
            "old_bill_value",
        ]
        # Left out, the company's default payable account (Bill.save).
        extra_kwargs = {"payable_account": {"required": False}}


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    description = serializers.SerializerMethodField()
    # "lot", "serial" or "none": whether the batch must be named before
    # the receipt posts.
    tracking = serializers.CharField(source="order_line.item.tracking", read_only=True, default="none")
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")
    # The batch as it is written on the bags. The receipt is where a batch
    # enters the company, so it is named here, made if it is new.
    batch = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)

    class Meta:
        model = GoodsReceiptLine
        fields = ["id", "receipt", "order_line", "description", "warehouse", "quantity_received",
            "bin", "lot", "lot_code", "tracking", "batch",
        ]

    def get_description(self, obj):
        return obj.order_line.label()

    def validate(self, data):
        batch = data.pop("batch", None)
        self._new_batch = None
        if batch is None:
            return data
        batch = batch.strip()
        if not batch:
            data["lot"] = None
            return data
        order_line = data.get("order_line") or getattr(self.instance, "order_line", None)
        item = getattr(order_line, "item", None)
        if item is None:
            raise serializers.ValidationError({"batch": ["Only goods come in batches."]})
        lot = Lot.objects.filter(item=item, code=batch).first()
        if lot is None:
            request = self.context.get("request")
            if request is not None and not request.user.has_perm("inventory.add_lot"):
                raise serializers.ValidationError({"batch": [
                    f"There is no batch {batch} of {item} yet, and naming a new one "
                    "takes the right to add batches."]})
            # Made with the line, not before it: a line refused on saving
            # must not leave a batch behind that nothing ever received.
            self._new_batch = (item, batch)
        else:
            data["lot"] = lot
        return data

    def _with_batch(self, data):
        if getattr(self, "_new_batch", None):
            item, code = self._new_batch
            data["lot"], _ = Lot.objects.get_or_create(item=item, code=code)
        return data

    def create(self, validated_data):
        with transaction.atomic():
            return super().create(self._with_batch(validated_data))

    def update(self, instance, validated_data):
        with transaction.atomic():
            return super().update(instance, self._with_batch(validated_data))


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="purchase_order.number", read_only=True)
    vendor_name = serializers.CharField(source="purchase_order.vendor.name", read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "number",
            "purchase_order",
            "order_number",
            "vendor_name",
            "receipt_date",
            "reference",
            "reverses",
            "posted",
            "posted_at",
            "lines",
        ]
        read_only_fields = ["reverses", "posted", "posted_at"]
