from rest_framework import serializers

from .models import Department, Employee, LeaveRequest


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "manager",
            "cost_centre",
        ]


class EmployeeSerializer(serializers.ModelSerializer):
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
            "working_days", "holiday_region",
        ]


class LeaveRequestSerializer(serializers.ModelSerializer):
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
        ]
        read_only_fields = ["status", "decided_by", "decided_at", "days_taken"]
