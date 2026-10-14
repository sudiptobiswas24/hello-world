"""
Payroll's go-live outputs over the API.

pay-details/            an employee's bank account, PAN and date of birth (payroll's roles only)
statutory-figures/      the dated figures the law sets (wage ceilings, the overtime multiple)
statutory-registers/    ?kind=pt|lwf&month=YYYY-MM: the month's register, from the kept figures
salary-advances/        advances and loans; {id}/void/ {on_date?}
salary-files/           a run's bank file: POST {run} writes it; {id}/csv/; {id}/mark_paid/ {payment};
                        {id}/return_transfer/ {row, receipt}; DELETE while unpaid
payslips/{id}/pdf/      the payslip on paper (pay roles)
my-payslips/            the signed-in employee's own posted payslips, and {id}/pdf/
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import to_date
from apps.core.permissions import RequiredPermission

from .advances import SalaryAdvance
from .bank_file import SalaryTransfer, SalaryTransferFile
from .pay_details import EmployeePayDetails
from .payroll import SLIP_PAYMENT_READS, PayRunStatus, Payslip
from .payroll_views import _run, _slip
from .statutory import StatutoryFigure


class PayDetailsSerializer(serializers.ModelSerializer):
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True)

    class Meta:
        model = EmployeePayDetails
        fields = ["id", "employee", "employee_number", "account_holder", "account_number", "ifsc", "pan",
                  "date_of_birth"]


class PayDetailsViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = EmployeePayDetails.objects.select_related("employee")
    serializer_class = PayDetailsSerializer
    filter_fields = ["employee"]
    search_fields = ["employee__employee_number", "account_holder"]

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)

    def perform_update(self, serializer):
        _run(super().perform_update, serializer)


class FigureSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatutoryFigure
        fields = ["id", "kind", "state", "effective_from", "value", "source"]


class FigureViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StatutoryFigure.objects.all()
    serializer_class = FigureSerializer
    filter_fields = ["kind", "state"]
    date_field = "effective_from"

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)

    def perform_update(self, serializer):
        _run(super().perform_update, serializer)

    def perform_destroy(self, instance):
        _run(super().perform_destroy, instance)


class RegisterView(viewsets.ViewSet):
    """GET ?kind=pt|lwf&month=YYYY-MM."""

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "hr.view_payslip"

    def list(self, request):
        from .statutory_files import lwf_register, pt_register

        build = {"pt": pt_register, "lwf": lwf_register}.get(request.query_params.get("kind"))
        if build is None:
            raise DRFValidationError({"kind": ["pt or lwf."]})
        month = to_date(f"{request.query_params.get('month', '')}-01")
        if month is None:
            raise DRFValidationError({"month": ["A month, as YYYY-MM."]})
        return Response({"kind": request.query_params["kind"], "month": f"{month:%Y-%m}", "rows": build(month)})


class AdvanceSerializer(serializers.ModelSerializer):
    recovered = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    voided = serializers.SerializerMethodField()

    class Meta:
        model = SalaryAdvance
        fields = ["id", "employee", "kind", "payment", "component", "amount", "instalment", "recover_from", "note",
                  "voided_at", "voided", "recovered", "balance"]

    def get_recovered(self, obj):
        return obj.recovered()

    def get_balance(self, obj):
        return obj.balance()

    def get_voided(self, obj):
        return obj.voided_at is not None


class AdvanceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalaryAdvance.objects.select_related("employee", "payment", "component")
    serializer_class = AdvanceSerializer
    filter_fields = ["employee", "kind"]
    date_field = "recover_from"
    action_permission_map = {"void": "hr.void_salaryadvance"}

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)

    def perform_update(self, serializer):
        _run(super().perform_update, serializer)

    def perform_destroy(self, instance):
        _run(super().perform_destroy, instance)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        advance = self.get_object()
        _run(advance.void, on_date=to_date(request.data.get("on_date")))
        return Response(self.get_serializer(SalaryAdvance.objects.get(pk=advance.pk)).data)


class TransferSerializer(serializers.ModelSerializer):
    employee = serializers.CharField(source="payslip.employee.employee_number", read_only=True)
    returned = serializers.SerializerMethodField()

    class Meta:
        model = SalaryTransfer
        fields = ["id", "payslip", "employee", "holder", "account_number", "ifsc", "amount", "returned_by",
                  "returned"]

    def get_returned(self, obj):
        return obj.is_returned()


class TransferFileSerializer(serializers.ModelSerializer):
    rows = TransferSerializer(many=True, read_only=True)

    class Meta:
        model = SalaryTransferFile
        fields = ["id", "run", "number", "narration", "total", "payment", "paid_at", "rows"]
        read_only_fields = ["number", "narration", "total", "payment", "paid_at"]

    def create(self, validated_data):
        # Written by prepare(), never from a client's figures: the rows are the run's slips still owed.
        return SalaryTransferFile.prepare(validated_data.pop("run"), **validated_data)


class TransferFileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalaryTransferFile.objects.select_related("run", "payment").prefetch_related(
        "rows__payslip__employee", "rows__returned_by")
    serializer_class = TransferFileSerializer
    filter_fields = ["run"]
    http_method_names = ["get", "post", "delete", "head", "options"]
    action_permission_map = {"mark_paid": "hr.post_payrun", "return_transfer": "hr.post_payrun"}

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)

    def perform_destroy(self, instance):
        _run(super().perform_destroy, instance)

    @action(detail=True, methods=["get"])
    def csv(self, request, pk=None):
        made = self.get_object()
        response = HttpResponse(made.text, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{made.number}.csv"'
        response["X-Total-Amount"] = f"{made.total:.2f}"
        return response

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        from apps.accounting.models import Payment

        made = self.get_object()
        _run(made.mark_paid, get_object_or_404(Payment, pk=request.data.get("payment")))
        return Response(self.get_serializer(self.get_queryset().get(pk=made.pk)).data)

    @action(detail=True, methods=["post"])
    def return_transfer(self, request, pk=None):
        from apps.accounting.models import Payment

        made = self.get_object()
        row = get_object_or_404(SalaryTransfer, pk=request.data.get("row"), file=made)
        _run(row.return_transfer, get_object_or_404(Payment, pk=request.data.get("receipt")))
        return Response(self.get_serializer(self.get_queryset().get(pk=made.pk)).data)


def _pdf(slip):
    from .payslip_print import payslip_pdf

    response = HttpResponse(payslip_pdf(slip), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="payslip_{slip.run.number}_{slip.employee.employee_number}.pdf"'
    return response


class MyPayslipsView(viewsets.GenericViewSet):
    """The signed-in employee's own payslips from posted runs: theirs, never another's."""

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "hr.view_own_payslip"
    queryset = Payslip.objects.none()

    def _mine(self, request):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise PermissionDenied("This login is nobody's on the payroll.")
        return Payslip.objects.filter(employee=employee, run__status=PayRunStatus.POSTED).select_related(
            "employee__party", "run", "payment__journal_entry").prefetch_related(
            "lines__component", *SLIP_PAYMENT_READS).order_by(
            "-run__period_end", "-pk")

    def list(self, request):
        slips = self.paginate_queryset(self._mine(request))
        return self.get_paginated_response([
            {**_slip(slip), "period_start": slip.run.period_start, "period_end": slip.run.period_end,
             "pay_date": slip.run.pay_date, "run_number": slip.run.number} for slip in slips])

    def retrieve(self, request, pk=None):
        return Response(_slip(get_object_or_404(self._mine(request), pk=pk)))

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        return _pdf(get_object_or_404(self._mine(request), pk=pk))
