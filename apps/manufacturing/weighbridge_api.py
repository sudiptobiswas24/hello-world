"""
A weighbridge slip's weight from the scale (L17): taken here, beside the scale bridge's readings, so
the reading is held and marked used in the module that makes readings, and inventory never reaches
into manufacturing.
"""

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.inventory.gate_api import WeighingSerializer
from apps.inventory.models import Weighing

from .station_scale import weigh_from_bridge


class WeighbridgeViewSet(AuditableViewSetMixin, viewsets.GenericViewSet):
    queryset = Weighing.objects.select_related("visit")
    serializer_class = WeighingSerializer
    action_permission_map = {"from_scale": "inventory.change_weighing"}

    @action(detail=True, methods=["post"], url_path="from-scale")
    def from_scale(self, request, pk=None):
        """{side: gross|tare}: the weighbridge scale's latest settled reading, held and marked used."""
        weighing = self.get_object()
        weigh_from_bridge(weighing, request.data.get("side"))
        return Response(self.get_serializer(Weighing.objects.get(pk=weighing.pk)).data)
