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
from .shortfalls import SettlementKind, ShortfallSettlement, owed_back
from .bank_file import SalaryTransfer, SalaryTransferFile
from .pay_details import EmployeePayDetails
from .payroll import SLIP_PAYMENT_READS, PayRunStatus, Payslip
from .payroll_views import _run, _slip
from .statutory import MinimumWage, StatutoryFigure


class PayDetailsSerializer(serializers.ModelSerializer):
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True)

    class Meta:
        model = EmployeePayDetails
        fields = ["id", "employee", "employee_number", "account_holder", "account_number", "ifsc", "pan",
                  "date_of_birth", "scheduled_employment", "wage_zone", "skill"]


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
        fields = ["id", "kind", "state", "effective_from", "value", "source", "confirmed_by", "confirmed_on"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class ConfirmAction:
    """confirm/: the Controller's word that the figures read (the request's, as the screen showed them) are the law's."""

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError as DRFValidationError

        row = self.get_object()
        seen = {name: request.data.get(name.removesuffix("_id"), request.data.get(name)) for name in row.FIGURES}
        try:
            row.confirm(request.user, seen=seen)
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return Response(self.get_serializer(type(row).objects.get(pk=row.pk)).data)


class FigureViewSet(ConfirmAction, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = StatutoryFigure.objects.all()
    serializer_class = FigureSerializer
    filter_fields = ["kind", "state"]
    date_field = "effective_from"
    action_permission_map = {"confirm": "hr.confirm_statutoryfigure"}

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)

    def perform_update(self, serializer):
        _run(super().perform_update, serializer)

    def perform_destroy(self, instance):
        _run(super().perform_destroy, instance)


class MinimumWageSerializer(serializers.ModelSerializer):
    monthly = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = MinimumWage
        fields = ["id", "state", "employment", "zone", "skill", "effective_from", "basic", "vda", "monthly",
                  "source", "confirmed_by", "confirmed_on"]
        read_only_fields = ["confirmed_by", "confirmed_on"]


class MinimumWageViewSet(ConfirmAction, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = MinimumWage.objects.all()
    serializer_class = MinimumWageSerializer
    filter_fields = ["state", "skill"]
    search_fields = ["employment", "zone"]
    date_field = "effective_from"
    action_permission_map = {"confirm": "hr.confirm_minimumwage"}

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


class ShortfallSettlementSerializer(serializers.ModelSerializer):
    employee = serializers.SerializerMethodField()
    stands = serializers.SerializerMethodField()
    owed_back = serializers.SerializerMethodField()

    class Meta:
        model = ShortfallSettlement
        fields = ["id", "payslip", "employee", "advance", "kind", "amount", "payment", "date", "reason", "posted_account",
                  "journal_entry", "voided_at", "voided_entry", "stands", "owed_back"]

    def get_stands(self, obj):
        return obj.stands()

    def get_employee(self, obj):
        # The slip's person, or for a leaver no run paid, the advance's (O262).
        return obj.person().employee_number

    def get_owed_back(self, obj):
        # What is still owed of what it settles: the advance's rest (O262), or the slip's shortfall.
        return obj.advance.balance() if obj.advance_id is not None else owed_back(obj.payslip)


class ShortfallSettlementViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """
    What a former employee paid back or the Controller wrote off, against a final slip's shortfall (O215).
    Posted as made: voided, never changed or deleted.
    """

    queryset = ShortfallSettlement.objects.select_related("payslip__employee", "advance__employee", "payment")
    serializer_class = ShortfallSettlementSerializer
    filter_fields = ["payslip", "kind"]
    date_field = "date"
    http_method_names = ["get", "post", "head", "options"]
    action_permission_map = {"void": "hr.void_shortfallsettlement"}

    def perform_create(self, serializer):
        if (serializer.validated_data.get("kind") == SettlementKind.WRITE_OFF
                and not self.request.user.has_perm("hr.write_off_shortfall")):
            raise PermissionDenied("Writing off what a former employee owes is the Controller's decision.")
        _run(super().perform_create, serializer)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        settlement = self.get_object()
        _run(settlement.void, on_date=to_date(request.data.get("on_date")))
        return Response(self.get_serializer(ShortfallSettlement.objects.get(pk=settlement.pk)).data)


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
    # Its real state (review RP6): a payment voided pays nobody, though the file still names it.
    paid = serializers.SerializerMethodField()
    payment_voided = serializers.SerializerMethodField()

    class Meta:
        model = SalaryTransferFile
        fields = ["id", "run", "number", "narration", "total", "payment", "paid_at", "paid", "payment_voided", "rows"]
        read_only_fields = ["number", "narration", "total", "payment", "paid_at"]

    def get_paid(self, obj):
        return obj.payment_id is not None and not obj.payment.is_voided()

    def get_payment_voided(self, obj):
        return obj.payment_id is not None and obj.payment.is_voided()

    def create(self, validated_data):
        # Written by prepare(), never from a client's figures: the rows are the run's slips still owed.
        return SalaryTransferFile.prepare(validated_data.pop("run"), **validated_data)


class TransferFileViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = SalaryTransferFile.objects.select_related("run", "payment").prefetch_related(
        "payment__journal_entry__reversed_by", "rows__payslip__employee", "rows__returned_by")
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
            # A login that is nobody's on the payroll has no payslips: an empty list, not a refusal of
            # a screen its role offers (the browser sweep opens every offered screen as each role).
            return Payslip.objects.none()
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
