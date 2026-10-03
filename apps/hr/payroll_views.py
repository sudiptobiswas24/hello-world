"""
Payroll over the API.

pay-components/ and compensation/ are the set-up: what can appear on a
payslip, and what each person is paid under it from when. pay-runs/ is
the period: POST to open one, {id}/calculate/ {employees?, hours?} to
work out its payslips (again, as often as needed, until posted),
{id}/post/ to put it in the ledger, {id}/void/ {on_date?} to reverse
it. payslips/ reads a run's slips with their lines, and {id}/pay/
{payment} settles one against a payment made.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin

from .models import Employee
from .payroll import EmployeeCompensation, PayComponent, PayRun, Payslip


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.messages)


class PayComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayComponent
        fields = ["id", "code", "name", "kind", "basis", "expense_account",
                  "liability_account", "measure", "is_taxable", "reduces_for_unpaid_leave",
                  "sequence", "is_active"]


class CompensationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeCompensation
        fields = ["id", "employee", "component", "amount", "effective_from", "effective_to",
                  "note"]


class PayRunSerializer(serializers.ModelSerializer):
    gross = serializers.SerializerMethodField()
    net = serializers.SerializerMethodField()
    employer_cost = serializers.SerializerMethodField()
    unpaid_net = serializers.SerializerMethodField()

    class Meta:
        model = PayRun
        fields = ["id", "number", "name", "period_start", "period_end", "pay_date", "status",
                  "posted_at", "voided_at", "gross", "net", "employer_cost", "unpaid_net"]
        read_only_fields = ["number", "status", "posted_at", "voided_at"]

    def get_gross(self, obj):
        return str(obj.gross())

    def get_net(self, obj):
        return str(obj.net())

    def get_employer_cost(self, obj):
        return str(obj.employer_cost())

    def get_unpaid_net(self, obj):
        return str(obj.unpaid_net())


def _slip(slip):
    return {
        "id": slip.pk, "run": slip.run_id, "employee": slip.employee.employee_number,
        "name": slip.employee.party.name, "gross": str(slip.gross()),
        "deductions": str(slip.deductions()), "net": str(slip.net()),
        "employer_cost": str(slip.employer_cost()), "paid": slip.is_paid(),
        "lines": [{"component": line.component.code, "kind": line.kind,
                   "description": line.description, "rate": str(line.rate),
                   "quantity": None if line.quantity is None else str(line.quantity),
                   "amount": str(line.amount)}
                  for line in slip.lines.select_related("component")],
    }


class PayComponentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PayComponent.objects.all()
    serializer_class = PayComponentSerializer


class CompensationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = EmployeeCompensation.objects.select_related("employee", "component")
    serializer_class = CompensationSerializer

    def get_queryset(self):
        rows = super().get_queryset()
        employee = self.request.query_params.get("employee")
        return rows.filter(employee_id=employee) if employee else rows


class PayRunViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PayRun.objects.all()
    serializer_class = PayRunSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    action_permission_map = {
        "calculate": "hr.change_payrun",
        "post": "hr.post_payrun",
        "void": "hr.post_payrun",
    }

    @action(detail=True, methods=["post"])
    def calculate(self, request, pk=None):
        """{employees?: [id], hours?: {employee id: hours}} — hours for hourly pay."""
        run = self.get_object()
        ids = request.data.get("employees")
        people = None
        if ids is not None:
            people = list(Employee.objects.filter(pk__in=ids))
            if len(people) != len(set(ids)):
                raise DRFValidationError(["Some of those employees do not exist."])
        hours = {}
        for key, value in (request.data.get("hours") or {}).items():
            person = get_object_or_404(Employee, pk=key)
            try:
                hours[person] = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise DRFValidationError([f"Hours for {person} are a number."])
            if not hours[person].is_finite() or hours[person] < 0:
                raise DRFValidationError([f"Hours for {person} are a number, not negative."])
        _run(run.calculate, employees=people, hours=hours)
        return Response({**self.get_serializer(run).data,
                         "payslips": [_slip(slip) for slip in run.payslips.all()]})

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        run = self.get_object()
        _run(run.post)
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        run = self.get_object()
        _run(run.void, on_date=parse_date(str(request.data.get("on_date") or "")))
        return Response(self.get_serializer(run).data)


class PayslipViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Payslip.objects.select_related("employee__party", "run")
    action_permission_map = {"pay": "hr.post_payrun"}

    def get_queryset(self):
        rows = super().get_queryset()
        run = self.request.query_params.get("run")
        return rows.filter(run_id=run) if run else rows

    def list(self, request):
        return Response([_slip(slip) for slip in self.get_queryset()[:500]])

    def retrieve(self, request, pk=None):
        return Response(_slip(self.get_object()))

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        from apps.accounting.models import Payment

        slip = self.get_object()
        payment = get_object_or_404(Payment, pk=request.data.get("payment"))
        _run(slip.pay, payment)
        return Response(_slip(slip))
