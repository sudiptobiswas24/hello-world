from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import Department, Employee, LeaveRequest
from .serializers import DepartmentSerializer, EmployeeSerializer, LeaveRequestSerializer


class DepartmentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer


class EmployeeViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Employee.objects.all()
    serializer_class = EmployeeSerializer


class LeaveRequestViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = LeaveRequest.objects.all()
    serializer_class = LeaveRequestSerializer
    action_permission_map = {
        "approve": "hr.decide_leaverequest",
        "reject": "hr.decide_leaverequest",
    }

    def _decider(self, request):
        # No User<->Employee link exists yet, so the decider is passed
        # explicitly rather than inferred from request.user. Revisit once
        # accounts are tied to Employee records.
        return get_object_or_404(Employee, pk=request.data.get("decided_by"))

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        leave_request = self.get_object()
        try:
            leave_request.approve(by=self._decider(request))
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(leave_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        leave_request = self.get_object()
        try:
            leave_request.reject(by=self._decider(request), reason=request.data.get("reason"))
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
