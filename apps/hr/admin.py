from django.contrib import admin

from apps.core.audit import AuditableAdminMixin

from .models import Department, Employee, LeaveRequest


@admin.register(Department)
class DepartmentAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("code", "name", "manager")


@admin.register(Employee)
class EmployeeAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = (
        "employee_number",
        "party",
        "department",
        "manager",
        "job_title",
        "employment_status",
    )
    list_filter = ("employment_status", "department")
    search_fields = ("employee_number", "party__name", "job_title")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(AuditableAdminMixin, admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "status", "decided_by")
    list_filter = ("status", "leave_type")
    readonly_fields = ("status", "decided_by", "decided_at")
