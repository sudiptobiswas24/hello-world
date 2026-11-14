"""Change orders on a bill of materials: raised from the bill, applied or rejected here."""

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .changes import BomChangeOrder, raise_change


def _bom_label(bom):
    return f"{bom.item.sku} · {bom.item.name}, version {bom.version}"


class BomChangeOrderSerializer(serializers.ModelSerializer):
    item_label = serializers.SerializerMethodField()
    supersedes_label = serializers.SerializerMethodField()
    draft_label = serializers.SerializerMethodField()
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default="")

    class Meta:
        model = BomChangeOrder
        fields = ["id", "number", "supersedes", "supersedes_label", "draft", "draft_label", "item_label",
                  "effective_from", "reason", "status", "decided_at", "decided_by_name", "decision_note",
                  "created_at", "created_by_name"]
        # The versions and the day are fixed when it is raised: a wrong day is rejected and raised again.
        read_only_fields = ["number", "supersedes", "draft", "effective_from", "status", "decided_at"]

    def get_item_label(self, order):
        return f"{order.supersedes.item.sku} · {order.supersedes.item.name}"

    def get_supersedes_label(self, order):
        return _bom_label(order.supersedes)

    def get_draft_label(self, order):
        return _bom_label(order.draft)


class BomChangeOrderViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Raised with {supersedes, effective_from, reason}; then `apply` or `reject` ({note})."""

    queryset = BomChangeOrder.objects.select_related("supersedes__item", "draft__item", "decided_by", "created_by")
    serializer_class = BomChangeOrderSerializer
    filter_fields = ["status", "supersedes", "draft"]
    search_fields = ["number", "reason", "supersedes__item__sku", "supersedes__item__name"]
    date_field = "effective_from"
    ordering_fields = ["effective_from", "number"]
    action_permission_map = {
        "apply": "manufacturing.apply_bomchangeorder",
        "reject": "manufacturing.apply_bomchangeorder",
    }

    def create(self, request, *args, **kwargs):
        from .bom import BillOfMaterials
        from apps.core.api import record_or_404

        bom = record_or_404(BillOfMaterials, request.data.get("supersedes"), "supersedes")
        order = raise_change(bom, request.data.get("effective_from"), str(request.data.get("reason", "")),
                             by=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data, status=201)

    def _answer(self, order):
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        order = self.get_object()
        order.apply(by=request.user, note=str(request.data.get("note", "")))
        return self._answer(order)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        order = self.get_object()
        order.reject(by=request.user, note=str(request.data.get("note", "")))
        return self._answer(order)
