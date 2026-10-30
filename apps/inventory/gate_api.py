"""
The gate's API (L17, L18): the register of vehicles, their weighings, and gate passes.

The Security Guard reads only what the gate needs and sees no prices: a pass's lines name an item
or an asset by its code and name, and the pickers below answer with nothing more, so the guard
holds no right to read the item master or the asset register, both of which carry money.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import flag, record_or_404
from apps.core.audit import AuditableViewSetMixin

from .gate import (
    GatePass, GatePassLine, GatePassReturn, GateVisit, Weighing, WeightSource, open_too_long, overdue_passes,
)
from .models import Item


class WeighingSerializer(serializers.ModelSerializer):
    net_kg = serializers.SerializerMethodField()
    typed = serializers.SerializerMethodField()
    vehicle_number = serializers.CharField(source="visit.vehicle_number", read_only=True)
    visit_number = serializers.CharField(source="visit.number", read_only=True)
    replaced_by = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField()

    class Meta:
        model = Weighing
        fields = ["id", "number", "visit", "visit_number", "vehicle_number", "scale_code",
                  "gross_kg", "gross_at", "gross_source", "gross_reason", "gross_note",
                  "tare_kg", "tare_at", "tare_source", "tare_reason", "tare_note",
                  "net_kg", "typed", "replaced_at", "replace_reason", "replaces", "replaced_by", "state"]
        read_only_fields = fields

    def get_net_kg(self, weighing):
        return weighing.net_kg()

    def get_typed(self, weighing):
        """Each side typed, so a typed weight shows as typed wherever the weighing does."""
        return {"gross": weighing.gross_source == WeightSource.TYPED,
                "tare": weighing.tare_source == WeightSource.TYPED, "any": weighing.typed()}

    def get_replaced_by(self, weighing):
        following = Weighing.objects.filter(replaces=weighing).first()
        return following.pk if following else None

    def get_state(self, weighing):
        if weighing.visit.status == "cancelled":
            return "cancelled"
        if weighing.replaced_at:
            return "replaced"
        return "weighed" if weighing.is_complete() else "weighing"


class GateVisitSerializer(serializers.ModelSerializer):
    weighings = WeighingSerializer(many=True, read_only=True)
    passes = serializers.SerializerMethodField()

    class Meta:
        model = GateVisit
        fields = ["id", "number", "vehicle_number", "driver_name", "driver_mobile", "transporter_name", "purpose",
                  "reference", "carried", "status", "in_at", "out_at", "cancelled_at", "cancel_reason",
                  "weighings", "passes"]
        read_only_fields = ["number", "status", "in_at", "out_at", "cancelled_at", "cancel_reason"]

    def get_passes(self, visit):
        return [{"id": each.pk, "number": each.number, "status": each.status} for each in visit.gate_passes.all()]


class GatePassReturnSerializer(serializers.ModelSerializer):
    class Meta:
        model = GatePassReturn
        fields = ["id", "line", "quantity", "returned_on", "condition", "note", "visit", "cancelled_at",
                  "cancel_reason"]
        read_only_fields = fields


class GatePassLineSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    returned = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    returns = GatePassReturnSerializer(many=True, read_only=True)

    class Meta:
        model = GatePassLine
        fields = ["id", "gate_pass", "item", "asset", "description", "quantity", "movement", "label", "returned",
                  "outstanding", "returns"]

    def get_label(self, line):
        return line.label()

    def get_returned(self, line):
        return line.returned()

    def get_outstanding(self, line):
        return line.outstanding()


class GatePassSerializer(serializers.ModelSerializer):
    lines = GatePassLineSerializer(many=True, read_only=True)
    visit_number = serializers.CharField(source="visit.number", read_only=True, default="")
    vehicle_number = serializers.CharField(source="visit.vehicle_number", read_only=True, default="")
    approved_by_name = serializers.SerializerMethodField()
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = GatePass
        fields = ["id", "number", "kind", "pass_date", "party_name", "purpose", "expected_return_date", "visit",
                  "visit_number", "vehicle_number", "status", "approved_by", "approved_by_name", "approved_at",
                  "out_at", "cancelled_at", "cancel_reason", "overdue", "lines"]
        read_only_fields = ["number", "status", "approved_by", "approved_at", "out_at", "cancelled_at",
                            "cancel_reason"]

    def get_approved_by_name(self, gate_pass):
        return gate_pass.approved_by.get_username() if gate_pass.approved_by_id else ""

    def get_overdue(self, gate_pass):
        return gate_pass.is_overdue()


class GateVisitViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GateVisit.objects.prefetch_related("weighings", "gate_passes")
    serializer_class = GateVisitSerializer
    http_method_names = ["get", "post", "patch", "put", "head", "options"]
    filter_fields = ["status", "purpose"]
    extra_params = ["open_too_long"]
    search_fields = ["number", "vehicle_number", "driver_name", "transporter_name", "reference"]
    ordering_fields = ["in_at", "number"]
    def get_queryset(self):
        rows = super().get_queryset()
        if flag(self.request.query_params, "open_too_long", False):
            rows = rows.filter(pk__in=open_too_long().values("pk"))
        return rows

    action_permission_map = {
        "leave": "inventory.change_gatevisit",
        "cancel": "inventory.change_gatevisit",
        "weigh": "inventory.add_weighing",
    }

    # Not check_out(): the URL says what the guard does, the vehicle leaves.
    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        visit = self.get_object()
        visit.check_out()
        return Response(self.get_serializer(visit).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        visit = self.get_object()
        visit.cancel(reason=request.data.get("reason") or "")
        return Response(self.get_serializer(visit).data)

    @action(detail=True, methods=["post"])
    def weigh(self, request, pk=None):
        """A weighing of this vehicle on {scale_code}, the weighbridge's scale."""
        visit = self.get_object()
        weighing = visit.start_weighing(scale_code=request.data.get("scale_code") or "")
        return Response(WeighingSerializer(weighing).data, status=201)


class WeighingViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Weighing.objects.select_related("visit")
    serializer_class = WeighingSerializer
    filter_fields = ["visit"]
    search_fields = ["number", "visit__vehicle_number", "visit__number"]
    ordering_fields = ["number"]
    action_permission_map = {
        "record": "inventory.change_weighing",
        "replace": "inventory.change_weighing",
    }

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        """{side: gross|tare, source: scale|typed, kg, reason, note}; typed needs inventory.type_weight."""
        weighing = self.get_object()
        weighing.record(request.data.get("side"), request.data.get("source"), kg=request.data.get("kg"),
                        reason=request.data.get("reason") or "", note=request.data.get("note") or "",
                        by=request.user)
        return Response(self.get_serializer(weighing).data)

    @action(detail=True, methods=["post"])
    def replace(self, request, pk=None):
        weighing = self.get_object()
        following = weighing.replace(reason=request.data.get("reason") or "")
        return Response(self.get_serializer(following).data, status=201)


class GatePassViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GatePass.objects.select_related("visit", "approved_by").prefetch_related(
        "lines__item", "lines__asset", "lines__returns")
    serializer_class = GatePassSerializer
    http_method_names = ["get", "post", "patch", "put", "head", "options"]
    filter_fields = ["status", "kind", "visit"]
    extra_params = ["overdue"]
    search_fields = ["number", "party_name", "purpose"]
    date_field = "pass_date"
    ordering_fields = ["pass_date", "number", "expected_return_date"]
    action_permission_map = {
        "approve": "inventory.approve_gatepass",
        "cancel": "inventory.change_gatepass",
        "returned": "inventory.add_gatepassreturn",
        "cancel_return": "inventory.change_gatepassreturn",
    }

    def get_queryset(self):
        rows = super().get_queryset()
        if flag(self.request.query_params, "overdue", False):
            rows = rows.filter(pk__in=overdue_passes().values("pk"))
        return rows

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        gate_pass = self.get_object()
        gate_pass.approve(by=request.user)
        return Response(self.get_serializer(gate_pass).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        gate_pass = self.get_object()
        gate_pass.cancel(reason=request.data.get("reason") or "")
        return Response(self.get_serializer(gate_pass).data)

    @action(detail=True, methods=["post"])
    def returned(self, request, pk=None):
        """What came back: {line, quantity, returned_on, condition: good|damaged, note, visit}."""
        gate_pass = self.get_object()
        line = get_object_or_404(GatePassLine, pk=request.data.get("line"), gate_pass=gate_pass)
        visit = record_or_404(GateVisit, request.data.get("visit"), "visit", optional=True)
        gate_pass.record_return(line, request.data.get("quantity") or "0", request.data.get("returned_on") or None,
                                request.data.get("condition") or "good", request.data.get("note") or "", visit)
        # Read again: the returns were prefetched before this one was recorded.
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="cancel-return")
    def cancel_return(self, request, pk=None):
        gate_pass = self.get_object()
        entry = get_object_or_404(GatePassReturn, pk=request.data.get("entry"), line__gate_pass=gate_pass)
        gate_pass.cancel_return(entry, reason=request.data.get("reason") or "")
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=["get"])
    def items(self, request):
        """The items a pass may name, by code and name only: the gate sees no prices."""
        text = (request.query_params.get("search") or "").strip()
        rows = Item.objects.filter(is_active=True)
        if text:
            rows = rows.filter(Q(sku__icontains=text) | Q(name__icontains=text))
        return Response([{"id": row.pk, "sku": row.sku, "name": row.name, "uom": str(row.uom)}
                         for row in rows.select_related("uom").order_by("sku")[:50]])

    @action(detail=False, methods=["get"])
    def assets(self, request):
        """The assets a pass may name, by number and name only."""
        from apps.assets.models import AssetStatus, FixedAsset

        text = (request.query_params.get("search") or "").strip()
        rows = FixedAsset.objects.filter(status=AssetStatus.IN_SERVICE)
        if text:
            rows = rows.filter(Q(number__icontains=text) | Q(name__icontains=text))
        return Response([{"id": row.pk, "number": row.number, "name": row.name}
                         for row in rows.order_by("number")[:50]])


class GatePassLineViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = GatePassLine.objects.select_related("gate_pass", "item", "asset")
    serializer_class = GatePassLineSerializer
