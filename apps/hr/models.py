from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import AuditModel, Party, PartyRole


def _require_employee_role(party):
    if party and not party.role_assignments.filter(role=PartyRole.EMPLOYEE).exists():
        raise ValidationError(f"{party} does not have the Employee role.")


class EmploymentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ON_LEAVE = "on_leave", "On Leave"
    TERMINATED = "terminated", "Terminated"


class Department(AuditModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    manager = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_departments"
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Employee(AuditModel):
    """
    An Employee is always backed by a core.Party with the EMPLOYEE role —
    HR doesn't invent its own idea of "a person" any more than Sales
    invents its own idea of "a customer".
    """

    party = models.OneToOneField(Party, on_delete=models.PROTECT, related_name="employee_profile")
    employee_number = models.CharField(max_length=32, unique=True)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees"
    )
    manager = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="direct_reports"
    )
    job_title = models.CharField(max_length=255, blank=True)
    hire_date = models.DateField()
    termination_date = models.DateField(null=True, blank=True)
    employment_status = models.CharField(
        max_length=16, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE
    )

    class Meta:
        ordering = ["employee_number"]

    def __str__(self):
        return f"{self.employee_number} - {self.party.name}"

    def clean(self):
        _require_employee_role(self.party)
        if self.manager_id and self.manager_id == self.pk:
            raise ValidationError("An employee cannot be their own manager.")
        if self.termination_date and self.employment_status != EmploymentStatus.TERMINATED:
            raise ValidationError(
                "employment_status must be 'terminated' when a termination_date is set."
            )


class LeaveType(models.TextChoices):
    VACATION = "vacation", "Vacation"
    SICK = "sick", "Sick"
    UNPAID = "unpaid", "Unpaid"
    OTHER = "other", "Other"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveRequest(AuditModel):
    employee = models.ForeignKey(Employee, related_name="leave_requests", on_delete=models.CASCADE)
    leave_type = models.CharField(max_length=16, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=LeaveStatus.choices, default=LeaveStatus.PENDING)
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        permissions = [("decide_leaverequest", "Can approve or reject leave requests")]

    def __str__(self):
        return f"{self.employee} {self.leave_type} {self.start_date}..{self.end_date} [{self.status}]"

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("end_date cannot be before start_date.")

    def approve(self, by):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be approved.")
        self.status = LeaveStatus.APPROVED
        self.decided_by = by
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])

    def reject(self, by, reason=None):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be rejected.")
        self.status = LeaveStatus.REJECTED
        self.decided_by = by
        self.decided_at = timezone.now()
        if reason:
            self.reason = reason
        self.save(update_fields=["status", "decided_by", "decided_at", "reason", "updated_at"])

    def cancel(self):
        if self.status != LeaveStatus.PENDING:
            raise ValidationError("Only a pending leave request can be cancelled.")
        self.status = LeaveStatus.CANCELLED
        self.save(update_fields=["status", "updated_at"])
