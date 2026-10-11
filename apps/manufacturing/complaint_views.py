"""
Complaints and their corrective actions over the API.

POST complaints/ opens one; {id}/lots/ {lot, quantity?} names a batch
shipped to that customer; {id}/investigation/ is the genealogy and who
else holds the batches; {id}/close/ {root_cause, by}, {id}/reject/
{reason, by} and {id}/reopen/ {reason} decide it. Actions are
corrective-actions/, with {id}/done/ {note} and {id}/verify/ {by};
corrective-actions/overdue/ lists those late on open complaints.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .alerts import QualityAlert
from .complaints import Complaint, CorrectiveAction, complaints_by, overdue_actions


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


def _employee(pk):
    from apps.hr.models import Employee

    if not pk:
        raise DRFValidationError(["Say who: by is an employee."])
    return get_object_or_404(Employee, pk=pk)


class CorrectiveActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorrectiveAction
        fields = ["id", "complaint", "alert", "kind", "description", "owner", "due_on", "done_on",
                  "done_note", "verified_on", "verified_by"]
        read_only_fields = ["done_on", "done_note", "verified_on", "verified_by"]


class ComplaintSerializer(serializers.ModelSerializer):
    lots = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    actions = CorrectiveActionSerializer(many=True, read_only=True)

    class Meta:
        model = Complaint
        fields = ["id", "number", "customer", "received_on", "category", "description",
                  "quantity_affected", "status", "root_cause", "decided_on", "decided_by",
                  "rejection_reason", "reopened_reason", "lots", "actions", "customer_name", "settled",
                  "cost"]
        read_only_fields = ["number", "status", "root_cause", "decided_on", "decided_by",
                            "rejection_reason", "reopened_reason"]

    settled = serializers.SerializerMethodField()
    cost = serializers.SerializerMethodField()

    def get_settled(self, obj):
        return [{"note": row.credit_note_id, "number": row.credit_note.number, "net": row.credit_note.subtotal(),
                 "reason": row.credit_note.claim_reason} for row in obj.settlements.all()]

    def get_cost(self, obj):
        return obj.cost()

    def get_lots(self, obj):
        return [{"lot": row.lot.code, "item": row.lot.item.sku,
                 "quantity": None if row.quantity is None else str(row.quantity)}
                for row in obj.complained_lots.select_related("lot__item")]


class ComplaintViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Complaint.objects.select_related("customer").prefetch_related("actions", "settlements__credit_note__lines")
    serializer_class = ComplaintSerializer
    filter_fields = ["status", "category", "customer"]
    search_fields = ["number", "customer__name", "description"]
    date_field = "received_on"
    ordering_fields = ["received_on", "number"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    action_permission_map = {
        "lots": "manufacturing.change_complaint",
        "close": "manufacturing.change_complaint",
        "reject": "manufacturing.change_complaint",
        "reopen": "manufacturing.change_complaint",
        # Giving money back is accounts', whoever investigated.
        "settle": "sales.post_invoice",
    }

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        """Money given back for it: {invoice, net, reason, memo}, a claim credit note on that invoice."""
        from apps.core.api import money_amount, record_or_404
        from apps.sales.models import Invoice

        complaint = self.get_object()
        invoice = record_or_404(Invoice, request.data.get("invoice"), "invoice")
        net = money_amount(request.data, "net")
        if net is None:
            raise DRFValidationError({"net": ["Say how much, before tax, is given back."]})
        _run(complaint.settle, invoice, net, request.data.get("reason") or "", request.data.get("memo") or "")
        complaint.refresh_from_db()
        return Response(self.get_serializer(complaint).data)

    @action(detail=True, methods=["post"])
    def lots(self, request, pk=None):
        from apps.inventory.models import Lot

        complaint = self.get_object()
        lot = get_object_or_404(Lot, code=request.data.get("lot"))
        _run(complaint.add_lot, lot, request.data.get("quantity"))
        return Response(self.get_serializer(complaint).data, status=201)

    @action(detail=True, methods=["get"])
    def investigation(self, request, pk=None):
        found = self.get_object().investigation()
        return Response({
            "made_from": {code: [{"level": row["level"], "lot": row["lot"].code,
                                  "made_by": row["made_by"].number,
                                  "from_lot": row["from_lot"].code if row["from_lot"] else None,
                                  "from_item": row["from_item"].sku,
                                  "quantity": str(row["quantity"])} for row in rows]
                          for code, rows in found["made_from"].items()},
            "also_held_by": [{"customer": row["customer"].code, "lot": row["lot"].code,
                              "quantity": str(row["quantity"]),
                              "deliveries": row["deliveries"]}
                             for row in found["also_held_by"]],
            "tape_settings": [{"run": row.work_order.number, "machine": row.machine.code if row.machine else "",
                               "recorded_at": row.recorded_at, "draw_ratio": row.draw_ratio,
                               "quench_temperature_c": row.quench_temperature_c,
                               "oven_temperature_c": row.oven_temperature_c, "note": row.note}
                              for row in found["tape_settings"]],
        })

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        complaint = self.get_object()
        _run(complaint.close, request.data.get("root_cause", ""),
             _employee(request.data.get("by")), request.data.get("on_date"))
        return Response(self.get_serializer(complaint).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        complaint = self.get_object()
        _run(complaint.reject, request.data.get("reason", ""),
             _employee(request.data.get("by")), request.data.get("on_date"))
        return Response(self.get_serializer(complaint).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        complaint = self.get_object()
        _run(complaint.reopen, request.data.get("reason", ""))
        return Response(self.get_serializer(complaint).data)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        """?start&end&by=category|customer"""
        start = parse_date(request.query_params.get("start") or "")
        end = parse_date(request.query_params.get("end") or "")
        if start is None or end is None:
            raise DRFValidationError(["start and end are required, as YYYY-MM-DD."])
        rows = _run(complaints_by, start, end, request.query_params.get("by", "category"))
        return Response([{"key": key, "complaints": count} for key, count in rows])


class CorrectiveActionViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = CorrectiveAction.objects.select_related("complaint", "alert", "owner")
    serializer_class = CorrectiveActionSerializer
    filter_fields = ["complaint", "alert", "kind", "done_on__isnull"]
    http_method_names = ["get", "post", "delete", "head", "options"]
    action_permission_map = {"done": "manufacturing.change_correctiveaction",
                             "verify": "manufacturing.change_correctiveaction"}

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        row = self.get_object()
        _run(row.done, request.data.get("note", ""), request.data.get("on_date"))
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        row = self.get_object()
        _run(row.verify, _employee(request.data.get("by")), request.data.get("on_date"))
        return Response(self.get_serializer(row).data)

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        on_date = parse_date(request.query_params.get("on_date") or "") or None
        return Response(self.get_serializer(overdue_actions(on_date), many=True).data)


# --- quality alerts: what the plant finds before a customer does

class QualityAlertSerializer(serializers.ModelSerializer):
    actions = CorrectiveActionSerializer(many=True, read_only=True)
    raised_by_name = serializers.CharField(source="raised_by.party.name", read_only=True, default="")
    owner_name = serializers.CharField(source="owner.party.name", read_only=True, default="")
    work_centre_code = serializers.CharField(source="work_centre.code", read_only=True, default="")
    machine_code = serializers.CharField(source="machine.code", read_only=True, default="")
    item_label = serializers.SerializerMethodField()
    lot_code = serializers.CharField(source="lot.code", read_only=True, default="")
    work_order_number = serializers.CharField(source="work_order.number", read_only=True, default="")
    where = serializers.CharField(read_only=True)

    class Meta:
        model = QualityAlert
        fields = ["id", "number", "raised_on", "raised_by", "raised_by_name", "title", "description", "severity",
                  "status", "work_centre", "work_centre_code", "machine", "machine_code", "item", "item_label",
                  "lot", "lot_code", "work_order", "work_order_number", "quantity_affected", "owner", "owner_name",
                  "root_cause", "decided_on", "decided_by", "cancelled_reason", "reopened_reason", "where",
                  "actions", "created_at"]
        read_only_fields = ["number", "status", "root_cause", "decided_on", "decided_by", "cancelled_reason",
                            "reopened_reason"]
        # Left out, today (QualityAlert.save).
        extra_kwargs = {"raised_on": {"required": False, "allow_null": True}}

    def get_item_label(self, alert):
        return f"{alert.item.sku} · {alert.item.name}" if alert.item_id else ""


class QualityAlertViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Raised by anyone on the floor; {id}/close/ {root_cause, by}, {id}/cancel/ {reason, by}, {id}/reopen/ {reason}."""

    queryset = QualityAlert.objects.select_related(
        "work_centre", "machine", "item", "lot", "work_order", "owner__party", "raised_by__party",
    ).prefetch_related("actions")
    serializer_class = QualityAlertSerializer
    filter_fields = ["status", "severity", "work_centre", "machine", "item", "lot", "work_order", "owner"]
    search_fields = ["number", "title", "description"]
    date_field = "raised_on"
    ordering_fields = ["raised_on", "number", "severity"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    action_permission_map = {
        "close": "manufacturing.change_qualityalert",
        "cancel": "manufacturing.change_qualityalert",
        "reopen": "manufacturing.change_qualityalert",
    }

    def _answer(self, alert):
        return Response(self.get_serializer(self.get_queryset().get(pk=alert.pk)).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        alert = self.get_object()
        _run(alert.close, request.data.get("root_cause", ""), _employee(request.data.get("by")),
             request.data.get("on_date"))
        return self._answer(alert)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        alert = self.get_object()
        _run(alert.cancel, request.data.get("reason", ""), _employee(request.data.get("by")),
             request.data.get("on_date"))
        return self._answer(alert)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        alert = self.get_object()
        _run(alert.reopen, request.data.get("reason", ""))
        return self._answer(alert)
