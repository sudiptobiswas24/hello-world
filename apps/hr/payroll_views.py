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

import copy
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.audit import AuditableViewSetMixin
from apps.core.models import to_date
from apps.core.permissions import RequiredPermission

from .models import Employee
from .payroll import (
    EmployeeCompensation,
    PayComponent,
    PayComponentSlab,
    PayRun,
    Payslip,
    StatutoryRemittance,
    statutory_liabilities,
)


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
                  "sequence", "is_active", "base_components", "base_ceiling",
                  "coverage_components", "coverage_ceiling", "coverage_period_months",
                  "rounding", "remit_by_day"]

    def validate(self, attrs):
        # ModelSerializer does not run model.clean(); save() does, and its
        # ValidationError would otherwise reach the client as a 500.
        instance = copy.copy(self.instance) if self.instance is not None else PayComponent()
        for name, value in attrs.items():
            if name not in ("base_components", "coverage_components"):
                setattr(instance, name, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages)
        return attrs


class SlabSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayComponentSlab
        fields = ["id", "component", "above", "up_to", "month", "amount"]


class RemittanceSerializer(serializers.ModelSerializer):
    payment_number = serializers.CharField(source="payment.number", read_only=True)
    account_name = serializers.CharField(source="liability_account.name", read_only=True)

    class Meta:
        model = StatutoryRemittance
        fields = ["id", "payment", "liability_account", "period", "amount", "payment_number", "account_name"]


class CompensationSerializer(serializers.ModelSerializer):
    component_name = serializers.CharField(source="component.name", read_only=True)

    class Meta:
        model = EmployeeCompensation
        fields = ["id", "employee", "component", "amount", "effective_from", "effective_to",
                  "note", "component_name"]


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
                  for line in slip.lines.all()],
    }


class PayComponentViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PayComponent.objects.all()
    serializer_class = PayComponentSerializer
    filter_fields = ["kind", "basis", "is_active"]
    search_fields = ["code", "name"]


class CompensationViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = EmployeeCompensation.objects.select_related("employee", "component")
    serializer_class = CompensationSerializer
    filter_fields = ["employee", "component"]
    date_field = "effective_from"


class PayRunViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    search_fields = ["number", "name"]
    filter_fields = ["status"]
    date_field = "period_start"
    ordering_fields = ["period_start", "pay_date", "number"]

    # Each run's totals add up its slips' lines: read once for the page.
    queryset = PayRun.objects.prefetch_related("payslips__lines__component", "payslips__employee__party",
                                               "payslips__payment__journal_entry__reversed_by")
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
        run = self._fresh(run)
        return Response({**self.get_serializer(run).data,
                         "payslips": [_slip(slip) for slip in run.payslips.all()]})

    def _fresh(self, run):
        """The run read again: what was prefetched for it predates the action."""
        return self.get_queryset().get(pk=run.pk)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        run = self.get_object()
        _run(run.post)
        return Response(self.get_serializer(self._fresh(run)).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        run = self.get_object()
        # to_date refuses a date it cannot read; parse_date answered None,
        # and None meant today, so a typo voided the run as of today.
        _run(run.void, on_date=to_date(request.data.get("on_date")))
        return Response(self.get_serializer(self._fresh(run)).data)


class PayslipViewSet(AuditableViewSetMixin, viewsets.ReadOnlyModelViewSet):
    extra_params = ('run',)
    queryset = Payslip.objects.select_related("employee__party", "run", "payment__journal_entry").prefetch_related(
        "payment__journal_entry__reversed_by", "lines__component")
    action_permission_map = {"pay": "hr.post_payrun"}

    def get_queryset(self):
        rows = super().get_queryset()
        run = self.request.query_params.get("run")
        return rows.filter(run_id=run) if run else rows

    def list(self, request):
        # Paged like every list: the first 500 and silence about the rest
        # was a pay run of 600 people missing a hundred slips.
        slips = self.paginate_queryset(self.get_queryset().order_by("run_id", "employee__employee_number", "pk")
                                       .prefetch_related("lines__component"))
        return self.get_paginated_response([_slip(slip) for slip in slips])

    def retrieve(self, request, pk=None):
        return Response(_slip(self.get_object()))

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        from apps.accounting.models import Payment

        slip = self.get_object()
        payment = get_object_or_404(Payment, pk=request.data.get("payment"))
        _run(slip.pay, payment)
        return Response(_slip(slip))


class SlabViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = PayComponentSlab.objects.select_related("component")
    serializer_class = SlabSerializer
    filter_fields = ["component"]

    def perform_create(self, serializer):
        try:
            super().perform_create(serializer)
        except IntegrityError as exc:
            raise DRFValidationError(["Those slab figures do not make a band."]) from exc

    def perform_update(self, serializer):
        try:
            super().perform_update(serializer)
        except IntegrityError as exc:
            raise DRFValidationError(["Those slab figures do not make a band."]) from exc


class RemittanceViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    """Money paid over to PF, ESI or the tax office, against a month. Not edited."""

    queryset = StatutoryRemittance.objects.select_related("payment", "liability_account")
    serializer_class = RemittanceSerializer
    filter_fields = ["liability_account", "payment"]
    date_field = "period"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def perform_create(self, serializer):
        _run(super().perform_create, serializer)


class StatutoryLiabilitiesView(viewsets.ViewSet):
    """GET ?as_of=: what payroll owes over, by account and month, and when it was due."""

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "hr.view_statutoryremittance"

    def list(self, request):
        # to_date: parse_date answers None for "tomorrow" but raises on
        # 2026-02-30, which was a 500.
        rows = statutory_liabilities(to_date(request.query_params.get("as_of")))
        return Response([{
            "account": row["account"].pk, "account_code": row["account"].code,
            "account_name": row["account"].name, "period": row["period"].isoformat(),
            "deducted": str(row["deducted"]), "remitted": str(row["remitted"]),
            "outstanding": str(row["outstanding"]),
            "due_date": row["due_date"].isoformat() if row["due_date"] else None,
            "overdue": row["overdue"],
        } for row in rows])


class GratuityView(viewsets.ViewSet):
    """
    GET ?as_of=&components=BASIC,DA&provision=<account>: gratuity owed to each
    employee in service, and the total set against the provision.
    """

    permission_classes = [IsAuthenticated, RequiredPermission]
    required_permission = "hr.view_employeecompensation"

    def list(self, request):
        from apps.accounting.models import Account
        from apps.core.api import record_or_404

        from .gratuity import gratuity_due

        as_of = to_date(request.query_params.get("as_of"))
        codes = [code.strip() for code in (request.query_params.get("components") or "").split(",") if code.strip()]
        if not as_of or not codes:
            raise DRFValidationError({"components": ["Give the day and the wage components gratuity is on, "
                                                     "as codes: BASIC,DA."]})
        components = list(PayComponent.objects.filter(code__in=codes))
        unknown = sorted(set(codes) - {component.code for component in components})
        provision = request.query_params.get("provision")
        provision = record_or_404(Account, provision, "provision", optional=True)
        found = gratuity_due(as_of, components, provision)
        if unknown:
            # A report, not a form: a code the plant does not use counts nothing and says so.
            found["note"] = (f"No pay component {', '.join(unknown)}: nothing is counted for it. Gratuity is on "
                             "the components named here by their codes.")
        return Response(found)
