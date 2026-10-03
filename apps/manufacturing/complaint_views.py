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
        fields = ["id", "complaint", "kind", "description", "owner", "due_on", "done_on",
                  "done_note", "verified_on", "verified_by"]
        read_only_fields = ["done_on", "done_note", "verified_on", "verified_by"]


class ComplaintSerializer(serializers.ModelSerializer):
    lots = serializers.SerializerMethodField()
    actions = CorrectiveActionSerializer(many=True, read_only=True)

    class Meta:
        model = Complaint
        fields = ["id", "number", "customer", "received_on", "category", "description",
                  "quantity_affected", "status", "root_cause", "decided_on", "decided_by",
                  "rejection_reason", "reopened_reason", "lots", "actions"]
        read_only_fields = ["number", "status", "root_cause", "decided_on", "decided_by",
                            "rejection_reason", "reopened_reason"]

    def get_lots(self, obj):
        return [{"lot": row.lot.code, "item": row.lot.item.sku,
                 "quantity": None if row.quantity is None else str(row.quantity)}
                for row in obj.complained_lots.select_related("lot__item")]


class ComplaintViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Complaint.objects.select_related("customer").prefetch_related("actions")
    serializer_class = ComplaintSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    action_permission_map = {
        "lots": "manufacturing.change_complaint",
        "close": "manufacturing.change_complaint",
        "reject": "manufacturing.change_complaint",
        "reopen": "manufacturing.change_complaint",
    }

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
    queryset = CorrectiveAction.objects.select_related("complaint", "owner")
    serializer_class = CorrectiveActionSerializer
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
