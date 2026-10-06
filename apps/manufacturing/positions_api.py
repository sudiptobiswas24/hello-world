"""Machine positions through the API: kept by maintenance, each with its placements, and the critical list."""

from decimal import Decimal

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .positions import MachinePosition, critical_spares

TENTH = Decimal("0.1")


def _plain(value):
    return format(value.normalize(), "f")


class MachinePositionSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source="machine.code", read_only=True)
    spare_item_sku = serializers.CharField(source="spare_item.sku", read_only=True, default="")
    spare_item_name = serializers.CharField(source="spare_item.name", read_only=True, default="")

    class Meta:
        model = MachinePosition
        fields = ["id", "machine", "machine_code", "code", "name", "spare_item", "spare_item_sku", "spare_item_name",
                  "is_critical", "note"]


class MachinePositionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MachinePosition.objects.select_related("machine", "spare_item")
    serializer_class = MachinePositionSerializer
    filter_fields = ["machine", "is_critical", "spare_item"]
    search_fields = ["code", "name", "machine__code"]
    ordering_fields = ["machine__code", "code"]
    action_permission_map = {"history": "manufacturing.view_machineposition",
                             "critical_spares": "manufacturing.view_machineposition"}

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """What went into this position and when, and the mean days between one and the next."""
        position = self.get_object()
        life = position.life_days()
        return Response({
            "position": str(position),
            "life_days": None if life is None else life.quantize(TENTH),
            "placements": [{"on": day, "item": item.sku, "quantity": _plain(quantity), "days_since_previous": days}
                           for day, item, quantity, days in position.history()],
        })

    @action(detail=False, methods=["get"], url_path="critical-spares")
    def critical_spares(self, request):
        """Every critical position, its spare, what is on any shelf, and whether that is nothing."""
        return Response([{
            "position_id": row["position"].pk, "position": str(row["position"]), "machine": row["machine"].code,
            "item": row["item"].sku, "item_name": row["item"].name, "on_hand": _plain(row["on_hand"]),
            "short": row["short"],
            "life_days": None if row["life_days"] is None else row["life_days"].quantize(TENTH),
        } for row in critical_spares()])
