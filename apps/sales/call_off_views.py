"""
Call-offs over the API: call-offs/ (filter ?line=), and on each line
sales-order-lines/{id}/schedule/ for what is still owed and by when.
"""

from rest_framework import serializers, viewsets

from apps.core.audit import AuditableViewSetMixin

from .call_offs import CallOff
from .scoping import CustomerScopedMixin


class CallOffSerializer(serializers.ModelSerializer):
    class Meta:
        model = CallOff
        fields = ["id", "line", "due_on", "quantity", "reference"]


class CallOffViewSet(CustomerScopedMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    customer_path = "line__order__customer"
    queryset = CallOff.objects.select_related("line")
    serializer_class = CallOffSerializer

    def get_queryset(self):
        rows = super().get_queryset()
        line = self.request.query_params.get("line")
        return rows.filter(line_id=line) if line else rows
