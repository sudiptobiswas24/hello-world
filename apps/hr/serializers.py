from django.db import transaction
from rest_framework import serializers

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import Department, Employee, LeavePolicy, LeaveRequest


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "manager",
            "cost_centre", "centre",
        ]


class EmployeeSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="party.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    # HR makes the person with the employee: whoever keeps employees need
    # not also keep customers and vendors to take somebody on.
    new_name = serializers.CharField(write_only=True, required=False, max_length=255)

    def validate(self, attrs):
        if self.instance is None and not attrs.get("party") and not attrs.get("new_name"):
            raise serializers.ValidationError({"party": ["Name the person, or choose who they already are."]})
        return attrs

    def create(self, validated_data):
        name = validated_data.pop("new_name", "")
        with transaction.atomic():
            if not validated_data.get("party"):
                person = Party.objects.create(code=validated_data["employee_number"], name=name)
                PartyRoleAssignment.objects.create(party=person, role=PartyRole.EMPLOYEE)
                validated_data["party"] = person
            return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("new_name", None)
        return super().update(instance, validated_data)

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
            "working_days", "holiday_region", "paid_by_attendance", "uan", "esi_number", "user", "name",
            "department_name", "new_name",
        ]
        extra_kwargs = {"party": {"required": False}}


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
