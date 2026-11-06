"""
Call-offs over the API: call-offs/ (filter ?line=), and on each line
sales-order-lines/{id}/schedule/ for what is still owed and by when.
"""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .call_offs import CallOff
from .scoping import CustomerScopedMixin


class CallOffSerializer(serializers.ModelSerializer):
    line_label = serializers.SerializerMethodField()

    def get_line_label(self, row):
        line = row.line
        return f"{line.order.number} · {line.order.customer.name} · {line.label()}"

    class Meta:
        model = CallOff
        fields = ["id", "line", "due_on", "quantity", "reference", "line_label"]


class CallOffViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "line__order__customer"
    queryset = CallOff.objects.select_related("line__order__customer", "line__item", "line__charge")
    serializer_class = CallOffSerializer
    filter_fields = ["line", "line__order", "line__order__customer"]
    search_fields = ["reference", "line__order__number", "line__order__customer__name", "line__item__sku"]
    date_field = "due_on"
    ordering_fields = ["due_on"]
