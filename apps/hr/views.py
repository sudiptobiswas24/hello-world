from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import record_or_404, whole_number
from apps.core.audit import AuditableViewSetMixin

from .models import Department, Employee, LeavePolicy, LeaveRequest, leave_summary


def _employee_of(user):
    """The employee a login belongs to, or None."""
    return Employee.objects.filter(user=user).first() if user.is_authenticated else None


def me_as_employee(user):
    """For /api/core/me/: the employee this login is, so a screen can say "mine"."""
    employee = _employee_of(user)
    return {"employee": employee.pk if employee else None,
            "employee_name": employee.party.name if employee else ""}
from .serializers import (
    DepartmentSerializer,
    EmployeeSerializer,
    LeavePolicySerializer,
    LeaveRequestSerializer,
)


class DepartmentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    search_fields = ["code", "name"]


class LeavePolicyViewSet(viewsets.ReadOnlyModelViewSet):
    """The allowances a leave request may draw on: read here, kept in the admin."""

    queryset = LeavePolicy.objects.order_by("code")
    serializer_class = LeavePolicySerializer


class EmployeeViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Employee.objects.select_related("party", "department")
    serializer_class = EmployeeSerializer
    filter_fields = ["department", "employment_status", "manager"]
    search_fields = ["employee_number", "party__name", "job_title"]
    ordering_fields = ["employee_number", "hire_date"]
    # ?missing=uan or esi_number: members of that scheme today with the
    # number blank (the morning check's list).
    extra_params = ("missing",)

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        missing = self.request.query_params.get("missing")
        if missing:
            from apps.hr.payroll import Statutory
            from apps.web.checks import members_without

            kinds = {"uan": (Statutory.PF, Statutory.PF_EMPLOYER),
                     "esi_number": (Statutory.ESI, Statutory.ESI_EMPLOYER)}.get(missing)
            if kinds is None:
                raise DRFValidationError({"missing": ["Ask for uan or esi_number."]})
            queryset = members_without(queryset, missing, kinds, timezone.localdate())
        return queryset

    @action(detail=True, methods=["get"], url_path="leave")
    def leave(self, request, pk=None):
        """Every allowance this person has, and where each stands."""
        employee = self.get_object()
        # The plant's year, and a typed one refused in words: int() of
        # "2026a" was a 500, and timezone.now() is UTC's day.
        year = whole_number(request.query_params, "year", default=timezone.localdate().year, least=2000)
        return Response([
            {
                "policy": row["policy"].code,
                "name": row["policy"].name,
                "entitled": row["entitled"],
                "taken": row["taken"],
                "booked": row["booked"],
                "balance": row["balance"],
            }
            for row in leave_summary(employee, year)
        ])


class LeaveRequestViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["employee__party__name", "employee__employee_number", "reason"]
    filter_fields = ["status", "employee", "leave_type", "policy"]
    date_field = "start_date"
    ordering_fields = ["start_date", "end_date", "status"]
    queryset = LeaveRequest.objects.select_related("employee__party", "decided_by__party", "policy")
    serializer_class = LeaveRequestSerializer
    action_permission_map = {
        "approve": "hr.decide_leaverequest",
        "reject": "hr.decide_leaverequest",
    }

    def get_queryset(self):
        # Your own requests and your reports'; everyone's only to those
        # who keep the records (HR, payroll). Self service read every
        # colleague's leave, reasons and all, until the login knew whose
        # it was.
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.has_perm("hr.view_every_leaverequest"):
            return queryset
        me = _employee_of(user)
        if me is None:
            return queryset.none()
        return queryset.filter(Q(employee=me) | Q(employee__in=me.reports()))

    def perform_create(self, serializer):
        user = self.request.user
        employee = serializer.validated_data.get("employee")
        if not (user.is_superuser or user.has_perm("hr.change_employee")):
            me = _employee_of(user)
            if me is None or employee != me:
                raise DRFValidationError({"employee": [
                    "You ask for your own leave; HR records anyone else's."
                    if me is not None else
                    "Your login is not linked to an employee: HR links it on your record."]})
        super().perform_create(serializer)

    @staticmethod
    def _as_hr(request):
        return request.user.has_perm("hr.decide_any_leaverequest")

    def _decider(self, request):
        """
        The signed-in person, as the employee they are. An administrator
        may name another (`decided_by`), and the audit trail still says who
        did it; anyone else decides as themselves, whatever they send.
        """
        named = request.data.get("decided_by")
        if request.user.is_superuser and named not in (None, ""):
            return record_or_404(Employee, named, "decided_by")
        me = _employee_of(request.user)
        if me is None:
            raise DRFValidationError({"decided_by": [
                "Your login is not linked to an employee: HR links it on your record."]})
        if named not in (None, "") and str(named) != str(me.pk):
            raise DRFValidationError({"decided_by": ["You decide as yourself."]})
        return me

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        leave_request = self.get_object()
        try:
            leave_request.approve(by=self._decider(request), as_hr=self._as_hr(request))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(leave_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        leave_request = self.get_object()
        try:
            leave_request.reject(by=self._decider(request), reason=request.data.get("reason"),
                                 as_hr=self._as_hr(request))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(leave_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        leave_request = self.get_object()
        try:
            leave_request.cancel()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(leave_request).data)
