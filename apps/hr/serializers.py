from rest_framework import serializers

from .models import Department, Employee, LeavePolicy, LeaveRequest


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "manager",
            "cost_centre",
        ]


class EmployeeSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "party",
            "employee_number",
            "department",
            "manager",
            "job_title",
            "hire_date",
            "termination_date",
            "employment_status",
            "working_days", "holiday_region", "user", "name",
        ]


class LeavePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LeavePolicy
        fields = ["id", "code", "name", "leave_type", "annual_days"]


class LeaveRequestSerializer(serializers.ModelSerializer):
    # Names, so a list says whose and who decided without asking again.
    employee_name = serializers.CharField(source="employee.party.name", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.party.name", read_only=True, default="")
    policy_name = serializers.CharField(source="policy.name", read_only=True, default="")

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            # The allowance it draws on, and half a day. Missing before
            # review, and DRF drops a field it does not know without a
            # word: every request made over the API drew on nothing, and
            # nobody's balance ever went down.
            "policy",
            "leave_type",
            "start_date",
            "end_date",
            "half_day",
            "days_taken",
            "status",
            "reason",
            "decided_by",
            "decided_at",
            "employee_name", "decided_by_name", "policy_name",
        ]
        read_only_fields = ["status", "decided_by", "decided_at", "days_taken"]
