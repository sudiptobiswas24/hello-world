from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import Party, PartyRole, PartyRoleAssignment

from .models import Department, Employee, EmploymentStatus, LeaveRequest, LeaveStatus, LeaveType


def make_employee_party(code, name):
    party = Party.objects.create(code=code, name=name)
    PartyRoleAssignment.objects.create(party=party, role=PartyRole.EMPLOYEE)
    return party


class EmployeeRoleTests(TestCase):
    def test_party_without_employee_role_is_rejected(self):
        customer = Party.objects.create(code="CUST-1", name="Not An Employee")
        employee = Employee(party=customer, employee_number="E001", hire_date="2026-01-01")
        with self.assertRaises(ValidationError):
            employee.full_clean()

    def test_party_with_employee_role_is_accepted(self):
        party = make_employee_party("EMP-1", "Jane Doe")
        employee = Employee(party=party, employee_number="E001", hire_date="2026-01-01")
        employee.full_clean()  # should not raise


class EmployeeConsistencyTests(TestCase):
    def test_cannot_be_own_manager(self):
        party = make_employee_party("EMP-2", "Sam Lee")
        employee = Employee.objects.create(party=party, employee_number="E002", hire_date="2026-01-01")
        employee.manager = employee
        with self.assertRaises(ValidationError):
            employee.full_clean()

    def test_termination_date_requires_terminated_status(self):
        party = make_employee_party("EMP-3", "Alex Kim")
        employee = Employee(
            party=party,
            employee_number="E003",
            hire_date="2026-01-01",
            termination_date="2026-06-01",
            employment_status=EmploymentStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError):
            employee.full_clean()

    def test_department_manager_can_reference_employee(self):
        party = make_employee_party("EMP-4", "Morgan Diaz")
        manager = Employee.objects.create(party=party, employee_number="E004", hire_date="2026-01-01")
        dept = Department.objects.create(code="ENG", name="Engineering", manager=manager)
        self.assertEqual(dept.manager, manager)


class LeaveRequestTests(TestCase):
    def setUp(self):
        employee_party = make_employee_party("EMP-5", "Riley Chen")
        self.employee = Employee.objects.create(
            party=employee_party, employee_number="E005", hire_date="2026-01-01"
        )
        manager_party = make_employee_party("EMP-6", "Jordan Park")
        self.manager = Employee.objects.create(
            party=manager_party, employee_number="E006", hire_date="2025-01-01"
        )

    def make_request(self):
        return LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=LeaveType.VACATION,
            start_date="2026-02-01",
            end_date="2026-02-05",
        )

    def test_end_date_before_start_date_is_rejected(self):
        request = LeaveRequest(
            employee=self.employee,
            leave_type=LeaveType.VACATION,
            start_date="2026-02-05",
            end_date="2026-02-01",
        )
        with self.assertRaises(ValidationError):
            request.full_clean()

    def test_approve_sets_status_and_decider(self):
        request = self.make_request()
        request.approve(by=self.manager)
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.APPROVED)
        self.assertEqual(request.decided_by, self.manager)
        self.assertIsNotNone(request.decided_at)

    def test_reject_sets_status_and_reason(self):
        request = self.make_request()
        request.reject(by=self.manager, reason="Team is short-staffed that week")
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.REJECTED)
        self.assertEqual(request.reason, "Team is short-staffed that week")

    def test_cannot_approve_a_decided_request_again(self):
        request = self.make_request()
        request.approve(by=self.manager)
        with self.assertRaises(ValidationError):
            request.approve(by=self.manager)

    def test_cancel_only_allowed_while_pending(self):
        request = self.make_request()
        request.approve(by=self.manager)
        with self.assertRaises(ValidationError):
            request.cancel()

    def test_pending_request_can_be_cancelled(self):
        request = self.make_request()
        request.cancel()
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveStatus.CANCELLED)
