"""The attendance register through the API: marked a day at a time, read in from the punch file, and the gaps."""


from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.api import flag
from apps.core.models import to_date
from apps.core.audit import AuditableViewSetMixin

from .attendance import AttendanceDay, import_punches, unmarked_report


class AttendanceDaySerializer(serializers.ModelSerializer):
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True)
    employee_name = serializers.CharField(source="employee.party.name", read_only=True)

    class Meta:
        model = AttendanceDay
        fields = ["id", "employee", "employee_number", "employee_name", "on", "status", "shift", "time_in",
                  "time_out", "late_minutes", "overtime_hours", "source", "note"]
        read_only_fields = ["late_minutes", "source"]


class AttendanceDayViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = AttendanceDay.objects.select_related("employee__party")
    serializer_class = AttendanceDaySerializer
    filter_fields = ["employee", "on", "status", "shift", "source"]
    search_fields = ["employee__employee_number", "employee__party__name"]
    date_field = "on"
    ordering_fields = ["on", "employee__employee_number"]
    action_permission_map = {"punches": "hr.add_attendanceday", "unmarked": "hr.view_attendanceday"}

    @action(detail=False, methods=["post"])
    def punches(self, request):
        """The reader's file, checked whole; kept only with commit=true and no errors."""
        text = request.data.get("text") or ""
        if not text.strip():
            raise ValidationError({"text": ["Paste or upload the punch file."]})
        commit = flag(request.data, "commit", False)
        report = import_punches(text, commit=commit)
        if commit and report["errors"]:
            # Asked to keep it and it cannot be: the refusal, row by row, beside the file.
            raise ValidationError({"text": [f"Row {row}{', ' + column if column else ''}: {message}"
                                            for row, column, message in report["errors"]]})
        return Response({**report, "committed": commit})

    @action(detail=False, methods=["get"])
    def unmarked(self, request):
        """Working days of day-rated people with no mark and no leave in a span: what a pay run would refuse on."""
        start, end = to_date(request.query_params.get("start")), to_date(request.query_params.get("end"))
        if not start or not end:
            raise ValidationError({"start": ["Give start and end as YYYY-MM-DD."]})
        return Response(unmarked_report(start, end))
