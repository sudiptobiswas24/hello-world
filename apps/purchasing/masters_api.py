"""
The purchasing masters the office keeps: agreed vendor prices, reorder
rules, budgets with what they have spent and committed, and the approval
policy with its tiers. Each was modelled, read by purchasing and
planning, and kept only in the Django admin.
"""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import ApprovalTier, Budget, PurchaseApprovalPolicy, ReorderRule, VendorPrice


class VendorPriceSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    item_label = serializers.SerializerMethodField()
    currency_code = serializers.CharField(source="currency.code", read_only=True, default="")

    class Meta:
        model = VendorPrice
        fields = [
            "id", "vendor", "item", "currency", "unit_price", "min_quantity", "vendor_item_code",
            "lead_time_days", "valid_from", "valid_to", "is_preferred", "is_active",
            "vendor_name", "item_label", "currency_code",
        ]

    def get_item_label(self, obj):
        return f"{obj.item.sku} · {obj.item.name}"


class VendorPriceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = VendorPrice.objects.select_related("vendor", "item", "currency")
    serializer_class = VendorPriceSerializer
    filter_fields = ["vendor", "item", "currency", "is_active", "is_preferred"]
    search_fields = ["item__sku", "item__name", "vendor__name", "vendor_item_code"]
    date_field = "valid_from"
    ordering_fields = ["valid_from", "unit_price"]


class ReorderRuleSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")

    class Meta:
        model = ReorderRule
        fields = [
            "id", "item", "warehouse", "minimum", "target", "multiple_of",
            "minimum_order_quantity", "maximum_order_quantity", "order_period_days", "vendor",
            "is_active", "item_label", "warehouse_name", "vendor_name",
        ]

    def get_item_label(self, obj):
        return f"{obj.item.sku} · {obj.item.name}"


class ReorderRuleViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ReorderRule.objects.select_related("item", "warehouse", "vendor")
    serializer_class = ReorderRuleSerializer
    filter_fields = ["item", "warehouse", "vendor", "is_active"]
    search_fields = ["item__sku", "item__name"]


class BudgetSerializer(serializers.ModelSerializer):
    account_label = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = ["id", "code", "name", "account", "start_date", "end_date", "amount", "is_active",
                  "account_label"]

    def get_account_label(self, obj):
        return f"{obj.account.code} · {obj.account.name}"


class BudgetViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Budget.objects.select_related("account")
    serializer_class = BudgetSerializer
    filter_fields = ["account", "is_active"]
    search_fields = ["code", "name", "account__code", "account__name"]
    date_field = "start_date"

    @action(detail=True, methods=["get"])
    def figures(self, request, pk=None):
        """
        Spent, committed and asked for, and what is left: worked out from
        the bills, orders and requisitions each time, one budget at a time.
        """
        budget = self.get_object()
        spent, committed, requested = budget.spent(), budget.committed(), budget.requested()
        return Response({
            "amount": budget.amount, "spent": spent, "committed": committed,
            "requested": requested, "available": budget.amount - spent - committed - requested,
        })


class ApprovalTierSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = ApprovalTier
        fields = ["id", "policy", "group", "up_to_amount", "group_name"]


class PurchaseApprovalPolicySerializer(serializers.ModelSerializer):
    tiers = ApprovalTierSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseApprovalPolicy
        fields = ["id", "code", "name", "max_order_value", "max_line_value",
                  "require_approval_without_vendor_price", "is_active", "tiers"]


class PurchaseApprovalPolicyViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PurchaseApprovalPolicy.objects.prefetch_related("tiers__group")
    serializer_class = PurchaseApprovalPolicySerializer
    filter_fields = ["is_active"]
    search_fields = ["code", "name"]


class ApprovalTierViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ApprovalTier.objects.select_related("policy", "group")
    serializer_class = ApprovalTierSerializer
    filter_fields = ["policy", "group"]
