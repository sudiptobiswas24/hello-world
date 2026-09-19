from rest_framework import serializers

from .models import Department, Employee, LeaveRequest


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "manager"]


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
        ]


class LeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "leave_type",
            "start_date",
            "end_date",
            "status",
            "reason",
            "decided_by",
            "decided_at",
        ]
        read_only_fields = ["status", "decided_by", "decided_at"]
