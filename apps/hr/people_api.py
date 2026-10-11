"""
Expense claims, appraisals and recruitment over the API, as the people
who use them: a claim is the claimant's own unless HR records it, their
manager decides it and accounts pays it; an appraisal is the reviewer's
until submitted and the person's to acknowledge; openings and their
applicants are the personnel office's.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.core.api import record_or_404
from apps.core.audit import AuditableViewSetMixin

from .appraisals import Appraisal
from .expenses import ExpenseClaim, ExpenseLine
from .models import Employee
from .recruitment import Applicant, JobOpening
from .views import _employee_of


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)


def _me(request, what="Your login"):
    me = _employee_of(request.user)
    if me is None:
        raise DRFValidationError([f"{what} is not linked to an employee: HR links it on your record."])
    return me


# --- expense claims

class ExpenseLineSerializer(serializers.ModelSerializer):
    expense_account_name = serializers.CharField(source="expense_account.name", read_only=True)

    class Meta:
        model = ExpenseLine
        fields = ["id", "claim", "spent_on", "expense_account", "expense_account_name", "description", "amount",
                  "receipt_reference"]


class ExpenseClaimSerializer(serializers.ModelSerializer):
    lines = ExpenseLineSerializer(many=True, read_only=True)
    employee_name = serializers.CharField(source="employee.party.name", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.party.name", read_only=True, default="")
    paid_from_name = serializers.CharField(source="paid_from.name", read_only=True, default="")
    total = serializers.SerializerMethodField()

    class Meta:
        model = ExpenseClaim
        fields = ["id", "number", "employee", "employee_name", "claim_date", "purpose", "status", "total",
                  "decided_by", "decided_by_name", "decided_at", "decision_note", "paid_on", "paid_from",
                  "paid_from_name", "journal_entry", "voided_entry", "lines", "created_at"]
        read_only_fields = ["number", "status", "decided_by", "decided_at", "decision_note", "paid_on", "paid_from",
                            "journal_entry", "voided_entry"]
        # Left out, the claimant is the login (perform_create); the day is today (save).
        extra_kwargs = {"employee": {"required": False, "allow_null": True},
                        "claim_date": {"required": False, "allow_null": True}}

    def get_total(self, claim):
        return claim.total()


class OwnOrReportsMixin:
    """Everyone's to who keeps the records; otherwise one's own and one's reports'."""

    every = ""          # the permission that reads everyone's
    employee_path = "employee"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.has_perm(self.every):
            return queryset
        me = _employee_of(user)
        if me is None:
            return queryset.none()
        return queryset.filter(Q(**{self.employee_path: me}) | Q(**{f"{self.employee_path}__in": me.reports()}))


class ExpenseClaimViewSet(OwnOrReportsMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ExpenseClaim.objects.select_related("employee__party", "decided_by__party", "paid_from").prefetch_related(
        "lines__expense_account")
    serializer_class = ExpenseClaimSerializer
    every = "hr.view_every_expenseclaim"
    filter_fields = ["status", "employee"]
    search_fields = ["number", "purpose", "employee__party__name", "employee__employee_number"]
    date_field = "claim_date"
    ordering_fields = ["claim_date", "number", "status"]
    action_permission_map = {
        "submit": "hr.change_expenseclaim",
        "approve": "hr.decide_expenseclaim",
        "reject": "hr.decide_expenseclaim",
        "pay": "hr.pay_expenseclaim",
        "unpay": "hr.pay_expenseclaim",
    }

    def perform_create(self, serializer):
        user = self.request.user
        employee = serializer.validated_data.get("employee")
        if not (user.is_superuser or user.has_perm("hr.change_employee")):
            me = _me(self.request)
            if employee is not None and employee != me:
                raise DRFValidationError({"employee": ["You claim your own expenses; HR records anyone else's."]})
            employee = me
        elif employee is None:
            employee = _me(self.request)
        super().perform_create(serializer, employee=employee)

    def _answer(self, claim):
        return Response(self.get_serializer(self.get_queryset().get(pk=claim.pk)).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        claim = self.get_object()
        _run(claim.submit)
        return self._answer(claim)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        claim = self.get_object()
        _run(claim.approve, _me(request), note=str(request.data.get("note", "")),
             as_hr=request.user.has_perm("hr.decide_any_expenseclaim"))
        return self._answer(claim)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        claim = self.get_object()
        _run(claim.reject, _me(request), note=str(request.data.get("note", "")),
             as_hr=request.user.has_perm("hr.decide_any_expenseclaim"))
        return self._answer(claim)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """{paid_from, on_date?, memo?}: one journal, the claim recorded as paid."""
        from apps.accounting.models import Account

        claim = self.get_object()
        _run(claim.pay, record_or_404(Account, request.data.get("paid_from"), "paid_from", optional=True),
             on_date=request.data.get("on_date"), memo=str(request.data.get("memo", "")))
        return self._answer(claim)

    @action(detail=True, methods=["post"])
    def unpay(self, request, pk=None):
        """{reason, on_date?}: the payment reversed, the claim approved again."""
        claim = self.get_object()
        _run(claim.unpay, str(request.data.get("reason", "")), on_date=request.data.get("on_date"))
        return self._answer(claim)


class ExpenseLineViewSet(OwnOrReportsMixin, AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = ExpenseLine.objects.select_related("claim__employee", "expense_account")
    serializer_class = ExpenseLineSerializer
    every = "hr.view_every_expenseclaim"
    employee_path = "claim__employee"
    filter_fields = ["claim"]

    def _check_claim(self, serializer):
        """
        A line is written onto one's own claim, as a claim is made for
        oneself; HR records anyone's. Self Service wrote a 5,000 line onto a
        colleague's draft, and a manager, reading a report's claim, could
        change its figures.
        """
        user = self.request.user
        if user.is_superuser or user.has_perm("hr.change_employee"):
            return
        me = _me(self.request)
        for claim in {serializer.validated_data.get("claim"), getattr(serializer.instance, "claim", None)} - {None}:
            if claim.employee_id != me.pk:
                raise DRFValidationError({"claim": ["You write lines on your own claims; HR records anyone else's."]})

    def perform_create(self, serializer):
        self._check_claim(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check_claim(serializer)
        super().perform_update(serializer)


# --- appraisals

class AppraisalSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.party.name", read_only=True)
    reviewer_name = serializers.CharField(source="reviewer.party.name", read_only=True)

    class Meta:
        model = Appraisal
        fields = ["id", "employee", "employee_name", "reviewer", "reviewer_name", "period_start", "period_end", "status",
                  "rating", "strengths", "improvements", "goals", "submitted_at", "employee_comment", "acknowledged_at"]
        read_only_fields = ["status", "submitted_at", "employee_comment", "acknowledged_at"]
        # Left out, the reviewer is the login (perform_create).
        extra_kwargs = {"reviewer": {"required": False, "allow_null": True}}


class AppraisalViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Appraisal.objects.select_related("employee__party", "reviewer__party")
    serializer_class = AppraisalSerializer
    filter_fields = ["status", "employee", "reviewer"]
    search_fields = ["employee__party__name", "employee__employee_number"]
    date_field = "period_start"
    ordering_fields = ["period_start", "status"]
    action_permission_map = {"submit": "hr.change_appraisal", "acknowledge": "hr.acknowledge_appraisal"}

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.has_perm("hr.view_every_appraisal"):
            return queryset
        me = _employee_of(user)
        if me is None:
            return queryset.none()
        # One's own only once submitted: a draft is the reviewer's working notes.
        return queryset.filter(Q(reviewer=me) | Q(employee__in=me.reports()) | (Q(employee=me) & ~Q(status="draft")))

    def _as_hr(self):
        user = self.request.user
        return user.is_superuser or user.has_perm("hr.view_every_appraisal")

    def _check_appraiser(self, serializer):
        """
        An appraisal is written by the person's manager (a manager above, or
        their department's), and changed by its reviewer; HR's for anyone.
        A Line Manager appraised a peer who does not report to them.
        """
        if self._as_hr():
            return
        me = _me(self.request)
        current = serializer.instance
        if current is not None and current.reviewer_id != me.pk:
            raise DRFValidationError(["Only its reviewer changes an appraisal."])
        employee = serializer.validated_data.get("employee", getattr(current, "employee", None))
        if employee is not None and employee.pk not in me.reports():
            raise DRFValidationError({"employee": [f"{employee} does not report to you: their manager appraises them."]})

    def perform_create(self, serializer):
        self._check_appraiser(serializer)
        reviewer = serializer.validated_data.get("reviewer")
        if reviewer is None or not self._as_hr():
            reviewer = _me(self.request)
        super().perform_create(serializer, reviewer=reviewer)

    def perform_update(self, serializer):
        self._check_appraiser(serializer)
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        appraisal = self.get_object()
        if not self._as_hr() and appraisal.reviewer != _me(request):
            raise DRFValidationError(["Only its reviewer submits an appraisal."])
        _run(appraisal.submit)
        return Response(self.get_serializer(appraisal).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        appraisal = self.get_object()
        if appraisal.employee != _me(request):
            raise DRFValidationError(["Only the person appraised acknowledges it."])
        _run(appraisal.acknowledge, str(request.data.get("comment", "")))
        return Response(self.get_serializer(appraisal).data)


# --- recruitment

class JobOpeningSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    hired = serializers.SerializerMethodField()
    applicant_count = serializers.SerializerMethodField()

    class Meta:
        model = JobOpening
        fields = ["id", "title", "department", "department_name", "openings", "description", "status", "opened_on",
                  "closed_on", "hired", "applicant_count"]
        read_only_fields = ["status", "closed_on"]
        extra_kwargs = {"opened_on": {"required": False, "allow_null": True}}

    def get_hired(self, opening):
        return opening.hired()

    def get_applicant_count(self, opening):
        return opening.applicants.count()


class ApplicantSerializer(serializers.ModelSerializer):
    opening_title = serializers.CharField(source="opening.title", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True, default="")

    class Meta:
        model = Applicant
        fields = ["id", "opening", "opening_title", "name", "phone", "email", "applied_on", "source", "stage", "rating",
                  "expected_pay", "notes", "rejected_reason", "employee", "employee_number"]
        read_only_fields = ["stage", "rejected_reason", "employee"]
        extra_kwargs = {"applied_on": {"required": False, "allow_null": True}}


class JobOpeningViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = JobOpening.objects.select_related("department").prefetch_related("applicants")
    serializer_class = JobOpeningSerializer
    filter_fields = ["status", "department"]
    search_fields = ["title", "description"]
    date_field = "opened_on"
    ordering_fields = ["opened_on", "title", "status"]
    action_permission_map = {"hold": "hr.change_jobopening", "reopen": "hr.change_jobopening", "close": "hr.change_jobopening"}

    def _answer(self, opening):
        return Response(self.get_serializer(self.get_queryset().get(pk=opening.pk)).data)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        opening = self.get_object()
        _run(opening.hold)
        return self._answer(opening)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        opening = self.get_object()
        _run(opening.reopen)
        return self._answer(opening)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        opening = self.get_object()
        _run(opening.close, on_date=request.data.get("on_date"))
        return self._answer(opening)


class ApplicantViewSet(AuditableViewSetMixin, viewsets.ModelViewSet):
    queryset = Applicant.objects.select_related("opening", "employee")
    serializer_class = ApplicantSerializer
    filter_fields = ["opening", "stage", "source"]
    search_fields = ["name", "phone", "email", "opening__title"]
    date_field = "applied_on"
    ordering_fields = ["applied_on", "name", "stage"]
    action_permission_map = {"advance": "hr.change_applicant", "reject": "hr.change_applicant", "hire": "hr.add_employee"}

    def _answer(self, applicant):
        return Response(self.get_serializer(self.get_queryset().get(pk=applicant.pk)).data)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        applicant = self.get_object()
        _run(applicant.advance, str(request.data.get("stage", "")))
        return self._answer(applicant)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        applicant = self.get_object()
        _run(applicant.reject, str(request.data.get("reason", "")))
        return self._answer(applicant)

    @action(detail=True, methods=["post"])
    def hire(self, request, pk=None):
        """{employee_number, hire_date?, job_title?}: the employee record made, the applicant hired."""
        applicant = self.get_object()
        employee = _run(applicant.hire, str(request.data.get("employee_number", "")),
                        hire_date=request.data.get("hire_date"), job_title=str(request.data.get("job_title", "")))
        data = self.get_serializer(self.get_queryset().get(pk=applicant.pk)).data
        data["employee"] = employee.pk
        return Response(data)
